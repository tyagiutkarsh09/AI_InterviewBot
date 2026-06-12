"""
Pydantic models and asyncpg helpers for interview_reports table.
read/write only — no ORM. Direct SQL via asyncpg.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class InterviewMetrics(BaseModel):
    total_questions: int = 0
    questions_answered: int = 0
    avg_answer_duration_s: float = 0.0
    total_candidate_words: int = 0
    total_bot_words: int = 0
    follow_ups_used: int = 0
    barge_ins: int = 0
    silence_strikes: int = 0


class CategoryScore(BaseModel):
    score: float = Field(ge=0, le=10)
    explanation: str = ""
    evidence: str = ""


class HighlightedAnswer(BaseModel):
    question: str = ""
    summary: str = ""
    why: str = ""


class InterviewAnalysis(BaseModel):
    summary: str = ""
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    communication_clarity: CategoryScore = Field(default_factory=CategoryScore)
    technical_depth: CategoryScore = Field(default_factory=CategoryScore)
    confidence_consistency: CategoryScore = Field(default_factory=CategoryScore)
    relevance: CategoryScore = Field(default_factory=CategoryScore)
    overall_score: float = 0.0
    best_answer: HighlightedAnswer = Field(default_factory=HighlightedAnswer)
    weakest_answer: HighlightedAnswer = Field(default_factory=HighlightedAnswer)
    red_flags: list[str] = Field(default_factory=list)
    hiring_recommendation: str = "maybe"
    per_question: list[dict] = Field(default_factory=list)
    topic_scores: dict[str, float] = Field(default_factory=dict)


class InterviewReport(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    candidate_name: str = "Candidate"
    job_role: str = ""
    experience_level: str = "mid"
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_seconds: Optional[int] = None
    transcript: list[dict] = Field(default_factory=list)
    metrics: InterviewMetrics = Field(default_factory=InterviewMetrics)
    analysis: InterviewAnalysis = Field(default_factory=InterviewAnalysis)
    created_at: Optional[str] = None


_pool = None


async def _get_pool():
    global _pool
    if _pool is None:
        import asyncpg
        from src.lib.settings import get_settings
        settings = get_settings()
        try:
            _pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=5)
        except Exception as exc:
            logger.error("Failed to create PG pool: %s", exc)
            return None
    return _pool


async def save_report(report: InterviewReport) -> bool:
    pool = await _get_pool()
    if pool is None:
        logger.error("No PG pool — report not saved for session %s", report.session_id)
        return False

    now = datetime.now(timezone.utc).isoformat()
    try:
        await pool.execute(
            """
            INSERT INTO interview_reports
                (id, session_id, candidate_name, job_role, experience_level,
                 started_at, ended_at, duration_seconds,
                 transcript, metrics, analysis, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (session_id) DO UPDATE SET
                transcript = EXCLUDED.transcript,
                metrics = EXCLUDED.metrics,
                analysis = EXCLUDED.analysis,
                ended_at = EXCLUDED.ended_at,
                duration_seconds = EXCLUDED.duration_seconds
            """,
            uuid.UUID(report.id),
            report.session_id,
            report.candidate_name,
            report.job_role,
            report.experience_level,
            datetime.fromisoformat(report.started_at) if report.started_at else None,
            datetime.fromisoformat(report.ended_at) if report.ended_at else None,
            report.duration_seconds,
            json.dumps(report.transcript),
            report.metrics.model_dump_json(),
            report.analysis.model_dump_json(),
            now,
        )
        logger.info("Report saved to PG for session %s", report.session_id)
        return True
    except Exception as exc:
        logger.error("Failed to save report session=%s: %s", report.session_id, exc)
        return False


async def get_report_by_session(session_id: str) -> Optional[InterviewReport]:
    pool = await _get_pool()
    if pool is None:
        return None

    try:
        row = await pool.fetchrow(
            "SELECT * FROM interview_reports WHERE session_id = $1",
            session_id,
        )
        if row is None:
            return None

        return InterviewReport(
            id=str(row["id"]),
            session_id=row["session_id"],
            candidate_name=row["candidate_name"],
            job_role=row["job_role"],
            experience_level=row["experience_level"],
            started_at=row["started_at"].isoformat() if row["started_at"] else None,
            ended_at=row["ended_at"].isoformat() if row["ended_at"] else None,
            duration_seconds=row["duration_seconds"],
            transcript=json.loads(row["transcript"]) if isinstance(row["transcript"], str) else row["transcript"],
            metrics=InterviewMetrics.model_validate_json(
                row["metrics"] if isinstance(row["metrics"], str) else json.dumps(row["metrics"])
            ),
            analysis=InterviewAnalysis.model_validate_json(
                row["analysis"] if isinstance(row["analysis"], str) else json.dumps(row["analysis"])
            ),
            created_at=row["created_at"].isoformat() if row["created_at"] else None,
        )
    except Exception as exc:
        logger.error("Failed to read report session=%s: %s", session_id, exc)
        return None
