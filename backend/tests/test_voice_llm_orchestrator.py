"""Tests for run_llm_turn — the per-turn LLM step.

These also pin the async-client contract: the orchestrator must `await` an
async Anthropic client (a blocking sync call would freeze the event loop).

New tests (prefixed with test_b_prime_*) cover the B′ free-form conversation
model: each must FAIL if the corresponding clamp or behavior regresses.
"""

import json

import pytest

from tests.conftest import seed_voice_session, make_question

from src.services.audio.voice_session import get_voice_session, set_voice_field
from src.services.interview import voice_llm_orchestrator
from src.services.interview.voice_llm_orchestrator import run_llm_turn, LOOP_GUARD_MAX


# ---------------------------------------------------------------------------
# LLM plumbing helpers
# ---------------------------------------------------------------------------

class _Content:
    def __init__(self, text: str) -> None:
        self.text = text


class _Response:
    def __init__(self, text: str) -> None:
        self.content = [_Content(text)]


class _Messages:
    def __init__(self, text: str) -> None:
        self._text = text

    async def create(self, **_: object) -> _Response:
        return _Response(self._text)


class FakeAsyncAnthropic:
    def __init__(self, text: str) -> None:
        self.messages = _Messages(text)


def _patch_llm(monkeypatch, xml: str) -> None:
    monkeypatch.setattr(
        voice_llm_orchestrator,
        "get_async_anthropic_client",
        lambda: FakeAsyncAnthropic(xml),
    )


def _xml(action: str, spoken: str, topic: str = "", score: str = "",
         confidence: str = "") -> str:
    """Build a minimal <interviewer_response> XML for the given parameters."""
    score_block = ""
    if score:
        score_block = (
            f"  <score_update>"
            f"<topic>{topic}</topic>"
            f"<score>{score}</score>"
            f"<reasoning>test</reasoning>"
            f"</score_update>\n"
        )
    conf_block = f"  <confidence>{confidence}</confidence>\n" if confidence else ""
    return (
        "<interviewer_response>\n"
        f"  <action>{action}</action>\n"
        f"  <spoken_text>{spoken}</spoken_text>\n"
        f"  <internal_notes>test</internal_notes>\n"
        f"{conf_block}"
        f"{score_block}"
        "  <next_state>questioning</next_state>\n"
        "  <flags></flags>\n"
        "</interviewer_response>"
    )


# ---------------------------------------------------------------------------
# Legacy XML fixtures (kept for backward-compat with existing tests)
# ---------------------------------------------------------------------------

ACKNOWLEDGE_XML = """
<interviewer_response>
  <action>acknowledge</action>
  <spoken_text>Thanks, that's clear.</spoken_text>
  <internal_notes>solid answer</internal_notes>
  <score_update><topic>python</topic><score>8</score><reasoning>good</reasoning></score_update>
  <next_state>questioning</next_state>
  <flags></flags>
</interviewer_response>
"""

FOLLOW_UP_XML = """
<interviewer_response>
  <action>follow_up</action>
  <spoken_text>Can you give a concrete example?</spoken_text>
  <internal_notes>needs depth</internal_notes>
  <next_state>questioning</next_state>
  <flags></flags>
</interviewer_response>
"""


# ===========================================================================
# EXISTING TESTS — must continue to pass
# ===========================================================================

@pytest.mark.asyncio
async def test_acknowledge_advances_to_next_question_and_records_score(monkeypatch):
    seed_voice_session(
        "s-ack",
        [make_question("q1", "python"), make_question("q2", "databases")],
    )
    _patch_llm(monkeypatch, ACKNOWLEDGE_XML)

    spoken = await run_llm_turn("s-ack", "I used decorators for caching.")

    session = get_voice_session("s-ack")
    assert int(session["current_question_idx"]) == 1
    assert json.loads(session["running_scores"]) == {"python": 8.0}
    assert "Thanks, that's clear." in spoken
    assert "databases" in spoken  # next question text appended


