"""
WebSocket gateway for voice interviews.

Route:   /ws/interview/voice/{session_id}?token=<jwt>
Binary frames  → PCM audio (forwarded to Deepgram pipeline)
JSON frames    → control messages
Heartbeat      → 30s ping
Disconnect     → pause Redis state, never destroy
"""

import asyncio
import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt

from src.lib.settings import get_settings
from src.services.audio.deepgram_client import DeepgramManager
from src.services.audio.voice_session import (
    get_voice_session,
    increment_voice_field,
    pause_voice_session,
    resume_voice_session,
    set_voice_field,
    append_transcript_turn,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["voice"])

HEARTBEAT_INTERVAL = 30
ALGORITHM = "HS256"

STT_LOW_CONFIDENCE = 0.50   # below this: ask candidate to repeat
STT_MID_CONFIDENCE = 0.70   # below this: soft-confirm before processing
MAX_REPEAT_REQUESTS = 1     # after this many consecutive low-confidence events, process anyway

DEBOUNCE_SECS = 3.0          # seconds to wait after last speech_final before flushing to LLM
DEBOUNCE_COMPLETE_SECS = 0.8 # shortened debounce when user signals answer completion
DEBOUNCE_INCOMPLETE_SECS = 5.0  # extended debounce when transcript ends mid-thought

_COMPLETION_PHRASES = (
    "that's my answer",
    "that's all",
    "that's it",
    "that's about it",
    "i think that covers it",
    "i don't have anything else",
    "i don't have anything more",
    "nothing else to add",
    "that's everything",
    "i'm done",
    "that would be all",
    "yeah that's it",
    "yes that's it",
    "i guess that's it",
    "i believe that's it",
    "that's all i can think of",
    "that's all i have",
    "that is what i think",
    "sorry i may not know",
    "i may not know",
)

_INCOMPLETE_TRAILING = (
    " and", " but", " or", " so", " because", " since", " although",
    " however", " therefore", " which", " that", " when", " where",
    " while", " if", " as", " with", " for", " to", " of", " the",
    " a", " an", " like", " such", " also", " then",
)


def _looks_complete(text: str) -> bool:
    """Rule-based: does the transcript end with an explicit completion phrase?"""
    lower = text.lower().strip()
    return any(lower.endswith(phrase) or phrase in lower[-60:] for phrase in _COMPLETION_PHRASES)


def _looks_incomplete(text: str) -> bool:
    """Rule-based: does the transcript trail off with a conjunction/preposition/article?"""
    lower = text.lower().rstrip(" .,;:")
    return any(lower.endswith(trail) for trail in _INCOMPLETE_TRAILING)


def _validate_token(token: str, session_id: str) -> dict[str, Any]:
    settings = get_settings()
    payload: dict[str, Any] = jwt.decode(
        token, settings.jwt_secret, algorithms=[ALGORITHM]
    )
    if payload.get("session_id") != session_id:
        raise JWTError("session_id mismatch")
    return payload


async def _send_json(ws: WebSocket, data: dict[str, Any]) -> None:
    try:
        await ws.send_json(data)
    except Exception:
        pass


