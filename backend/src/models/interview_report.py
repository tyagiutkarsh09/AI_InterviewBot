"""
Pydantic models and aiosqlite helpers for interview_reports table.
read/write only — no ORM. Direct SQL via aiosqlite.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "interviews.db")


class InterviewMetrics(BaseModel):
    total_questions: int = 0
    questions_answered: int = 0
    avg_answer_duration_s: float = 0.0
    total_candidate_words: int = 0
    total_bot_words: int = 0
    follow_ups_used: int = 0
    barge_ins: int = 0
    silence_strikes: int = 0
    per_topic_confidence: dict[str, float] = Field(default_factory=dict)
    avg_transcription_confidence: float = 1.0
    avg_evaluation_confidence: float = 0.0
    qa_extraction_confidence: float = 1.0


class CategoryScore(BaseModel):
    score: float = Field(default=0.0, ge=0, le=10)
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
    interview_type: str = "text"
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_seconds: Optional[int] = None
    transcript: list[dict] = Field(default_factory=list)
    metrics: InterviewMetrics = Field(default_factory=InterviewMetrics)
    analysis: InterviewAnalysis = Field(default_factory=InterviewAnalysis)
    created_at: Optional[str] = None


_db: Optional[aiosqlite.Connection] = None


async def _get_db() -> Optional[aiosqlite.Connection]:
    global _db
    if _db is not None:
        try:
            await _db.execute("SELECT 1")
            return _db
        except Exception:
            _db = None
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _init_tables(_db)
        return _db
    except Exception as exc:
        logger.error("Failed to open SQLite DB at %s: %s", DB_PATH, exc)
        return None


async def _init_tables(db: aiosqlite.Connection) -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS interview_reports (
            id TEXT PRIMARY KEY,
            session_id TEXT UNIQUE NOT NULL,
            candidate_name TEXT NOT NULL DEFAULT 'Candidate',
            job_role TEXT NOT NULL DEFAULT '',
            experience_level TEXT NOT NULL DEFAULT 'mid',
            interview_type TEXT NOT NULL DEFAULT 'text',
            started_at TEXT,
            ended_at TEXT,
            duration_seconds INTEGER,
            transcript TEXT NOT NULL DEFAULT '[]',
            metrics TEXT NOT NULL DEFAULT '{}',
            analysis TEXT NOT NULL DEFAULT '{}',
            created_at TEXT
        )
    """)
    await db.commit()


async def save_report(report: InterviewReport) -> bool:
    db = await _get_db()
    if db is None:
        logger.error("No DB — report not saved for session %s", report.session_id)
        return False

    now = datetime.now(timezone.utc).isoformat()
    try:
        await db.execute(
            """
            INSERT INTO interview_reports
                (id, session_id, candidate_name, job_role, experience_level,
                 interview_type, started_at, ended_at, duration_seconds,
                 transcript, metrics, analysis, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                transcript = excluded.transcript,
                metrics = excluded.metrics,
                analysis = excluded.analysis,
                ended_at = excluded.ended_at,
                duration_seconds = excluded.duration_seconds,
                interview_type = excluded.interview_type
            """,
            (
                report.id,
                report.session_id,
                report.candidate_name,
                report.job_role,
                report.experience_level,
                report.interview_type,
                report.started_at,
                report.ended_at,
                report.duration_seconds,
                json.dumps(report.transcript),
                report.metrics.model_dump_json(),
                report.analysis.model_dump_json(),
                now,
            ),
        )
        await db.commit()
        logger.info("Report saved to SQLite for session %s", report.session_id)
        return True
    except Exception as exc:
        logger.error("Failed to save report session=%s: %s", report.session_id, exc)
        return False


async def get_report_by_session(session_id: str) -> Optional[InterviewReport]:
    db = await _get_db()
    if db is None:
        return None

    try:
        cursor = await db.execute(
            "SELECT * FROM interview_reports WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_report(row)
    except Exception as exc:
        logger.error("Failed to read report session=%s: %s", session_id, exc)
        return None


def _row_to_report(row) -> InterviewReport:
    return InterviewReport(
        id=row["id"],
        session_id=row["session_id"],
        candidate_name=row["candidate_name"],
        job_role=row["job_role"],
        experience_level=row["experience_level"],
        interview_type=row["interview_type"] or "text",
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        duration_seconds=row["duration_seconds"],
        transcript=json.loads(row["transcript"]) if row["transcript"] else [],
        metrics=InterviewMetrics.model_validate_json(row["metrics"]) if row["metrics"] else InterviewMetrics(),
        analysis=InterviewAnalysis.model_validate_json(row["analysis"]) if row["analysis"] else InterviewAnalysis(),
        created_at=row["created_at"],
    )


async def list_reports(
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[InterviewReport], int]:
    db = await _get_db()
    if db is None:
        return [], 0

    try:
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM interview_reports")
        count_row = await cursor.fetchone()
        total = count_row["cnt"] if count_row else 0

        cursor = await db.execute(
            """
            SELECT * FROM interview_reports
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = await cursor.fetchall()

        reports = []
        for row in rows:
            try:
                reports.append(_row_to_report(row))
            except Exception as exc:
                logger.warning("Skipping malformed report row: %s", exc)

        return reports, total
    except Exception as exc:
        logger.error("Failed to list reports: %s", exc)
        return [], 0
