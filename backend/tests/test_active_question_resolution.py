"""Tests for active-question resolution in the text interview path.

These encode the invariant the product requires: the bot must not advance to a
new assessment question until the current active question is closed. A follow-up
(rephrase / clarification) keeps the SAME active question, and the candidate's
next answer must be evaluated against that same question — not a newly
introduced one.

Root cause being guarded against: turn_manager.process_answer previously
advanced current_question_idx unconditionally, ignoring the LLM's `action`. A
vague answer that should have triggered a follow-up instead silently moved the
active question forward, so the candidate's next answer was scored against a
question they were never clearly asked.
"""

import types
from unittest.mock import AsyncMock, patch

import pytest

from src.services.interview import session_manager, turn_manager
from src.services.llm import llm_service
from src.types.interview import (
    ExperienceLevel,
    InterviewState,
    Question,
    QuestionType,
    SessionState,
)


def _make_question(qid: str, topic: str) -> Question:
    return Question(
        id=qid,
        topic=topic,
        difficulty="medium",
        question_type=QuestionType.CONCEPTUAL,
        experience_level="mid",
        question_text=f"What is {topic}?",
        rubric={"criteria": []},
    )


def _make_session(num_questions: int = 2, current_idx: int = 0) -> SessionState:
    questions = [_make_question(f"q{i}", f"topic{i}") for i in range(num_questions)]
    return SessionState(
        session_id="s-test",
        state=InterviewState.QUESTIONING,
        candidate_name="Alice",
        job_role="backend",
        experience_level=ExperienceLevel.MID,
        required_skills=["python"],
        questions=questions,
        current_question_idx=current_idx,
    )


def _llm_result(action, spoken_text, score=None, reasoning=None):
    """Stand-in for the LLM evaluation result. The LLM call is a network
    boundary, so it is unavoidable to substitute it here; turn_manager only
    reads attributes off this object."""
    return types.SimpleNamespace(
        action=action,
        spoken_text=spoken_text,
        score=score,
        reasoning=reasoning,
        confidence=None,
        flags=[],
    )


@pytest.fixture(autouse=True)
def no_redis():
    """session_manager mutates the in-memory SessionState in place; skip the
    Redis write so these stay pure unit tests."""
    with patch.object(session_manager, "_persist", lambda session: None):
        yield


@pytest.mark.asyncio
async def test_follow_up_does_not_advance_active_question():
    """A follow-up must keep the SAME active question. If the index advances,
    the candidate's next answer gets scored against a question they were never
    clearly asked — the exact production bug."""
    session = _make_session(num_questions=2, current_idx=0)
    stub = _llm_result("follow_up", "Could you be more specific about async?")

    with patch.object(llm_service, "evaluate_answer", new=AsyncMock(return_value=stub)):
        result = await turn_manager.process_answer(session, "um, doing things later?")

    assert session.current_question_idx == 0, \
        "Follow-up must not advance the active question"
    assert result.state == InterviewState.QUESTIONING
    assert result.topic == "topic0", \
        "Active question/topic must stay unchanged on a follow-up"


@pytest.mark.asyncio
async def test_follow_up_surfaces_follow_up_text_as_next_question():
    """The candidate must SEE the follow-up. The text route returns
    result.next_question to the frontend, so the follow-up text must travel
    there — and it concerns the same active question."""
    session = _make_session(num_questions=2, current_idx=0)
    fu = "Could you be more specific about async?"
    stub = _llm_result("follow_up", fu)

    with patch.object(llm_service, "evaluate_answer", new=AsyncMock(return_value=stub)):
        result = await turn_manager.process_answer(session, "vague")

    assert result.next_question == fu
    assert result.question_number == 1, \
        "Still on question 1 (1-indexed) during a follow-up"


@pytest.mark.asyncio
async def test_follow_up_does_not_record_final_question_result():
    """An unresolved question must not produce a final per-question result;
    otherwise one question yields multiple QuestionResults and corrupts the
    final score aggregation."""
    session = _make_session(num_questions=2, current_idx=0)
    stub = _llm_result("follow_up", "Say more?")

    with patch.object(llm_service, "evaluate_answer", new=AsyncMock(return_value=stub)):
        await turn_manager.process_answer(session, "vague")

    assert len(session.question_results) == 0, \
        "A follow-up turn must not finalize a QuestionResult for the open question"


@pytest.mark.asyncio
async def test_acknowledge_advances_to_next_question():
    """Regression guard: a sufficient answer (acknowledge) must still advance,
    record the result, and surface the next question — existing behavior must
    be preserved."""
    session = _make_session(num_questions=2, current_idx=0)
    stub = _llm_result("acknowledge", "Good answer.", score=8.0, reasoning="solid")

    with patch.object(llm_service, "evaluate_answer", new=AsyncMock(return_value=stub)):
        result = await turn_manager.process_answer(session, "thorough answer")

    assert session.current_question_idx == 1, "Acknowledge must advance the active question"
    assert result.next_question == "What is topic1?"
    assert result.topic == "topic1"
    assert len(session.question_results) == 1, "A closed question records exactly one result"
    assert session.question_results[0].topic == "topic0"


@pytest.mark.asyncio
async def test_follow_up_on_last_question_stays_questioning():
    """A follow-up on the final question must NOT jump to EVALUATING. The bot
    must finish resolving the active question before the interview can end."""
    session = _make_session(num_questions=2, current_idx=1)  # last question
    stub = _llm_result("follow_up", "Can you elaborate?")

    with patch.object(llm_service, "evaluate_answer", new=AsyncMock(return_value=stub)):
        result = await turn_manager.process_answer(session, "vague")

    assert result.state == InterviewState.QUESTIONING
    assert session.current_question_idx == 1
    assert session.state != InterviewState.EVALUATING


@pytest.mark.asyncio
async def test_follow_up_capped_then_force_advances():
    """Follow-ups must be bounded. After MAX_FOLLOW_UPS the bot stops looping on
    the same question and advances, even if the LLM asks for another follow-up —
    otherwise a confused candidate can be trapped on one question forever."""
    session = _make_session(num_questions=2, current_idx=0)
    session.follow_up_count = turn_manager.MAX_FOLLOW_UPS  # cap already reached
    stub = _llm_result("follow_up", "Yet another rephrase?", score=4.0, reasoning="weak")

    with patch.object(llm_service, "evaluate_answer", new=AsyncMock(return_value=stub)):
        await turn_manager.process_answer(session, "still vague")

    assert session.current_question_idx == 1, \
        "At the follow-up cap, the bot must advance instead of following up again"
    assert len(session.question_results) == 1, "A force-closed question records a result"


@pytest.mark.asyncio
async def test_evaluation_result_propagates_action_from_xml():
    """turn_manager can only honor follow-ups if the parsed `action` survives
    into EvaluationResult. Guards the wiring the active-question fix depends on."""
    xml = (
        "<interviewer_response>"
        "<action>follow_up</action>"
        "<spoken_text>Could you elaborate on that?</spoken_text>"
        "<internal_notes>vague</internal_notes>"
        "<confidence>0.4</confidence>"
        "<next_state>questioning</next_state>"
        "<flags></flags>"
        "</interviewer_response>"
    )
    fake_msg = types.SimpleNamespace(content=[types.SimpleNamespace(text=xml)])
    fake_client = types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kwargs: fake_msg)
    )
    session = _make_session()
    question = session.questions[0]

    with patch.object(llm_service, "get_anthropic_client", return_value=fake_client):
        result = await llm_service.evaluate_answer(question, "vague answer", session)

    assert result.action == "follow_up"
