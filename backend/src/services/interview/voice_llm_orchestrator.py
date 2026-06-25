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

import asyncio
import json
import logging
import re
from typing import Any

from src.lib.anthropic_client import get_async_anthropic_client, get_model_for_task
from src.services.audio.voice_session import (
    get_voice_session,
    increment_voice_field,
    set_voice_field,
    append_transcript_turn,
)
from src.services.llm.prompt_builder import (
    build_system_prompt,
    build_answer_evaluation_prompt,
)
from src.services.llm.response_parser import parse_xml_response, validate_single_question
from src.types.interview import (
    ExperienceLevel,
    InterviewState,
    Question,
    SessionState,
    TurnRecord,
)

logger = logging.getLogger(__name__)

LOW_CONFIDENCE_THRESHOLD = 0.5

COMPLETION_MESSAGE = (
    "That concludes our interview. Thank you so much for your time today. "
    "You did great. The evaluation is being processed and you'll receive your "
    "report shortly."
)

from src.services.interview.outro import answer_candidate_question, MAX_OUTRO_QUESTIONS

WRAP_UP_INVITE = (
    "That's the last of my questions, {name}. Before we wrap up — is there "
    "anything you'd like to ask me about the role or the team?"
)
CLOSING_SIGN_OFF = (
    "Thank you so much for your time today, {name}. You'll get a summary of how the "
    "interview went, and the recruiter will follow up with you on next steps. "
    "Best of luck!"
)

_NO_QUESTION_PHRASES = (
    "no", "nope", "no questions", "no question", "nothing", "im good", "i'm good",
    "all good", "im fine", "i'm fine", "no thanks", "no thank you", "that's all",
    "thats all", "nothing else",
)


def _acknowledgment_only(spoken: str) -> str:
    """Reduce an LLM acknowledgment bridge to its non-interrogative lead-in.

    The orchestrator appends the canonical next question (from the question bank
    or the wrap-up invite) on top of spoken_text, so spoken_text must contribute
    only a brief acknowledgment. Any question the model included would otherwise
    reach the candidate as a SECOND question — the "two questions at once" bug.
    Keep the leading non-question sentences; drop everything from the first
    question onward. (The follow_up path keeps spoken_text verbatim and is not
    routed through here, since there the question IS the turn.)
    """
    if not spoken:
        return ""
    if "?" not in spoken:
        return spoken.strip()
    kept: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", spoken.strip()):
        if "?" in sentence:
            break
        kept.append(sentence)
    return " ".join(kept).strip()


def _is_no_questions(text: str) -> bool:
    """Deterministic: did the candidate decline to ask anything? (code, not LLM)"""
    t = text.lower().strip().strip(".!?,")
    if t in _NO_QUESTION_PHRASES:
        return True
    words = t.split()
    # Anything longer than a short utterance, or that contains a question signal,
    # is presumed to carry a real question even if it opens with "no"/"nothing".
    if len(words) > 4 or any(kw in t for kw in ("question", "ask", "wonder", "curious")):
        return False
    leading = t.split(",")[0].strip()
    if leading in _NO_QUESTION_PHRASES:
        return True
    return t.startswith(("no ", "nope", "nothing", "i'm good", "im good", "i'm fine", "im fine"))


def _enter_wrap_up(session_id: str, voice_data: dict, lead_in: str = "") -> str:
    set_voice_field(session_id, "interview_phase", "wrap_up")
    set_voice_field(session_id, "outro_questions_used", 0)
    name = voice_data.get("candidate_name", "there")
    invite = WRAP_UP_INVITE.format(name=name)
    append_transcript_turn(session_id, "bot", invite, entry_type="wrap_up_invite")
    return f"{lead_in} {invite}".strip()


