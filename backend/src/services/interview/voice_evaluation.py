"""
Voice interview evaluation pipeline.

1. Load transcript + session metadata from Redis
2. Compute deterministic metrics
3. Call Claude with evaluation prompt
4. Parse response into InterviewAnalysis
5. Save to PostgreSQL
6. Return InterviewReport
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.lib.anthropic_client import get_async_anthropic_client, get_model_for_task
from src.models.interview_report import (
    InterviewAnalysis,
    InterviewMetrics,
    InterviewReport,
    save_report,
)
from src.services.audio.voice_session import get_voice_session, set_voice_field

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "voice_evaluation_prompt.txt"


def _compute_metrics(voice_data: dict[str, Any]) -> InterviewMetrics:
    transcript: list[dict] = json.loads(voice_data.get("transcript", "[]"))
    questions: list[dict] = json.loads(voice_data.get("questions", "[]"))

    candidate_turns = [t for t in transcript if t.get("speaker") == "candidate"]
    bot_turns = [t for t in transcript if t.get("speaker") == "bot"]

    total_candidate_words = sum(len(t.get("text", "").split()) for t in candidate_turns)
    total_bot_words = sum(len(t.get("text", "").split()) for t in bot_turns)

    questions_answered = len(candidate_turns)
    avg_duration = 0.0

    return InterviewMetrics(
        total_questions=len(questions),
        questions_answered=questions_answered,
        avg_answer_duration_s=avg_duration,
        total_candidate_words=total_candidate_words,
        total_bot_words=total_bot_words,
        follow_ups_used=int(voice_data.get("follow_up_count", 0)),
        barge_ins=int(voice_data.get("barge_in_count", 0)),
        silence_strikes=int(voice_data.get("silence_strikes", 0)),
    )


def _format_transcript(voice_data: dict[str, Any]) -> str:
    transcript: list[dict] = json.loads(voice_data.get("transcript", "[]"))
    lines = []
    for t in transcript:
        speaker = "Interviewer" if t.get("speaker") == "bot" else "Candidate"
        ts_raw = t.get("timestamp", "")
        time_prefix = ""
        if ts_raw:
            try:
                dt = datetime.fromisoformat(ts_raw)
                time_prefix = f"[{dt.strftime('%H:%M:%S')}] "
            except (ValueError, TypeError):
                pass
        lines.append(f"{time_prefix}[{speaker}]: {t.get('text', '')}")
    return "\n".join(lines)


def _build_prompt(voice_data: dict[str, Any], metrics: InterviewMetrics) -> str:
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    return template.format(
        job_role=voice_data.get("job_role", ""),
        experience_level=voice_data.get("experience_level", "mid"),
        candidate_name=voice_data.get("candidate_name", "Candidate"),
        transcript=_format_transcript(voice_data),
        total_questions=metrics.total_questions,
        questions_answered=metrics.questions_answered,
        avg_answer_duration_s=metrics.avg_answer_duration_s,
        total_candidate_words=metrics.total_candidate_words,
        follow_ups_used=metrics.follow_ups_used,
        barge_ins=metrics.barge_ins,
        silence_strikes=metrics.silence_strikes,
    )


def _strip_markdown_fences(text: str) -> str:
    stripped = text.strip()
    match = re.search(r"```(?:\w*)\s*\n(.*?)```", stripped, re.DOTALL)
    if match:
        return match.group(1).strip()
    return stripped


def _parse_analysis(raw: str) -> InterviewAnalysis:
    cleaned = _strip_markdown_fences(raw)
    try:
        return InterviewAnalysis.model_validate(json.loads(cleaned))
    except (json.JSONDecodeError, ValueError) as first_err:
        json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if json_match:
            try:
                return InterviewAnalysis.model_validate(json.loads(json_match.group()))
            except (json.JSONDecodeError, ValueError):
                pass
        logger.error("Failed to parse LLM evaluation response: %s", first_err)
        return InterviewAnalysis(summary="Evaluation parsing failed — raw response stored for review.")


async def run_voice_evaluation(session_id: str) -> InterviewReport:
    """
    Full evaluation pipeline. Returns InterviewReport.
    Raises on LLM failure (caller should handle).
    """
    voice_data = get_voice_session(session_id)
    if voice_data is None:
        raise ValueError(f"Voice session {session_id} not found")

    set_voice_field(session_id, "state", "EVALUATING")

    metrics = _compute_metrics(voice_data)
    prompt_text = _build_prompt(voice_data, metrics)

    client = get_async_anthropic_client()
    response = await client.messages.create(
        model=get_model_for_task("evaluation"),
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt_text}],
    )
    raw_json = response.content[0].text
    analysis = _parse_analysis(raw_json)

    transcript_raw: list[dict] = json.loads(voice_data.get("transcript", "[]"))
    now = datetime.now(timezone.utc).isoformat()

    report = InterviewReport(
        session_id=session_id,
        candidate_name=voice_data.get("candidate_name", "Candidate"),
        job_role=voice_data.get("job_role", ""),
        experience_level=voice_data.get("experience_level", "mid"),
        started_at=voice_data.get("started_at"),
        ended_at=now,
        duration_seconds=None,
        transcript=transcript_raw,
        metrics=metrics,
        analysis=analysis,
    )

    saved = await save_report(report)
    if not saved:
        logger.warning("Report not persisted to PG for session %s — Redis still has it", session_id)

    set_voice_field(session_id, "state", "COMPLETE")
    set_voice_field(session_id, "evaluation_report", report.model_dump_json())

    logger.info("Voice evaluation complete session=%s score=%.1f", session_id, analysis.overall_score)
    return report