async def _heartbeat_loop(ws: WebSocket, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        if stop.is_set():
            break
        try:
            await _send_json(ws, {"event": "ping"})
        except Exception:
            break


@router.websocket("/ws/interview/voice/{session_id}")
async def voice_interview_ws(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(default=""),
) -> None:
    settings = get_settings()

    # JWT validation on upgrade
    if token:
        try:
            _validate_token(token, session_id)
        except JWTError as exc:
            logger.warning("WS auth failed session=%s: %s", session_id, exc)
            await websocket.close(code=1008)
            return
    else:
        if settings.environment != "development":
            await websocket.close(code=1008)
            return

    session = get_voice_session(session_id)
    if session is None:
        logger.warning("No voice session found: %s", session_id)
        await websocket.close(code=4404)
        return

    await websocket.accept()
    resume_voice_session(session_id)
    logger.info("Voice WS connected session=%s", session_id)

    # Per-connection state
    deepgram: Optional[DeepgramManager] = None
    stop_event = asyncio.Event()
    accumulated_text: list[str] = []
    debounce_task: list[Optional[asyncio.Task]] = [None]  # list for mutability in closure
    soft_confirm_pending: list[bool] = [False]
    repeat_request_count: list[int] = [0]

    async def on_transcript(text: str, is_final: bool, confidence: float = 1.0) -> None:
        """Called by Deepgram on every transcript event."""
        # Transcript consistency: every text-bearing WebSocket event has a corresponding
        # Redis write via append_transcript_turn(). Mapping:
        #   transcript (is_final=True, high conf)  -> flush_accumulated -> run_llm_turn -> append "candidate" with question_id
        #   transcript (is_final=True, mid conf)   -> soft_confirm      -> append "soft_confirm"
        #   transcript (is_final=True, low conf)   -> repeat_request    -> append "repeat_request"
        #   interviewer_prompt                     -> silence monitor   -> append "silence_prompt"
        #   turn (speaker=bot, w/text)             -> stream_response   -> append "response"/"question"/"follow_up"
        #   turn (speaker=candidate)               -> state signal only, no text, no persistence needed
        # NOTE: candidate answers are appended inside run_llm_turn (with question_id)
        #       rather than here, so each answer is correctly tagged to its question.

        # Always send to client immediately for live display
        await _send_json(websocket, {
            "event": "transcript",
            "text": text,
            "is_final": is_final,
            "type": "candidate",
            "confidence": round(confidence, 3),
        })

        if is_final:
            if soft_confirm_pending[0]:
                # Candidate responded after a soft-confirm — accept regardless of confidence
                soft_confirm_pending[0] = False
                repeat_request_count[0] = 0
            elif confidence < STT_LOW_CONFIDENCE:
                if repeat_request_count[0] >= MAX_REPEAT_REQUESTS:
                    # Give up retrying — process with what was transcribed
                    logger.warning(
                        "Max repeat requests reached session=%s — processing low-confidence transcript",
                        session_id,
                    )
                    repeat_request_count[0] = 0
                    # Fall through to accumulate+debounce
                else:
                    repeat_request_count[0] += 1
                    increment_voice_field(session_id, "low_confidence_retries")
                    await _stream_bot_message(
                        websocket, session_id,
                        "I'm sorry, I didn't catch that clearly. Could you please repeat your answer?",
                        "repeat_request",
                    )
                    return
            elif confidence < STT_MID_CONFIDENCE:
                soft_confirm_pending[0] = True
                increment_voice_field(session_id, "soft_confirm_count")
                await _stream_bot_message(
                    websocket, session_id,
                    f"Just to make sure I heard you correctly — you said: {text}?",
                    "soft_confirm",
                )
                return
            else:
                repeat_request_count[0] = 0

            accumulated_text.append(text)

            # Cancel existing debounce timer
            if debounce_task[0] is not None and not debounce_task[0].done():
                debounce_task[0].cancel()

            # Adaptive debounce: fast for completion phrases, slow for incomplete trailing
            current_text = " ".join(accumulated_text)
            if _looks_complete(current_text):
                flush_delay = DEBOUNCE_COMPLETE_SECS
            elif _looks_incomplete(current_text):
                flush_delay = DEBOUNCE_INCOMPLETE_SECS
            else:
                flush_delay = DEBOUNCE_SECS

            async def _flush_accumulated(delay: float = flush_delay) -> None:
                await asyncio.sleep(delay)
                if not accumulated_text:
                    return
                full_text = " ".join(accumulated_text)
                accumulated_text.clear()
                # Candidate answer is stored inside run_llm_turn with question_id;
                # do not append here to avoid duplicating the turn.
                set_voice_field(session_id, "state", "PROCESSING")
                increment_voice_field(session_id, "turn_count")
                await _process_turn(websocket, session_id, full_text)

            debounce_task[0] = asyncio.create_task(_flush_accumulated())

    # Start Deepgram connection
    deepgram = DeepgramManager(session_id=session_id, on_transcript=on_transcript)
    await deepgram.start()

    heartbeat_task = asyncio.create_task(_heartbeat_loop(websocket, stop_event))

    await _send_json(websocket, {
        "event": "connected",
        "session_id": session_id,
        "state": session.get("state", "INITIALIZING"),
    })

    # Send full transcript for reconnect recovery
    transcript_raw = json.loads(session.get("transcript", "[]"))
    if transcript_raw:
        await _send_json(websocket, {
            "event": "transcript_sync",
            "transcript": transcript_raw,
        })

    # Deliver first question via TTS on initial connect
    if session.get("state") == "WAITING_FOR_CANDIDATE":
        first_q_entries = [
            t for t in transcript_raw
            if t.get("speaker") == "bot" and t.get("type") == "question"
        ]
        if first_q_entries:
            from src.services.interview.voice_turn_processor import get_or_create_turn_state
            turn_state = get_or_create_turn_state(session_id, websocket)
            await turn_state.stream_response(
                first_q_entries[0]["text"], entry_type="question",
            )

    try:
        while True:
            message = await websocket.receive()

            if "bytes" in message and message["bytes"]:
                await deepgram.send(message["bytes"])

            elif "text" in message and message["text"]:
                try:
                    data = json.loads(message["text"])
                except json.JSONDecodeError:
                    await _send_json(websocket, {
                        "event": "error",
                        "message": "Invalid JSON in control frame",
                    })
                    continue
                await _handle_control(websocket, session_id, data, debounce_task)

    except WebSocketDisconnect:
        logger.info("Voice WS disconnected session=%s", session_id)
    except Exception as exc:
        logger.error("Voice WS error session=%s: %s", session_id, exc, exc_info=True)
    finally:
        stop_event.set()
        heartbeat_task.cancel()
        if debounce_task[0] is not None and not debounce_task[0].done():
            debounce_task[0].cancel()
        if deepgram:
            await deepgram.stop()
        pause_voice_session(session_id)
        logger.info("Voice WS paused session=%s", session_id)


async def _handle_control(
    ws: WebSocket,
    session_id: str,
    data: dict[str, Any],
    debounce_task: list[Optional[asyncio.Task]] = None,  # type: ignore[type-arg]
) -> None:
    event = data.get("event", "")

    if event == "pong":
        return

    elif event == "speech_start":
        set_voice_field(session_id, "state", "CANDIDATE_SPEAKING")
        # Cancel debounce: user resumed speaking, don't flush yet
        if debounce_task and debounce_task[0] is not None and not debounce_task[0].done():
            debounce_task[0].cancel()
        # Cancel silence monitor: user is actively speaking
        from src.services.interview.voice_turn_processor import get_or_create_turn_state
        turn_state = get_or_create_turn_state(session_id, ws)
        turn_state.cancel_silence_monitor()
        await _send_json(ws, {"event": "ack", "for": "speech_start"})

    elif event == "speech_end":
        # Don't set PROCESSING — let the debounce timer decide when processing starts.
        await _send_json(ws, {"event": "ack", "for": "speech_end"})

    elif event == "tts_complete":
        set_voice_field(session_id, "state", "WAITING_FOR_CANDIDATE")
        await _send_json(ws, {"event": "turn", "speaker": "candidate"})

    elif event == "barge_in_ack":
        increment_voice_field(session_id, "barge_in_count")

    elif event == "end_session":
        await _send_json(ws, {"event": "session_ending"})
        set_voice_field(session_id, "state", "COMPLETE")
        await ws.close(code=1000)


async def _process_turn(ws: WebSocket, session_id: str, transcript: str) -> None:
    """
    Orchestrate one full interview turn: transcript → LLM → TTS.
    LLM and TTS are wired in Features [6] and [9].
    For now: echo back a placeholder response.
    """
    from src.services.interview.voice_turn_processor import process_voice_turn
    try:
        await process_voice_turn(ws=ws, session_id=session_id, transcript=transcript)
    except Exception as exc:
        logger.error("Turn processing failed session=%s: %s", session_id, exc)
        await _send_json(ws, {
            "event": "error",
            "message": "I had trouble processing that. Could you repeat?",
        })
        set_voice_field(session_id, "state", "WAITING_FOR_CANDIDATE")


async def _stream_bot_message(
    ws: WebSocket, session_id: str, text: str, entry_type: str
) -> None:
    """Stream a bot message through TTS without an LLM call (used for repeat/soft-confirm)."""
    from src.services.interview.voice_turn_processor import get_or_create_turn_state
    turn_state = get_or_create_turn_state(session_id, ws)
    await turn_state.stream_response(text, entry_type=entry_type)
