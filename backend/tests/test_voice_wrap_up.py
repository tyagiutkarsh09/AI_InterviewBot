"""Voice WRAP_UP outro: invite -> bounded candidate Q&A -> sign-off -> evaluate.

WHY: The voice interview ended abruptly (COMPLETION_MESSAGE then immediate eval).
A proper close invites candidate questions, answers them ONLY from job context with
a deterministic cap, then signs off and triggers evaluation. Routing (advance vs
answer) is deterministic — no LLM decides it.
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch
import pytest

from src.services.audio.voice_session import (
    create_voice_session, get_voice_session, set_voice_field,
)
from src.services.interview import voice_llm_orchestrator as orch
from src.types.interview import Question, QuestionType


def _seed_one_question_session(sid):
    q = Question(
        id="q0", topic="apis", difficulty="easy", question_type=QuestionType.CONCEPTUAL,
        experience_level="junior", question_text="What is REST?", rubric={"criteria": ["x"]},
    )
    create_voice_session(
        session_id=sid, candidate_name="Alex", job_role="Backend",
        experience_level="junior", required_skills=["python"],
        questions_json=json.dumps([q.model_dump()]),
        intro_text="Hi.", ease_in_text="Ready.", jd_summary_json=json.dumps({"skills": ["python"]}),
    )


@pytest.mark.asyncio
async def test_last_answer_enters_wrap_up_not_eval(monkeypatch):
    _seed_one_question_session("w1")

    async def _fake_eval(_sid):  # must not be called yet
        raise AssertionError("evaluation triggered too early")
    monkeypatch.setattr(orch, "_trigger_final_evaluation", _fake_eval)

    # Force the LLM parse to 'transition' so the single question is consumed.
    class _Parsed:
        action = "transition"; spoken_text = "Thanks."; score = None
        score_topic = None; confidence = None
    with patch.object(orch, "parse_xml_response", return_value=_Parsed()), \
         patch.object(orch, "get_async_anthropic_client") as client:
        fake_response = type("R", (), {"content": [type("C", (), {"text": "<x/>"})()]})()
        client.return_value.messages.create = AsyncMock(return_value=fake_response)
        reply = await orch.run_llm_turn("w1", "REST is an architectural style.")

    assert "?" in reply                              # invites candidate questions
    sess = get_voice_session("w1")
    assert sess["interview_phase"] == "wrap_up"
    assert int(sess["outro_questions_used"]) == 0


@pytest.mark.asyncio
async def test_wrap_up_answers_then_signs_off(monkeypatch):
    _seed_one_question_session("w2")
    set_voice_field("w2", "interview_phase", "wrap_up")
    set_voice_field("w2", "outro_questions_used", 0)

    with patch.object(orch, "answer_candidate_question", return_value="It's a backend role."):
        reply1 = await orch.run_llm_turn("w2", "What does the team work on?")
    assert "backend role" in reply1
    assert int(get_voice_session("w2")["outro_questions_used"]) == 1

    evaluated = {}
    async def _fake_eval(sid):
        evaluated["sid"] = sid
    monkeypatch.setattr(orch, "_trigger_final_evaluation", _fake_eval)

    reply2 = await orch.run_llm_turn("w2", "No, I'm good, thanks.")
    await asyncio.sleep(0)  # let the fire-and-forget eval task run
    assert "thank you" in reply2.lower()             # deterministic sign-off
    assert evaluated.get("sid") == "w2"              # evaluation now triggered


@pytest.mark.asyncio
async def test_wrap_up_caps_questions(monkeypatch):
    _seed_one_question_session("w3")
    set_voice_field("w3", "interview_phase", "wrap_up")
    set_voice_field("w3", "outro_questions_used", orch.MAX_OUTRO_QUESTIONS)

    evaluated = {}
    async def _fake_eval(sid):
        evaluated["sid"] = sid
    monkeypatch.setattr(orch, "_trigger_final_evaluation", _fake_eval)

    reply = await orch.run_llm_turn("w3", "One more question — what's the stack?")
    await asyncio.sleep(0)  # let the fire-and-forget eval task run
    assert "thank you" in reply.lower()              # cap reached -> sign-off, no answer
    assert evaluated.get("sid") == "w3"


def test_is_no_questions_true_for_plain_declines():
    assert orch._is_no_questions("no")
    assert orch._is_no_questions("Nope.")
    assert orch._is_no_questions("No, I'm good, thanks.")
    assert orch._is_no_questions("nothing else")
    assert orch._is_no_questions("I'm good")


def test_is_no_questions_false_when_a_real_question_follows():
    assert not orch._is_no_questions("No worries, but I do have a question about the stack")
    assert not orch._is_no_questions("Nothing comes to mind, but I am wondering about remote work")
    assert not orch._is_no_questions("Actually, what's the team size?")