# spoken_text that already contains a question — the model "asking two questions
# at once" originates here: the orchestrator then appends the canonical bank
# question on top of this, so the candidate hears both.
QUESTION_IN_SPOKEN_XML = """
<interviewer_response>
  <action>acknowledge</action>
  <spoken_text>Good answer. So how would you scale that database?</spoken_text>
  <internal_notes>ok</internal_notes>
  <score_update><topic>python</topic><score>7</score><reasoning>ok</reasoning></score_update>
  <next_state>questioning</next_state>
  <flags></flags>
</interviewer_response>
"""


@pytest.mark.asyncio
async def test_transition_strips_question_from_spoken_text(monkeypatch):
    """When the model's spoken_text already contains a question, only the
    canonical next question may reach the candidate — never both.

    This is the "model asks two questions at once" bug: spoken_text's question
    must be dropped, the acknowledgment kept, and the bank question appended.
    """
    seed_voice_session(
        "s-two-q",
        [make_question("q1", "python"), make_question("q2", "databases")],
    )
    _patch_llm(monkeypatch, QUESTION_IN_SPOKEN_XML)

    spoken = await run_llm_turn("s-two-q", "I used caching.")

    assert "Tell me about databases." in spoken     # canonical question kept
    assert "scale that database" not in spoken       # model's extra question dropped
    assert "Good answer." in spoken                  # acknowledgment preserved
    assert spoken.count("?") == 0                    # no leftover question


@pytest.mark.asyncio
async def test_exactly_one_question_when_bank_question_is_interrogative(monkeypatch):
    """With an interrogative bank question, the candidate must hear exactly one
    '?' — the bank question — even when the model added its own question."""
    from src.types.interview import Question, QuestionType

    q1 = make_question("q1", "python")
    q2 = Question(
        id="q2",
        topic="databases",
        difficulty="medium",
        question_type=QuestionType.CONCEPTUAL,
        experience_level="mid",
        question_text="How do you design a schema?",
        rubric={"criteria": []},
    )
    seed_voice_session("s-one-q", [q1, q2])
    _patch_llm(monkeypatch, QUESTION_IN_SPOKEN_XML)

    spoken = await run_llm_turn("s-one-q", "I used caching.")

    assert spoken.count("?") == 1
    assert "How do you design a schema?" in spoken
    assert "scale that database" not in spoken


@pytest.mark.asyncio
async def test_wrap_up_lead_in_strips_model_question(monkeypatch):
    """Entering wrap-up after the last question, the model's spoken_text question
    must be stripped so only the wrap-up invite is asked."""
    seed_voice_session("s-wrap", [make_question("q1", "python")])
    _patch_llm(monkeypatch, QUESTION_IN_SPOKEN_XML)

    spoken = await run_llm_turn("s-wrap", "I used caching.")

    assert spoken.count("?") == 1                    # only the wrap-up invite
    assert "scale that database" not in spoken        # model's question dropped


COMPOUND_FOLLOW_UP_XML = """
<interviewer_response>
  <action>follow_up</action>
  <spoken_text>What's your experience with indexing? And how do you handle migrations?</spoken_text>
  <internal_notes>needs depth</internal_notes>
  <next_state>questioning</next_state>
  <flags></flags>
</interviewer_response>
"""


@pytest.mark.asyncio
async def test_compound_follow_up_reduced_to_single_question(monkeypatch):
    """A follow-up whose spoken_text packs two questions must be reduced to one
    before it reaches the candidate (and the transcript)."""
    seed_voice_session(
        "s-compound-fu",
        [make_question("q1", "python"), make_question("q2", "databases")],
    )
    _patch_llm(monkeypatch, COMPOUND_FOLLOW_UP_XML)

    spoken = await run_llm_turn("s-compound-fu", "I used an index once.")

    assert spoken == "What's your experience with indexing?"
    assert spoken.count("?") == 1
    assert "migrations" not in spoken

    # The persisted follow-up turn must also be the single-question form.
    session = get_voice_session("s-compound-fu")
    transcript = json.loads(session["transcript"])
    last_bot = [t for t in transcript if t["speaker"] == "bot"][-1]
    assert last_bot["text"] == "What's your experience with indexing?"


