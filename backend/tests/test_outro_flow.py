"""WRAP_UP outro candidate Q&A.

WHY: The outro must answer ONLY from JD/config context, fall back to a recruiter-
clarify line when the LLM can't answer, cap the number of candidate questions
deterministically, and never score these turns.
"""
from unittest.mock import MagicMock, patch

import pytest

from src.services.interview.outro import answer_candidate_question, RECRUITER_FALLBACK


def _mock_response(text: str):
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    return resp


def test_answer_uses_jd_context():
    client = MagicMock()
    client.messages.create.return_value = _mock_response("The role focuses on backend APIs.")
    with patch("src.services.interview.outro.get_anthropic_client", return_value=client):
        ans = answer_candidate_question(
            question="What does the role focus on?",
            job_role="backend engineer",
            jd_summary={"responsibilities": ["build APIs"], "skills": ["python"]},
        )
    assert "backend" in ans.lower()


def test_answer_falls_back_on_llm_error():
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("down")
    with patch("src.services.interview.outro.get_anthropic_client", return_value=client):
        ans = answer_candidate_question(
            question="What's the salary?", job_role="backend engineer", jd_summary={},
        )
    assert ans == RECRUITER_FALLBACK


from typing import Any
from unittest.mock import AsyncMock

from src.types.api import SubmitAnswerRequest, SubmitAnswerResponse
from src.services.interview.outro import MAX_OUTRO_QUESTIONS
from src.types.config import InterviewConfig, InterviewPlan, JDSummary
from src.types.interview import (
    ExperienceLevel, InterviewState, Question, QuestionType, SessionState,
)

_S: dict[str, Any] = {}
def _sj(k, v, ttl=0): _S[k] = v
def _gj(k): return _S.get(k)


@pytest.fixture(autouse=True)
def _reset():
    _S.clear()
    yield
    _S.clear()


def _seed_wrapup_session() -> str:
    q = Question(id="a", topic="t", difficulty="medium",
                 question_type=QuestionType.CONCEPTUAL, experience_level="mid",
                 question_text="Q", rubric={"criteria": []})
    s = SessionState(
        session_id="sid", state=InterviewState.WRAP_UP, candidate_name="Alice",
        job_role="backend engineer", experience_level=ExperienceLevel.MID,
        questions=[q], current_question_idx=0, interview_config_id="cfg-1",
        jd_summary={"skills": ["python"]}, outro_questions_used=0,
    )
    _S["session:sid"] = s.model_dump()
    return "sid"


@pytest.mark.asyncio
async def test_wrapup_answer_is_unscored_and_stays_in_wrapup():
    from src.routes.interview import submit_answer
    sid = _seed_wrapup_session()
    with (
        # Patch the *used* bindings in session_manager (it binds set_json/get_json at
        # import via `from redis_client import ...`); a source-only patch would miss it
        # since another test module imports session_manager at collection time.
        patch("src.services.interview.session_manager.set_json", side_effect=_sj),
        patch("src.services.interview.session_manager.get_json", side_effect=_gj),
        # Patch where the name is *used* — interview.py binds it at import via
        # `from ...outro import answer_candidate_question`, so patching the outro
        # module attribute would not affect the route's already-bound reference.
        patch("src.routes.interview.answer_candidate_question", return_value="Sure — it's backend focused."),
    ):
        resp = await submit_answer(SubmitAnswerRequest(session_id=sid, answer="What's the team size?"))
    assert resp.state == InterviewState.WRAP_UP
    assert resp.score is None


@pytest.mark.asyncio
async def test_wrapup_cap_advances_to_evaluation():
    from src.routes.interview import submit_answer
    sid = _seed_wrapup_session()
    s = SessionState(**_S["session:sid"])
    s.outro_questions_used = MAX_OUTRO_QUESTIONS
    _S["session:sid"] = s.model_dump()

    with (
        patch("src.services.interview.session_manager.set_json", side_effect=_sj),
        patch("src.services.interview.session_manager.get_json", side_effect=_gj),
        patch("src.routes.interview._finalize_and_report",
              new=AsyncMock(return_value=SubmitAnswerResponse(
                  session_id=sid, state=InterviewState.COMPLETE, is_complete=True, feedback="done"))),
    ):
        resp = await submit_answer(SubmitAnswerRequest(session_id=sid, answer="one more?"))
    assert resp.state == InterviewState.COMPLETE
    assert resp.is_complete is True
