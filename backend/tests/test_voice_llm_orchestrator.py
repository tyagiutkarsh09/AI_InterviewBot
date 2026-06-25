"""Tests for run_llm_turn — the per-turn LLM step.

These also pin the async-client contract: the orchestrator must `await` an
async Anthropic client (a blocking sync call would freeze the event loop).
"""

import pytest

from tests.conftest import seed_voice_session, make_question

from src.services.audio.voice_session import get_voice_session
from src.services.interview import voice_llm_orchestrator
from src.services.interview.voice_llm_orchestrator import run_llm_turn


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
    import json
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
    import json

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
