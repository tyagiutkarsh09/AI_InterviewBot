"""
Voice LLM orchestrator — Feature [9] FSM wire-up.

Imports existing:
  - InterviewState (types/interview.py)
  - build_system_prompt, build_answer_evaluation_prompt (services/llm/prompt_builder.py)
  - parse_xml_response (services/llm/response_parser.py)

Never copies, always imports.

speech_final → Redis session → LLM → parse XML → update Redis → return spoken_text
Scores written to Redis immediately; session end hands off to existing evaluation pipeline.
"""

import json
import logging
from typing import Any

from src.lib.anthropic_client import get_async_anthropic_client, get_model_for_task
from src.services.audio.voice_session import (
    get_voice_session,
    set_voice_field,
    append_transcript_turn,
)
from src.services.llm.prompt_builder import (
    build_system_prompt,
    build_answer_evaluation_prompt,
)
from src.services.llm.response_parser import parse_xml_response
from src.types.interview import (
    ExperienceLevel,
    InterviewState,
    Question,
    SessionState,
    TurnRecord,
)

logger = logging.getLogger(__name__)

COMPLETION_MESSAGE = (
    "That concludes our interview. Thank you so much for your time today. "
    "You did great. The evaluation is being processed and you'll receive your "
    "report shortly."
)


def _build_session_state(voice_data: dict[str, Any]) -> SessionState:
    """Reconstruct a SessionState from the Redis voice session hash."""
    questions_raw: list[dict] = json.loads(voice_data.get("questions", "[]"))
    questions = [Question(**q) for q in questions_raw]

    transcript_raw: list[dict] = json.loads(voice_data.get("transcript", "[]"))
    transcript = [
        TurnRecord(
            turn_idx=i,
            speaker=t["speaker"],
            text=t["text"],
            timestamp="",
        )
        for i, t in enumerate(transcript_raw)
    ]

    running_scores: dict[str, float] = json.loads(
        voice_data.get("running_scores", "{}")
    )

    return SessionState(
        session_id="",  # not needed for prompt building
        candidate_name=voice_data.get("candidate_name", "Candidate"),
        job_role=voice_data.get("job_role", ""),
        experience_level=ExperienceLevel(
            voice_data.get("experience_level", "mid")
        ),
        required_skills=json.loads(voice_data.get("required_skills", "[]")),
        questions=questions,
        current_question_idx=int(voice_data.get("current_question_idx", 0)),
        transcript=transcript,
        running_scores=running_scores,
        follow_up_count=int(voice_data.get("follow_up_count", 0)),
        state=InterviewState.QUESTIONING,
    )


async def run_llm_turn(session_id: str, transcript: str) -> str:
    """
    Run one interview turn through the LLM.
    Returns the spoken_text for TTS.
    """
    voice_data = get_voice_session(session_id)
    if voice_data is None:
        return "I lost track of the session. Let's continue."

    questions_raw: list[dict] = json.loads(voice_data.get("questions", "[]"))
    if not questions_raw:
        return "I'm having trouble loading the questions. Let's continue."

    questions = [Question(**q) for q in questions_raw]
    current_idx = int(voice_data.get("current_question_idx", 0))

    if current_idx >= len(questions):
        # All questions done — trigger final evaluation
        await _trigger_final_evaluation(session_id, voice_data)
        return COMPLETION_MESSAGE

    current_q = questions[current_idx]
    session = _build_session_state(voice_data)

    system_prompt = build_system_prompt()
    user_prompt = build_answer_evaluation_prompt(
        question=current_q,
        answer=transcript,
        session=session,
    )

    try:
        client = get_async_anthropic_client()
        response = await client.messages.create(
            model=get_model_for_task("interview"),
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw_text = response.content[0].text
        parsed = parse_xml_response(raw_text)
    except Exception as exc:
        logger.error("LLM call failed session=%s: %s", session_id, exc)
        return "Thank you. Let me continue with the next question."

    # Persist score to Redis
    if parsed.score is not None and parsed.score_topic:
        scores: dict[str, float] = json.loads(
            voice_data.get("running_scores", "{}")
        )
        scores[parsed.score_topic] = parsed.score
        set_voice_field(session_id, "running_scores", json.dumps(scores))
        logger.info(
            "Score recorded session=%s topic=%s score=%.1f",
            session_id, parsed.score_topic, parsed.score,
        )

    # Advance question or follow-up
    action = parsed.action
    follow_up_count = int(voice_data.get("follow_up_count", 0))
    MAX_FOLLOW_UPS = 2

    if action in ("acknowledge", "transition") or follow_up_count >= MAX_FOLLOW_UPS:
        # Move to next question
        next_idx = current_idx + 1
        set_voice_field(session_id, "current_question_idx", next_idx)
        set_voice_field(session_id, "follow_up_count", 0)

        if next_idx >= len(questions):
            await _trigger_final_evaluation(session_id, voice_data)
            spoken = parsed.spoken_text or "Great."
            return f"{spoken} {COMPLETION_MESSAGE}"

        next_q = questions[next_idx]
        append_transcript_turn(session_id, "bot", next_q.question_text)
        spoken = parsed.spoken_text or "Thank you."
        return f"{spoken} {next_q.question_text}"

    elif action == "follow_up":
        set_voice_field(session_id, "follow_up_count", follow_up_count + 1)
        append_transcript_turn(
            session_id, "bot", parsed.spoken_text or "Can you elaborate?"
        )
        return parsed.spoken_text or "Could you tell me more about that?"

    else:
        # Default: acknowledge and move on
        next_idx = current_idx + 1
        set_voice_field(session_id, "current_question_idx", next_idx)
        set_voice_field(session_id, "follow_up_count", 0)

        if next_idx >= len(questions):
            await _trigger_final_evaluation(session_id, voice_data)
            return COMPLETION_MESSAGE

        next_q = questions[next_idx]
        append_transcript_turn(session_id, "bot", next_q.question_text)
        spoken = parsed.spoken_text or "Thank you."
        return f"{spoken} {next_q.question_text}"


async def _trigger_final_evaluation(
    session_id: str, voice_data: dict[str, Any]
) -> None:
    """
    Hand off to the voice evaluation pipeline.
    Computes metrics, runs LLM evaluation, persists to PG + Redis.
    """
    logger.info("Triggering voice evaluation for session %s", session_id)
    try:
        from src.services.interview.voice_evaluation import run_voice_evaluation
        await run_voice_evaluation(session_id)
    except Exception as exc:
        logger.error(
            "Voice evaluation failed session=%s: %s", session_id, exc
        )
        set_voice_field(session_id, "state", "COMPLETE")
