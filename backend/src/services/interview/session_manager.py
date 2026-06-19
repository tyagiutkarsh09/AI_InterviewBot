import uuid
from datetime import datetime, timezone
from typing import Optional
from src.types.interview import (
    SessionState,
    InterviewState,
    ExperienceLevel,
    QuestionResult,
    TurnRecord,
)
from src.services.questions.question_bank import get_question_set
from src.lib.redis_client import set_json, get_json, delete_key, key_exists
from src.types.config import InterviewConfig

SESSION_KEY_PREFIX = "session:"
SESSION_TTL = 14400  # 4 hours


def _session_key(session_id: str) -> str:
    return f"{SESSION_KEY_PREFIX}{session_id}"


def create_session(
    candidate_name: str,
    job_role: str,
    experience_level: ExperienceLevel,
    required_skills: list[str],
    question_count: int = 5,
) -> SessionState:
    session_id = str(uuid.uuid4())
    questions = get_question_set(job_role, experience_level, required_skills, question_count)

    session = SessionState(
        session_id=session_id,
        state=InterviewState.STARTED,
        candidate_name=candidate_name,
        job_role=job_role,
        experience_level=experience_level,
        required_skills=required_skills,
        questions=questions,
        current_question_idx=0,
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    _persist(session)
    return session


def get_session(session_id: str) -> Optional[SessionState]:
    data = get_json(_session_key(session_id))
    if data is None:
        return None
    return SessionState(**data)


def update_session(session: SessionState) -> None:
    _persist(session)


def end_session(session: SessionState) -> SessionState:
    session.ended_at = datetime.now(timezone.utc).isoformat()
    if session.started_at:
        start = datetime.fromisoformat(session.started_at)
        end = datetime.fromisoformat(session.ended_at)
        duration = int((end - start).total_seconds())
    else:
        duration = 0
    _persist(session)
    return session


def record_turn(
    session: SessionState,
    speaker: str,
    text: str,
    question_id: Optional[str] = None,
) -> TurnRecord:
    turn = TurnRecord(
        turn_idx=len(session.transcript),
        speaker=speaker,
        text=text,
        timestamp=datetime.now(timezone.utc).isoformat(),
        question_id=question_id,
    )
    session.transcript.append(turn)
    _persist(session)
    return turn


def advance_question(session: SessionState) -> bool:
    session.current_question_idx += 1
    session.follow_up_count = 0
    _persist(session)
    return session.current_question_idx < len(session.questions)


def create_session_from_config(
    config: InterviewConfig,
    candidate_name: str,
    resume_details: Optional[dict] = None,
) -> SessionState:
    """Create a session whose questions ARE the config's frozen plan.

    Stores only whitelisted resume fields (skills, current_company) — never PII.
    """
    session_id = str(uuid.uuid4())
    whitelisted = None
    if resume_details:
        whitelisted = {
            "skills": resume_details.get("skills") or [],
            "current_company": resume_details.get("current_company") or "",
        }

    session = SessionState(
        session_id=session_id,
        state=InterviewState.STARTED,
        candidate_name=candidate_name,
        job_role=config.role,
        experience_level=config.experience_level,
        required_skills=config.jd_summary.skills,
        questions=list(config.interview_plan.questions),
        current_question_idx=0,
        started_at=datetime.now(timezone.utc).isoformat(),
        interview_config_id=config.id,
        jd_summary=config.jd_summary.model_dump(),
        resume_details=whitelisted,
    )
    _persist(session)
    return session


def _persist(session: SessionState) -> None:
    set_json(_session_key(session.session_id), session.model_dump(), SESSION_TTL)
