"""POST /voice/plan/preview + /voice/session/start-from-draft (JD-driven planner flow).

WHY: preview must (a) reject non-admins before any LLM call (guard tested directly);
(b) generate a plan from the JD via the planner and cache a draft; (c) flag a shortfall
for admin confirmation when the JD can't meet the requested count; (d) hard-fail (422)
below the floor; (e) reject out-of-range counts and unreadable files. start-from-draft
must build a session from a cached draft and 404 a missing one. Endpoint functions are
called directly (the codebase's convention) so auth/multipart plumbing is out of scope.
"""
import io
import json

import pytest
from fastapi import HTTPException, UploadFile

from src.routes import voice_api
from src.types.interview import ExperienceLevel
from src.types.planning import InterviewPlanDraft, PlannedQuestion


def _upload(name="jd.pdf", data=b"%PDF-bytes"):
    return UploadFile(filename=name, file=io.BytesIO(data))


class _Req:
    class _Url:
        scheme = "http"; netloc = "testserver"
    url = _Url()


def _pq(comp, diff="medium", src="jd"):
    return PlannedQuestion(competency=comp, source=src, question_text=f"Q {comp}?",
                           difficulty=diff, rubric_keypoints=["a"], time_budget_sec=120)


def _draft(n, role="Sr ME"):
    return InterviewPlanDraft(role_title=role, skills=["GD&T"],
                              questions=[_pq(f"c{i}") for i in range(n)],
                              project_question_text="walk me through a project")


@pytest.mark.asyncio
async def test_admin_guard_rejects_before_llm():
    # Guard fails loud on a bad key, independent of the endpoint wiring.
    from src.routes.admin import require_admin
    with pytest.raises(HTTPException) as exc:
        await require_admin(x_admin_key="wrong")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_preview_returns_draft_and_questions(monkeypatch):
    monkeypatch.setattr(voice_api, "extract_jd_text", lambda fn, data: "JD TEXT")
    monkeypatch.setattr(voice_api, "plan_interview", lambda *a, **k: _draft(5))
    resp = await voice_api.preview_plan(
        jd=_upload(), resume=None, job_role="ME",
        experience_level=ExperienceLevel.MID, num_questions=5,
    )
    assert resp.draft_id
    assert len(resp.questions) == 5
    assert resp.needs_confirmation is False
    assert resp.role_title == "Sr ME"


@pytest.mark.asyncio
async def test_preview_flags_shortfall_for_confirmation(monkeypatch):
    # asked 8, planner grounded only 6 -> usable 6, needs_confirmation True.
    monkeypatch.setattr(voice_api, "extract_jd_text", lambda fn, data: "JD")
    monkeypatch.setattr(voice_api, "plan_interview", lambda *a, **k: _draft(6))
    resp = await voice_api.preview_plan(
        jd=_upload(), resume=None, job_role="ME",
        experience_level=ExperienceLevel.MID, num_questions=8,
    )
    assert resp.needs_confirmation is True
    assert resp.usable_count == 6


@pytest.mark.asyncio
async def test_preview_too_thin_is_422(monkeypatch):
    monkeypatch.setattr(voice_api, "extract_jd_text", lambda fn, data: "JD")
    monkeypatch.setattr(voice_api, "plan_interview", lambda *a, **k: _draft(3))
    with pytest.raises(HTTPException) as exc:
        await voice_api.preview_plan(
            jd=_upload(), resume=None, job_role="ME",
            experience_level=ExperienceLevel.MID, num_questions=5,
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_preview_out_of_range_rejected():
    with pytest.raises(HTTPException) as exc:
        await voice_api.preview_plan(
            jd=_upload(), resume=None, job_role="ME",
            experience_level=ExperienceLevel.MID, num_questions=99,
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_preview_unreadable_jd_422(monkeypatch):
    from src.lib.jd_extract import JDExtractError

    def _boom(fn, data):
        raise JDExtractError("bad")

    monkeypatch.setattr(voice_api, "extract_jd_text", _boom)
    with pytest.raises(HTTPException) as exc:
        await voice_api.preview_plan(
            jd=_upload(), resume=None, job_role="ME",
            experience_level=ExperienceLevel.MID, num_questions=5,
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_start_from_draft_builds_session(monkeypatch):
    monkeypatch.setattr(voice_api, "extract_jd_text", lambda fn, data: "JD")
    monkeypatch.setattr(voice_api, "plan_interview", lambda *a, **k: _draft(5))
    pv = await voice_api.preview_plan(
        jd=_upload(), resume=None, job_role="ME",
        experience_level=ExperienceLevel.MID, num_questions=5,
    )
    resp = await voice_api.start_from_draft(
        body=voice_api.StartFromDraftRequest(draft_id=pv.draft_id, candidate_name="Alex"),
        request=_Req(),
    )
    assert resp.session_id
    assert resp.ws_url
    from src.services.audio.voice_session import get_voice_session
    sess = get_voice_session(resp.session_id)
    assert sess is not None
    # split opening: the intro is its own questionless turn.
    assert json.loads(sess["transcript"])[0]["type"] == "intro"


@pytest.mark.asyncio
async def test_start_from_missing_draft_404():
    with pytest.raises(HTTPException) as exc:
        await voice_api.start_from_draft(
            body=voice_api.StartFromDraftRequest(draft_id="nope", candidate_name="A"),
            request=_Req(),
        )
    assert exc.value.status_code == 404