@pytest.mark.asyncio
async def test_follow_up_stays_on_question_and_increments_count(monkeypatch):
    seed_voice_session(
        "s-fu",
        [make_question("q1", "python"), make_question("q2", "databases")],
    )
    _patch_llm(monkeypatch, FOLLOW_UP_XML)

    spoken = await run_llm_turn("s-fu", "It's faster.")

    session = get_voice_session("s-fu")
    assert int(session["current_question_idx"]) == 0  # unchanged
    assert int(session["follow_up_count"]) == 1
    assert spoken == "Can you give a concrete example?"


# ===========================================================================
# NEW TESTS — B′ conversation model invariants
# ===========================================================================


# ---------------------------------------------------------------------------
# Test 1: acknowledge_advance scores the current question's topic exactly once
#         and advances the index by exactly 1.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_b_prime_acknowledge_advance_scores_once_and_advances_by_one(monkeypatch):
    """acknowledge_advance must record the score keyed by the CURRENT question's
    topic (not whatever the model puts in <topic>) and move to the next question.

    This test fails if: score is not recorded, score is keyed by wrong topic,
    index advances by more than 1, or index does not advance at all.
    """
    questions = [make_question("q1", "python"), make_question("q2", "sql")]
    seed_voice_session("s-b1", questions)
    # Note: XML says topic="wrong_topic" — orchestrator must key by current_q.topic.
    xml = _xml("acknowledge_advance", "That's great.", topic="wrong_topic", score="9",
               confidence="0.9")
    _patch_llm(monkeypatch, xml)

    await run_llm_turn("s-b1", "Decorators let you wrap functions.")

    session = get_voice_session("s-b1")
    assert int(session["current_question_idx"]) == 1, "index must advance by exactly 1"
    scores = json.loads(session["running_scores"])
    assert "python" in scores, "score must be keyed by current question topic 'python'"
    assert scores["python"] == 9.0, "score value must match XML"
    assert "wrong_topic" not in scores, "must NOT use the model's topic label"


# ---------------------------------------------------------------------------
# Test 2: non-advancing actions (follow_up, answer_clarification,
#         accept_thinking, redirect) do NOT advance index or record a score.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("action,spoken,entry_type", [
    ("follow_up", "Can you elaborate on that?", "follow_up"),
    ("answer_clarification", "Sure! By closure I mean a function that captures its scope. Does that help clarify?", "clarification"),
    ("accept_thinking", "Of course, take your time.", "accept_thinking"),
    ("redirect", "Let's come back to the original question.", "redirect"),
])
async def test_b_prime_non_advancing_actions_do_not_score_or_advance(
    monkeypatch, action, spoken, entry_type
):
    """Non-advancing actions must leave current_question_idx and running_scores
    unchanged.  Failure means the bot skipped the current question or scored
    prematurely.
    """
    sid = f"s-b2-{action}"
    seed_voice_session(sid, [make_question("q1", "python"), make_question("q2", "sql")])
    _patch_llm(monkeypatch, _xml(action, spoken))

    await run_llm_turn(sid, "Hmm, let me think...")

    session = get_voice_session(sid)
    assert int(session["current_question_idx"]) == 0, (
        f"{action} must not advance the question index"
    )
    scores = json.loads(session["running_scores"])
    assert scores == {}, (
        f"{action} must not record any score, got {scores}"
    )


