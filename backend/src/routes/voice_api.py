"""
REST endpoints for voice interview session lifecycle.

POST /api/v1/voice/session/start  — create session, return token + session_id
GET  /api/v1/voice/session/{id}   — rehydrate session state (for reconnect)
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, status
from jose import jwt
from pydantic import BaseModel

from src.lib.settings import get_settings
from src.services.audio.voice_session import (
    create_voice_session,
    get_voice_session,
)
from src.services.questions.question_bank import get_question_set
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
async def start_voice_session(body: VoiceSessionStartRequest) -> VoiceSessionStartResponse:
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

    import json as _json
    create_voice_session(
        session_id=session_id,
        candidate_name=body.candidate_name,
        job_role=body.job_role,
        experience_level=body.experience_level.value,
        required_skills=body.required_skills,
        questions_json=_json.dumps([q.model_dump() for q in questions]),
    )

    token = _issue_token(session_id)
    settings = get_settings()
    ws_base = "ws://localhost:8000"
    if settings.environment != "development":
        ws_base = "wss://api.yourdomain.com"

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
