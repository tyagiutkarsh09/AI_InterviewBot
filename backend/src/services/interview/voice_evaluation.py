"""
Voice interview evaluation pipeline.

1. Load transcript + session metadata from Redis
2. Compute deterministic metrics
3. Call Claude with evaluation prompt
4. Parse response into InterviewAnalysis
5. Save to PostgreSQL
6. Return InterviewReport
"""

import asyncio
import json
import logging
import re
from time import monotonic
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.lib.anthropic_client import get_async_anthropic_client, get_model_for_task
from src.models.interview_report import (
    InterviewAnalysis,
    InterviewMetrics,
    InterviewReport,
    get_report_by_session,
    save_report,
)
from src.services.audio.voice_session import (
    acquire_lock,
    get_voice_session,
    release_lock,
    set_voice_field,
)

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "voice_evaluation_prompt.txt"
FINALIZATION_WAIT_TIMEOUT_SECS = 90.0
FINALIZATION_POLL_INTERVAL_SECS = 0.25


def _compute_per_topic_confidence(voice_data: dict[str, Any]) -> dict[str, float]:
    """
    Composite confidence per topic from three signals:
      - LLM self-confidence (0.55 weight): how certain the LLM was when scoring
      - Follow-up ratio    (0.30 weight): 1.0 if no follow-ups needed, 0 if max used
      - STT reliability    (0.15 weight): session-wide fraction of turns that cleared the low-confidence gate
    """
    running_scores: dict[str, float] = json.loads(voice_data.get("running_scores", "{}"))
    if not running_scores:
        return {}

    llm_confs: dict[str, float] = json.loads(voice_data.get("llm_confidence_by_topic", "{}"))
    fu_by_topic: dict[str, int] = json.loads(voice_data.get("follow_ups_by_topic", "{}"))

    total_turns = max(int(voice_data.get("turn_count", 1)), 1)
    retries = int(voice_data.get("low_confidence_retries", 0))
    stt_reliability = max(0.0, 1.0 - (retries / total_turns))

    result: dict[str, float] = {}
    for topic in running_scores:
        # 0.7 default: topic was accepted (not suppressed) but confidence not recorded
        lc = llm_confs.get(topic, 0.7)
        fu = fu_by_topic.get(topic, 0)
        follow_up_ratio = max(0.0, 1.0 - (fu / 2.0))  # 0 fu→1.0, 1→0.5, 2→0.0
        composite = (lc * 0.55) + (follow_up_ratio * 0.30) + (stt_reliability * 0.15)
        result[topic] = round(composite, 3)

    return result


