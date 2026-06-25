"""
REST endpoints for voice interview session lifecycle.

POST /api/v1/voice/session/start  — create session, return token + session_id
GET  /api/v1/voice/session/{id}   — rehydrate session state (for reconnect)
"""

import asyncio
import json as _json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from jose import jwt
from pydantic import BaseModel

from src.lib.jd_extract import extract_jd_text, JDExtractError
from src.lib.settings import get_settings
from src.routes.admin import require_admin
from src.services.audio.voice_session import (
    create_voice_session,
    get_voice_session,
)
from src.services.interview.plan_builder import assemble_voice_plan
from src.services.interview.plan_floor import assess_plan_capacity, TooThinError
from src.services.interview.plan_draft_store import save_plan_draft, get_plan_draft
from src.services.interview.warmup import generate_introduction, build_ease_in
from src.services.llm.interview_planner import plan_interview, PlannerError
from src.services.questions.question_bank import get_question_set
from src.types.config import JDSummary
from src.types.interview import ExperienceLevel
from src.types.planning import InterviewPlanDraft

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice", tags=["voice"])

ALGORITHM = "HS256"
TOKEN_TTL_HOURS = 4


class VoiceSessionStartRequest(BaseModel):
    candidate_name: str = "Candidate"
    job_role: str
    experience_level: ExperienceLevel = ExperienceLevel.MID
    required_skills: list[str] = []


class VoiceSessionStartResponse(BaseModel):
    session_id: str
    token: str
    state: str
    ws_url: str


class VoiceSessionStateResponse(BaseModel):
    session_id: str
    state: str
    current_question_idx: int
    turn_count: int
    connection_state: str


def _issue_token(session_id: str) -> str:
    settings = get_settings()
    payload = {
        "sub": "voice_candidate",
        "session_id": session_id,
        "type": "voice_interview",
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


@router.post(
    "/session/start",
    response_model=VoiceSessionStartResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_voice_session(body: VoiceSessionStartRequest, request: Request) -> VoiceSessionStartResponse:
    session_id = str(uuid.uuid4())

    questions = get_question_set(
        body.job_role,
        body.experience_level,
        body.required_skills,
        count=5,
    )
    if not questions:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No questions available for the selected role and level.",
        )

    intro_text = generate_introduction(body.candidate_name, body.job_role, len(questions))
    create_voice_session(
        session_id=session_id,
        candidate_name=body.candidate_name,
        job_role=body.job_role,
        experience_level=body.experience_level.value,
        required_skills=body.required_skills,
        questions_json=_json.dumps([q.model_dump() for q in questions]),
        intro_text=intro_text,
    )
    logger.info(
        "Voice session start created session=%s role=%s experience=%s",
        session_id,
        body.job_role,
        body.experience_level.value,
    )

    token = _issue_token(session_id)
    # Derive WS base from the incoming request so the URL always matches the
    # server the browser is actually talking to. VOICE_WS_BASE overrides for
    # cases where the public WS hostname differs from the API hostname.
    ws_base = os.getenv("VOICE_WS_BASE")
    if not ws_base:
        scheme = "wss" if request.url.scheme == "https" else "ws"
        ws_base = f"{scheme}://{request.url.netloc}"

    return VoiceSessionStartResponse(
        session_id=session_id,
        token=token,
        state="INITIALIZING",
        ws_url=f"{ws_base}/ws/interview/voice/{session_id}?token={token}",
    )


MIN_QUESTIONS = 5
MAX_QUESTIONS = 8


class PlanPreviewResponse(BaseModel):
    draft_id: str
    role_title: str
    questions: list[dict]
    requested: int
    usable_count: int
    needs_confirmation: bool


class StartFromDraftRequest(BaseModel):
    draft_id: str
    candidate_name: str = "Candidate"


@router.post(
    "/plan/preview",
    response_model=PlanPreviewResponse,
    dependencies=[Depends(require_admin)],
)
async def preview_plan(
    jd: UploadFile = File(...),
    resume: Optional[UploadFile] = File(None),
    job_role: str = Form(...),
    experience_level: ExperienceLevel = Form(ExperienceLevel.MID),
    num_questions: int = Form(MIN_QUESTIONS),
) -> PlanPreviewResponse:
    """Generate a JD-driven plan, cache it as a draft, and return it for admin review.

    Regenerate = call this again (new draft_id). The admin then confirms/starts via
    start_from_draft. Fails loud: unreadable file -> 422, planner failure -> 502,
    too-thin JD (below the floor) -> 422.
    """
    if not (MIN_QUESTIONS <= num_questions <= MAX_QUESTIONS):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"num_questions must be between {MIN_QUESTIONS} and {MAX_QUESTIONS}.",
        )

    jd_bytes = await jd.read()
    try:
        # Offloaded to a thread: sync pypdf/docx + the sync Anthropic client would
        # otherwise block the single event loop and freeze every concurrent session.
        jd_text = await asyncio.to_thread(extract_jd_text, jd.filename or "", jd_bytes)
    except JDExtractError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not read the job description file.",
        )

    resume_text: Optional[str] = None
    if resume is not None:
        resume_bytes = await resume.read()
        try:
            resume_text = await asyncio.to_thread(
                extract_jd_text, resume.filename or "", resume_bytes
            )
        except JDExtractError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Could not read the resume file.",
            )

    try:
        draft = await asyncio.to_thread(
            plan_interview, jd_text, resume_text, job_role, experience_level, num_questions
        )
    except PlannerError as exc:
        logger.error("Voice planner failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not analyze the job description. Try again.",
        )

    try:
        usable_count, shortfall = assess_plan_capacity(len(draft.questions), num_questions)
    except TooThinError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    # Cache the full draft + resolved usable_count so start_from_draft rebuilds the
    # EXACT previewed plan without re-calling the LLM.
    draft_id = save_plan_draft({
        "draft": draft.model_dump(),
        "usable_count": usable_count,
        "job_role": job_role,
        "experience_level": experience_level.value,
    })
    return PlanPreviewResponse(
        draft_id=draft_id,
        role_title=draft.role_title,
        questions=[pq.model_dump() for pq in draft.questions[:usable_count]],
        requested=num_questions,
        usable_count=usable_count,
        needs_confirmation=shortfall,
    )


