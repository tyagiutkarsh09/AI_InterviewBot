"""POST /interview/start-from-config.

WHY: A candidate starts from a frozen config — the session's questions must BE the
config's plan (deterministic), the warmup is personalized from whitelisted resume
fields, and a missing config returns 404.
"""
from typing import Any
from unittest.mock import patch

import pytest

from src.types.api import StartFromConfigRequest
from src.types.config import InterviewConfig, InterviewPlan, JDSummary
from src.types.interview import ExperienceLevel, InterviewState, Question, QuestionType

_STORED: dict[str, Any] = {}


def _set_json(key, value, ttl=0): _STORED[key] = value
def _get_json(key): return _STORED.get(key)


@pytest.fixture(autouse=True)
def reset_store():
    _STORED.clear()
    yield
    _STORED.clear()


def _plan_q(qid: str) -> Question:
    return Question(
        id=qid, topic=f"t_{qid}", difficulty="medium",
        question_type=QuestionType.CONCEPTUAL, experience_level="mid",
        question_text=f"Plan question {qid}", rubric={"criteria": []}, tags=["core"],
    )


def _config() -> InterviewConfig:
    return InterviewConfig(
        id="cfg-1", title="t", role="backend engineer", experience_level=ExperienceLevel.MID,
        job_description="jd", total_questions=6, core_question_ratio=0.8,
        jd_summary=JDSummary(skills=["python"]),
        interview_plan=InterviewPlan(questions=[_plan_q("a"), _plan_q("b")]),
    )


@pytest.mark.asyncio
async def test_start_from_config_uses_frozen_plan():
    from src.routes.interview import start_from_config
    with (
        patch("src.services.interview.session_manager.set_json", side_effect=_set_json),
        patch("src.services.interview.session_manager.get_json", side_effect=_get_json),
        patch("src.routes.interview.get_config", return_value=_config()),
    ):
        req = StartFromConfigRequest(interview_config_id="cfg-1", candidate_name="Alice")
        resp = await start_from_config(req)
    assert resp.state == InterviewState.WARMUP
    assert resp.total_questions == 2  # the frozen plan's question count


@pytest.mark.asyncio
async def test_start_from_config_personalizes_warmup_from_resume():
    from src.routes.interview import start_from_config
    with (
        patch("src.services.interview.session_manager.set_json", side_effect=_set_json),
        patch("src.services.interview.session_manager.get_json", side_effect=_get_json),
        patch("src.routes.interview.get_config", return_value=_config()),
    ):
        req = StartFromConfigRequest(
            interview_config_id="cfg-1", candidate_name="Alice",
            resume_details={"skills": ["Kubernetes"], "current_company": "Acme",
                            "email": "alice@x.com"},
        )
        resp = await start_from_config(req)
    assert "Acme" in resp.question_text
    assert "alice@x.com" not in resp.question_text  # PII never leaks


@pytest.mark.asyncio
async def test_start_from_config_missing_returns_404():
    from fastapi import HTTPException
    from src.routes.interview import start_from_config
    with (
        patch("src.services.interview.session_manager.set_json", side_effect=_set_json),
        patch("src.services.interview.session_manager.get_json", side_effect=_get_json),
        patch("src.routes.interview.get_config", return_value=None),
    ):
        req = StartFromConfigRequest(interview_config_id="nope", candidate_name="Alice")
        with pytest.raises(HTTPException) as exc:
            await start_from_config(req)
    assert exc.value.status_code == 404
