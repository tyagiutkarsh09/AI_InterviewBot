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
from src.services.interview.plan_builder import build_voice_plan, InsufficientQuestionsError
from src.services.interview.warmup import generate_introduction, build_ease_in
from src.services.llm.jd_analysis import analyze_jd, JDAnalysisError
from src.services.llm.resume_analysis import analyze_resume, ResumeAnalysisError
from src.services.questions.question_bank import get_question_set, eligible_question_count
from src.types.config import JDSummary
from src.types.interview import ExperienceLevel

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


VOICE_CORE_RATIO = 0.7   # ~70% bank / 30% JD when a JD is attached
MIN_QUESTIONS = 5
MAX_QUESTIONS = 10


@router.post(
    "/session/start-from-jd",
    response_model=VoiceSessionStartResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def start_voice_session_from_jd(
    request: Request,
    resume: Optional[UploadFile] = File(None),
    jd: Optional[UploadFile] = File(None),
    candidate_name: str = Form("Candidate"),
    job_role: str = Form(...),
    experience_level: ExperienceLevel = Form(ExperienceLevel.MID),
    num_questions: int = Form(MIN_QUESTIONS),
) -> VoiceSessionStartResponse:
    if not (MIN_QUESTIONS <= num_questions <= MAX_QUESTIONS):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"num_questions must be between {MIN_QUESTIONS} and {MAX_QUESTIONS}.",
        )

    # --- Resume (optional, primary): extract + analyze -> skills + personalized Qs ---
    resume_skills: list[str] = []
    resume_questions: list[dict] = []
    if resume is not None:
        resume_bytes = await resume.read()
        try:
            # Offloaded to a thread: extract_jd_text (sync pypdf/docx) and analyze_resume
            # (sync Anthropic client) would otherwise block the single event loop and
            # freeze the whole server. See get_async_anthropic_client docstring.
            resume_text = await asyncio.to_thread(
                extract_jd_text, resume.filename or "", resume_bytes
            )
        except JDExtractError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Could not read the resume file.",
            )
        try:
            resume_skills, resume_questions = await asyncio.to_thread(
                analyze_resume, resume_text, num_questions=2
            )
        except ResumeAnalysisError as exc:
            logger.error("Voice resume analysis failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not analyze the resume. Try again.",
            )

    # --- JD (optional filler): extract + analyze -> jd_summary + role-specific Qs ---
    jd_summary = JDSummary(skills=resume_skills)
    jd_ideas: list[dict] = []
    if jd is not None:
        jd_bytes = await jd.read()
        try:
            # Offloaded to a thread for the same reason as the resume path above.
            jd_text = await asyncio.to_thread(extract_jd_text, jd.filename or "", jd_bytes)
        except JDExtractError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Could not read the job description file.",
            )
        try:
            parsed_summary, jd_ideas = await asyncio.to_thread(analyze_jd, jd_text)
        except JDAnalysisError as exc:
            logger.error("Voice JD analysis failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not analyze the job description. Try again.",
            )
        # Dedup case-insensitively (LLM-derived skills aren't case-normalized) so a
        # "Python"/"python" pair doesn't burn two of the 8 skill slots. Resume comes
        # first, so the candidate's own casing wins on collision.
        merged: list[str] = []
        seen: set[str] = set()
        for skill in [*resume_skills, *parsed_summary.skills]:
            if skill.lower() not in seen:
                seen.add(skill.lower())
                merged.append(skill)
        merged = merged[:8]
        jd_summary = JDSummary(
            skills=merged,
            responsibilities=parsed_summary.responsibilities,
            seniority_signals=parsed_summary.seniority_signals,
        )

    # --- No-JD cap: the bank can't exceed its per-level eligibility ---
    technical_count = num_questions
    if not jd_ideas:
        capacity = eligible_question_count(experience_level)
        if technical_count > capacity:
            logger.warning(
                "Capping num_questions %d -> %d (no JD, level=%s bank capacity)",
                technical_count, capacity, experience_level.value,
            )
            technical_count = capacity

    try:
        plan = build_voice_plan(
            role=job_role,
            experience_level=experience_level,
            jd_summary=jd_summary,
            jd_question_ideas=jd_ideas,
            resume_questions=resume_questions,
            technical_count=technical_count,
            core_ratio=VOICE_CORE_RATIO,
        )
    except InsufficientQuestionsError as exc:
        logger.warning("Voice plan could not be built: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Not enough questions available to build the interview for this role and level.",
        )

    session_id = str(uuid.uuid4())
    intro_text = generate_introduction(candidate_name, job_role, len(plan.questions))
    ease_in_text = build_ease_in(candidate_name)
    create_voice_session(
        session_id=session_id,
        candidate_name=candidate_name,
        job_role=job_role,
        experience_level=experience_level.value,
        required_skills=jd_summary.skills,
        questions_json=_json.dumps([q.model_dump() for q in plan.questions]),
        intro_text=intro_text,
        ease_in_text=ease_in_text,
        jd_summary_json=_json.dumps(jd_summary.model_dump()),
    )
    logger.info(
        "Voice session created session=%s role=%s technical=%d total=%d jd=%s resume=%s",
        session_id, job_role, technical_count, len(plan.questions),
        jd is not None, resume is not None,
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