@router.post(
    "/session/start-from-draft",
    response_model=VoiceSessionStartResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def start_from_draft(body: StartFromDraftRequest, request: Request) -> VoiceSessionStartResponse:
    cached = get_plan_draft(body.draft_id)
    if cached is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan draft not found or expired. Please regenerate.",
        )

    draft = InterviewPlanDraft(**cached["draft"])
    plan = assemble_voice_plan(draft, usable_count=int(cached["usable_count"]))

    session_id = str(uuid.uuid4())
    job_role = draft.role_title or cached["job_role"]
    intro_text = generate_introduction(body.candidate_name, job_role, len(plan.questions))
    ease_in_text = build_ease_in(body.candidate_name)
    jd_summary = JDSummary(skills=draft.skills)
    create_voice_session(
        session_id=session_id,
        candidate_name=body.candidate_name,
        job_role=job_role,
        experience_level=cached["experience_level"],
        required_skills=draft.skills,
        questions_json=_json.dumps([q.model_dump() for q in plan.questions]),
        intro_text=intro_text,
        ease_in_text=ease_in_text,
        jd_summary_json=_json.dumps(jd_summary.model_dump()),
    )
    logger.info(
        "Voice session from draft session=%s role=%s questions=%d",
        session_id, job_role, len(plan.questions),
    )

    token = _issue_token(session_id)
    ws_base = os.getenv("VOICE_WS_BASE")
    if not ws_base:
        scheme = "wss" if request.url.scheme == "https" else "ws"
        ws_base = f"{scheme}://{request.url.netloc}"
    return VoiceSessionStartResponse(
        session_id=session_id,
        token=token,
        state="INITIALIZING",
        ws_url=f"{ws_base}/ws/interview/voice/{session_id}?token={token}",
    )


@router.get("/session/{session_id}", response_model=VoiceSessionStateResponse)
async def get_voice_session_state(session_id: str) -> VoiceSessionStateResponse:
    session = get_voice_session(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Voice session not found.")

    return VoiceSessionStateResponse(
        session_id=session_id,
        state=session.get("state", "UNKNOWN"),
        current_question_idx=int(session.get("current_question_idx", 0)),
        turn_count=int(session.get("turn_count", 0)),
        connection_state=session.get("connection_state", "paused"),
    )