async def _handle_wrap_up_turn(session_id: str, transcript: str, voice_data: dict) -> str:
    name = voice_data.get("candidate_name", "there")
    used = int(voice_data.get("outro_questions_used", 0))

    if _is_no_questions(transcript) or used >= MAX_OUTRO_QUESTIONS:
        set_voice_field(session_id, "interview_phase", "done")
        sign_off = CLOSING_SIGN_OFF.format(name=name)
        append_transcript_turn(session_id, "bot", sign_off, entry_type="closing")
        asyncio.create_task(_trigger_final_evaluation(session_id))
        return sign_off

    # Candidate asked something — record it, answer ONLY from job context.
    append_transcript_turn(session_id, "candidate", transcript, entry_type="wrap_up_question")
    job_role = voice_data.get("job_role", "")
    try:
        jd_summary = json.loads(voice_data.get("jd_summary", "{}"))
    except json.JSONDecodeError:
        jd_summary = {}
    # answer_candidate_question uses the SYNC Anthropic client; run it off the event
    # loop so a wrap-up LLM round-trip doesn't stall other concurrent voice sessions.
    reply = await asyncio.to_thread(answer_candidate_question, transcript, job_role, jd_summary)
    set_voice_field(session_id, "outro_questions_used", used + 1)
    append_transcript_turn(session_id, "bot", reply, entry_type="wrap_up")
    return f"{reply} Anything else you'd like to ask?"


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

    if voice_data.get("interview_phase") == "wrap_up":
        return await _handle_wrap_up_turn(session_id, transcript, voice_data)

    questions_raw: list[dict] = json.loads(voice_data.get("questions", "[]"))
    if not questions_raw:
        return "I'm having trouble loading the questions. Let's continue."

    questions = [Question(**q) for q in questions_raw]
    current_idx = int(voice_data.get("current_question_idx", 0))

    if current_idx >= len(questions):
        return _enter_wrap_up(session_id, voice_data)

    current_q = questions[current_idx]

    # Record the candidate's answer in the transcript before calling the LLM.
    # Tagging with question_id allows deterministic Q/A extraction during evaluation
    # rather than relying on the LLM to infer the mapping from transcript position.
    append_transcript_turn(
        session_id,
        "candidate",
        transcript,
        entry_type="candidate",
        question_id=current_q.id,
    )

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

    # Persist score to Redis — suppress if LLM confidence is too low to trust
    if parsed.score is not None and parsed.score_topic:
        if parsed.confidence is not None and parsed.confidence < LOW_CONFIDENCE_THRESHOLD:
            logger.warning(
                "Score suppressed (low confidence) session=%s topic=%s score=%.1f confidence=%.2f",
                session_id, parsed.score_topic, parsed.score, parsed.confidence,
            )
            increment_voice_field(session_id, "low_confidence_turns")
        else:
            scores: dict[str, float] = json.loads(
                voice_data.get("running_scores", "{}")
            )
            scores[parsed.score_topic] = parsed.score
            set_voice_field(session_id, "running_scores", json.dumps(scores))
            logger.info(
                "Score recorded session=%s topic=%s score=%.1f",
                session_id, parsed.score_topic, parsed.score,
            )
            if parsed.confidence is not None:
                llm_confs: dict[str, float] = json.loads(
                    voice_data.get("llm_confidence_by_topic", "{}")
                )
                llm_confs[parsed.score_topic] = parsed.confidence
                set_voice_field(session_id, "llm_confidence_by_topic", json.dumps(llm_confs))

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
            return _enter_wrap_up(
                session_id,
                voice_data,
                lead_in=_acknowledgment_only(parsed.spoken_text) or "Great, thank you.",
            )

        next_q = questions[next_idx]
        append_transcript_turn(session_id, "bot", next_q.question_text, entry_type="question")
        spoken = _acknowledgment_only(parsed.spoken_text) or "Thank you."
        return f"{spoken} {next_q.question_text}"

    elif action == "follow_up":
        set_voice_field(session_id, "follow_up_count", follow_up_count + 1)
        fu_by_topic: dict[str, int] = json.loads(
            voice_data.get("follow_ups_by_topic", "{}")
        )
        fu_by_topic[current_q.topic] = fu_by_topic.get(current_q.topic, 0) + 1
        set_voice_field(session_id, "follow_ups_by_topic", json.dumps(fu_by_topic))
        # A follow-up's spoken_text IS the question, but the model sometimes packs
        # two into it; reduce to a single question before it reaches the candidate.
        follow_up_text = validate_single_question(parsed.spoken_text) or "Could you tell me more about that?"
        append_transcript_turn(session_id, "bot", follow_up_text, entry_type="follow_up")
        return follow_up_text

    else:
        # Default: acknowledge and move on
        next_idx = current_idx + 1
        set_voice_field(session_id, "current_question_idx", next_idx)
        set_voice_field(session_id, "follow_up_count", 0)

        if next_idx >= len(questions):
            return _enter_wrap_up(
                session_id,
                voice_data,
                lead_in=_acknowledgment_only(parsed.spoken_text) or "Thank you.",
            )

        next_q = questions[next_idx]
        append_transcript_turn(session_id, "bot", next_q.question_text, entry_type="question")
        spoken = _acknowledgment_only(parsed.spoken_text) or "Thank you."
        return f"{spoken} {next_q.question_text}"


async def _trigger_final_evaluation(session_id: str) -> None:
    """
    Hand off to the voice evaluation pipeline.
    Computes metrics, runs LLM evaluation, persists to PG + Redis.
    """
    logger.info("Triggering voice evaluation for session %s", session_id)
    try:
        from src.services.interview.voice_evaluation import finalize_voice_session
        await finalize_voice_session(session_id)
    except Exception as exc:
        logger.error(
            "Voice evaluation failed session=%s: %s", session_id, exc
        )
        set_voice_field(session_id, "state", "COMPLETE")