def _compute_metrics(voice_data: dict[str, Any]) -> InterviewMetrics:
    transcript: list[dict] = json.loads(voice_data.get("transcript", "[]"))
    questions: list[dict] = json.loads(voice_data.get("questions", "[]"))

    candidate_turns = [t for t in transcript if t.get("speaker") == "candidate"]
    bot_turns = [t for t in transcript if t.get("speaker") == "bot"]

    total_candidate_words = sum(len(t.get("text", "").split()) for t in candidate_turns)
    total_bot_words = sum(len(t.get("text", "").split()) for t in bot_turns)

    questions_answered = min(len(candidate_turns), len(questions))
    avg_duration = 0.0

    per_topic_conf = _compute_per_topic_confidence(voice_data)

    llm_confs: dict[str, float] = json.loads(voice_data.get("llm_confidence_by_topic", "{}"))
    avg_eval_conf = sum(llm_confs.values()) / len(llm_confs) if llm_confs else 0.0

    total_turns = max(int(voice_data.get("turn_count", 1)), 1)
    retries = int(voice_data.get("low_confidence_retries", 0))
    stt_reliability = max(0.0, 1.0 - (retries / total_turns))

    fu_count = int(voice_data.get("follow_up_count", 0))
    topic_count = max(len(per_topic_conf), 1)
    qa_extraction = max(0.0, 1.0 - (fu_count / (2.0 * topic_count)))

    return InterviewMetrics(
        total_questions=len(questions),
        questions_answered=questions_answered,
        avg_answer_duration_s=avg_duration,
        total_candidate_words=total_candidate_words,
        total_bot_words=total_bot_words,
        follow_ups_used=int(voice_data.get("follow_up_count", 0)),
        barge_ins=int(voice_data.get("barge_in_count", 0)),
        silence_strikes=int(voice_data.get("silence_strikes", 0)),
        per_topic_confidence=per_topic_conf,
        avg_transcription_confidence=round(stt_reliability, 3),
        avg_evaluation_confidence=round(avg_eval_conf, 3),
        qa_extraction_confidence=round(qa_extraction, 3),
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

    # Inject live per-topic scores so the evaluator does not cold-re-score from
    # the transcript (the "double-pass" scoring bug).  An empty dict gets a
    # sentinel so the LLM still receives a coherent message.
    raw_scores: dict[str, float] = json.loads(voice_data.get("running_scores", "{}"))
    running_scores_text = json.dumps(raw_scores, indent=2) if raw_scores else "(none recorded)"

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
        running_scores=running_scores_text,
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
    logger.info(
        "Voice evaluation started session=%s transcript_turns=%d questions=%d",
        session_id,
        len(json.loads(voice_data.get("transcript", "[]"))),
        len(json.loads(voice_data.get("questions", "[]"))),
    )

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
    started_at = voice_data.get("started_at")
    duration_seconds = None
    if started_at:
        try:
            duration_seconds = int(
                (datetime.fromisoformat(now) - datetime.fromisoformat(started_at)).total_seconds()
            )
        except ValueError:
            logger.warning(
                "Voice evaluation duration parse failed session=%s started_at=%s",
                session_id,
                started_at,
            )

    report = InterviewReport(
        session_id=session_id,
        candidate_name=voice_data.get("candidate_name", "Candidate"),
        job_role=voice_data.get("job_role", ""),
        experience_level=voice_data.get("experience_level", "mid"),
        interview_type="voice",
        started_at=started_at,
        ended_at=now,
        duration_seconds=duration_seconds,
        transcript=transcript_raw,
        metrics=metrics,
        analysis=analysis,
    )

    saved = await save_report(report)
    if not saved:
        logger.warning("Report not persisted to PG for session %s — Redis still has it", session_id)

    set_voice_field(session_id, "state", "COMPLETE")
    set_voice_field(session_id, "ended_at", now)
    set_voice_field(session_id, "evaluation_report", report.model_dump_json())

    logger.info("Voice evaluation complete session=%s score=%.1f", session_id, analysis.overall_score)
    return report


async def finalize_voice_session(session_id: str) -> InterviewReport | None:
    voice_data = get_voice_session(session_id)
    if voice_data is None:
        logger.warning("Voice finalization lookup missed session=%s", session_id)
        report = await get_report_by_session(session_id)
        if report is not None:
            logger.info("Voice finalization recovered persisted report session=%s", session_id)
        return report

    report_json = voice_data.get("evaluation_report")
    if report_json:
        logger.info("Voice session already finalized session=%s", session_id)
        return InterviewReport.model_validate_json(report_json)

    if not acquire_lock(session_id):
        logger.info("Voice finalization already in progress session=%s", session_id)
        deadline = monotonic() + FINALIZATION_WAIT_TIMEOUT_SECS
        while monotonic() < deadline:
            await asyncio.sleep(FINALIZATION_POLL_INTERVAL_SECS)
            current = get_voice_session(session_id)
            if current is None:
                report = await get_report_by_session(session_id)
                if report is not None:
                    logger.info("Voice finalization observed persisted report session=%s", session_id)
                    return report
                continue
            report_json = current.get("evaluation_report")
            if report_json:
                logger.info("Voice finalization observed completed report session=%s", session_id)
                return InterviewReport.model_validate_json(report_json)
        logger.error("Voice finalization timed out session=%s", session_id)
        return await get_report_by_session(session_id)

    try:
        current = get_voice_session(session_id)
        if current is None:
            logger.warning("Voice finalization lost session after lock session=%s", session_id)
            return await get_report_by_session(session_id)

        report_json = current.get("evaluation_report")
        if report_json:
            logger.info("Voice session finalized during lock wait session=%s", session_id)
            return InterviewReport.model_validate_json(report_json)

        logger.info(
            "Voice finalization started session=%s state=%s connection_state=%s",
            session_id,
            current.get("state", "UNKNOWN"),
            current.get("connection_state", "unknown"),
        )
        return await run_voice_evaluation(session_id)
    finally:
        release_lock(session_id)