# ---------------------------------------------------------------------------
# Test 3: CORE BUG REGRESSION — answer_clarification keeps the '?' intact.
#
# Previously _acknowledgment_only was applied to ALL spoken_text, which
# stripped any sentence containing '?'.  When the bot answers a clarifying
# question and re-poses the original question, the candidate would hear only
# the first sentence ("Sure!") and never hear the question re-stated.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_b_prime_answer_clarification_preserves_question_mark(monkeypatch):
    """answer_clarification spoken_text must be returned VERBATIM even when it
    contains a question mark.

    This is the defining cross-questioning fix: the bot is ANSWERING the
    candidate's question, so stripping '?' from its reply is wrong.
    """
    clarification_text = (
        "Sure! By 'closures' I mean functions that capture variables from "
        "their enclosing scope. So with that in mind — can you tell me how "
        "you've used closures in your code?"
    )
    seed_voice_session(
        "s-b3",
        [make_question("q1", "python"), make_question("q2", "sql")],
    )
    _patch_llm(monkeypatch, _xml("answer_clarification", clarification_text))

    spoken = await run_llm_turn("s-b3", "Wait, what do you mean by closures?")

    # The question must survive — this is the core fix.
    assert "can you tell me how" in spoken.lower(), (
        "answer_clarification must not strip sentences containing '?' — "
        f"got: {spoken!r}"
    )
    assert "?" in spoken, (
        "answer_clarification spoken_text must preserve the '?' verbatim"
    )
    # Index must not advance.
    session = get_voice_session("s-b3")
    assert int(session["current_question_idx"]) == 0


# ---------------------------------------------------------------------------
# Test 4 (Clamp 3): follow_up at the cap → force acknowledge_advance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_b_prime_clamp3_follow_up_at_cap_forces_advance(monkeypatch):
    """When follow_up_count == max_follow_ups_for(question), a follow_up action
    from the LLM must be silently converted to acknowledge_advance so the
    interview makes forward progress.

    This prevents the bot from endlessly probing one question.
    """
    questions = [make_question("q1", "python"), make_question("q2", "sql")]
    seed_voice_session("s-b4", questions)
    # For a medium question, MAX_FOLLOW_UPS == 1.  Preset follow_up_count to 1.
    from src.services.interview.voice_llm_orchestrator import max_follow_ups_for
    cap = max_follow_ups_for(questions[0])  # 1 for medium
    set_voice_field("s-b4", "follow_up_count", cap)

    # LLM wants another follow_up — clamp must override to acknowledge_advance.
    _patch_llm(monkeypatch, _xml("follow_up", "Can you give an example?"))

    await run_llm_turn("s-b4", "I think I know decorators.")

    session = get_voice_session("s-b4")
    assert int(session["current_question_idx"]) == 1, (
        "Clamp 3: follow_up at cap must force advance to next question "
        f"(follow_up_count was {cap}, MAX_FOLLOW_UPS={cap})"
    )


# ---------------------------------------------------------------------------
# Test 5 (Clamp 5): loop guard — LOOP_GUARD_MAX consecutive non-advancing
#                   turns forces acknowledge_advance.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_b_prime_clamp5_loop_guard_forces_advance(monkeypatch):
    """When non_advancing_turns reaches LOOP_GUARD_MAX, ANY non-advancing action
    (redirect used here) must be overridden to acknowledge_advance.

    This prevents the bot from looping on one question indefinitely when the
    candidate keeps going off-topic or asking for clarifications.
    """
    questions = [make_question("q1", "python"), make_question("q2", "sql")]
    seed_voice_session("s-b5", questions)
    # Preset non_advancing_turns to exactly LOOP_GUARD_MAX.
    set_voice_field("s-b5", "non_advancing_turns", LOOP_GUARD_MAX)

    # LLM wants to redirect — clamp must override.
    _patch_llm(
        monkeypatch,
        _xml("redirect", "Let's come back to the Python question."),
    )

    await run_llm_turn("s-b5", "Uh, I was thinking about something else.")

    session = get_voice_session("s-b5")
    assert int(session["current_question_idx"]) == 1, (
        f"Clamp 5: with non_advancing_turns={LOOP_GUARD_MAX} a redirect must "
        "force advance to next question"
    )


# ---------------------------------------------------------------------------
# Test 6 (Clamp 2): acknowledge_advance with no <score_update> advances but
#                   records the topic in unscored_topics (never silent).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_b_prime_clamp2_no_score_update_records_unscored_topic(monkeypatch):
    """When acknowledge_advance carries no <score_update>, the orchestrator
    must still advance but MUST record the topic in unscored_topics.

    Failure mode: silent unscored advance — the evaluation pipeline receives
    missing data and either crashes or produces a report with holes.
    """
    questions = [make_question("q1", "python"), make_question("q2", "sql")]
    seed_voice_session("s-b6", questions)
    # No score in XML.
    _patch_llm(monkeypatch, _xml("acknowledge_advance", "Understood, moving on."))

    await run_llm_turn("s-b6", "I'm not sure about that.")

    session = get_voice_session("s-b6")
    # Must still advance.
    assert int(session["current_question_idx"]) == 1, (
        "Clamp 2: must advance even when no score is present"
    )
    # Must record the missing topic.
    unscored = json.loads(session.get("unscored_topics", "[]"))
    assert "python" in unscored, (
        "Clamp 2: unscored advance must be recorded in unscored_topics, "
        f"got {unscored}"
    )


# ---------------------------------------------------------------------------
# Test 7 (Clamp 1): last question + acknowledge_advance → enters wrap_up,
#                   never steps past end of question list.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_b_prime_clamp1_last_question_enters_wrap_up(monkeypatch):
    """When acknowledge_advance fires on the final question, the orchestrator
    must enter the wrap_up phase rather than advancing the index out of bounds.

    Failure mode: IndexError (crashes the turn) or interview gets stuck.
    """
    seed_voice_session("s-b7", [make_question("q1", "python")])
    _patch_llm(
        monkeypatch,
        _xml("acknowledge_advance", "Great answer!", topic="python", score="8",
             confidence="0.85"),
    )

    spoken = await run_llm_turn("s-b7", "Decorators are wrappers.")

    session = get_voice_session("s-b7")
    # interview_phase must be "wrap_up" — never "done" yet, never still "questioning".
    assert session.get("interview_phase") == "wrap_up", (
        f"Clamp 1: last question must enter wrap_up, got phase={session.get('interview_phase')!r}"
    )
    # The wrap-up invite must appear in the spoken response.
    assert "?" in spoken, "Wrap-up invite must contain a question for the candidate"


# ---------------------------------------------------------------------------
# Test 8: accept_thinking sets silence_grace_pending; a subsequent real-answer
#         turn (acknowledge_advance) clears it — leak fix.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_b_prime_accept_thinking_sets_grace_and_advance_clears_it(monkeypatch):
    """accept_thinking must set silence_grace_pending so the silence monitor
    grants extra time.  A subsequent real turn must clear it so the flag
    cannot accumulate across questions.

    Two-part test:
    1. After accept_thinking: silence_grace_pending == "1"
    2. After acknowledge_advance on same question: silence_grace_pending == ""
    """
    questions = [make_question("q1", "python"), make_question("q2", "sql")]
    seed_voice_session("s-b8", questions)

    # Turn 1 — candidate asks for time.
    _patch_llm(monkeypatch, _xml("accept_thinking", "Of course, take your time."))
    await run_llm_turn("s-b8", "Hmm, give me a second.")

    session = get_voice_session("s-b8")
    assert session.get("silence_grace_pending") == "1", (
        "accept_thinking must set silence_grace_pending='1' so the silence "
        "monitor gives extra time"
    )
    assert int(session["current_question_idx"]) == 0, (
        "accept_thinking must not advance the question"
    )

    # Turn 2 — candidate answers; real-utterance path clears the grace flag.
    _patch_llm(
        monkeypatch,
        _xml("acknowledge_advance", "Solid answer.", topic="python", score="7",
             confidence="0.8"),
    )
    await run_llm_turn("s-b8", "Decorators wrap a function to add behaviour.")

    session = get_voice_session("s-b8")
    grace = session.get("silence_grace_pending", "")
    assert grace == "" or grace is None or grace == 0 or str(grace) == "0", (
        "acknowledge_advance must clear silence_grace_pending — "
        f"got {grace!r}"
    )
    assert int(session["current_question_idx"]) == 1, (
        "acknowledge_advance must still advance after clearing the grace flag"
    )
