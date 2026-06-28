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
import re
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt

from src.lib.settings import get_settings
from src.services.audio.deepgram_client import DeepgramManager
from src.services.audio.voice_session import (
    append_transcript_turn,
    get_voice_session,
    increment_voice_field,
    pause_voice_session,
    resume_voice_session,
    set_voice_field,
)
from src.services.interview.voice_evaluation import finalize_voice_session

logger = logging.getLogger(__name__)
router = APIRouter(tags=["voice"])

HEARTBEAT_INTERVAL = 30
SESSION_MAX_DURATION_SECS = 45 * 60  # 45 minutes hard cap
MAX_CONSECUTIVE_SILENCE_STRIKES = 3
ALGORITHM = "HS256"

STT_LOW_CONFIDENCE = 0.50   # below this: ask candidate to repeat
STT_MID_CONFIDENCE = 0.70   # below this: soft-confirm before processing
MAX_REPEAT_REQUESTS = 1     # after this many consecutive low-confidence events, process anyway

DEBOUNCE_SECS = 2.0          # seconds to wait after last speech_final before flushing to LLM
DEBOUNCE_COMPLETE_SECS = 0.8 # shortened debounce when user signals answer completion
DEBOUNCE_INCOMPLETE_SECS = 5.0  # extended debounce when transcript ends mid-thought
SPEECH_END_FINAL_GRACE_SECS = 0.35  # let Deepgram deliver a final event before fallback
WAIT_REQUEST_ACK = "Of course, take your time. I'll be here when you're ready."

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

_WAIT_REQUEST_PATTERNS = (
    re.compile(
        r"\b(?:give|grant)\s+me\s+(?:\w+\s+){0,4}"
        r"(?:second|seconds|minute|minutes|moment|moments|time)\b"
    ),
    re.compile(
        r"\b(?:can|could|may)\s+i\s+(?:have|get|take)\s+(?:\w+\s+){0,4}"
        r"(?:second|seconds|minute|minutes|moment|moments|time)\b"
    ),
    re.compile(
        r"\bi\s+need\s+(?:\w+\s+){0,4}"
        r"(?:second|seconds|minute|minutes|moment|moments|time)\b"
    ),
    re.compile(r"\b(?:let|allow)\s+me\s+(?:think|form|collect|gather|prepare|structure)\b"),
    re.compile(r"\b(?:one|a)\s+(?:second|minute|moment)\b"),
    re.compile(r"\bhold\s+on\b"),
)


def _looks_complete(text: str) -> bool:
    """Rule-based: does the transcript end with an explicit completion phrase?"""
    lower = text.lower().strip()
    return any(lower.endswith(phrase) or phrase in lower[-60:] for phrase in _COMPLETION_PHRASES)


def _looks_incomplete(text: str) -> bool:
    """Rule-based: does the transcript trail off with a conjunction/preposition/article?"""
    lower = text.lower().rstrip(" .,;:")
    return any(lower.endswith(trail) for trail in _INCOMPLETE_TRAILING)


def _looks_wait_request(text: str) -> bool:
    """Rule-based: did the candidate ask for thinking time instead of answering?"""
    lower = re.sub(r"\s+", " ", text.lower()).strip(" .,;:?!")
    if not lower:
        return False
    return any(pattern.search(lower) for pattern in _WAIT_REQUEST_PATTERNS)


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


async def _session_timeout(ws: WebSocket, session_id: str, stop: asyncio.Event) -> None:
    """Hard cap on session duration — closes the WS after SESSION_MAX_DURATION_SECS."""
    await asyncio.sleep(SESSION_MAX_DURATION_SECS)
    if stop.is_set():
        return
    logger.warning("Session max duration reached session=%s — force closing", session_id)
    stop.set()
    await _send_json(ws, {
        "event": "error",
        "message": "Interview session timed out (45 minute limit).",
    })
    try:
        await ws.close(code=1000)
    except Exception:
        pass


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
    latest_interim_text: list[str] = [""]
    promoted_interim_text: list[str] = [""]
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

        if not is_final:
            latest_interim_text[0] = text.strip()
            return

        if is_final:
            promoted_text = promoted_interim_text[0]
            if promoted_text and (
                text == promoted_text
                or text.startswith(promoted_text)
                or promoted_text.startswith(text)
            ):
                latest_interim_text[0] = ""
                promoted_interim_text[0] = ""
                return
            latest_interim_text[0] = ""
            promoted_interim_text[0] = ""

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
            if _looks_wait_request(current_text):
                accumulated_text.clear()
                await _handle_wait_request(websocket, session_id, current_text)
                return

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

    async def flush_accumulated_now() -> None:
        if SPEECH_END_FINAL_GRACE_SECS > 0:
            await asyncio.sleep(SPEECH_END_FINAL_GRACE_SECS)

        if debounce_task[0] is not None and not debounce_task[0].done():
            try:
                if accumulated_text:
                    debounce_task[0].cancel()
                    await debounce_task[0]
                else:
                    await debounce_task[0]
                    return
            except asyncio.CancelledError:
                pass

        if not accumulated_text and latest_interim_text[0]:
            accumulated_text.append(latest_interim_text[0])
            promoted_interim_text[0] = latest_interim_text[0]
            latest_interim_text[0] = ""
        elif accumulated_text:
            promoted_interim_text[0] = ""

        if not accumulated_text:
            return

        full_text = " ".join(accumulated_text)
        accumulated_text.clear()
        set_voice_field(session_id, "state", "PROCESSING")
        increment_voice_field(session_id, "turn_count")
        logger.info("Flushing buffered transcript before finalization session=%s", session_id)
        await _process_turn(websocket, session_id, full_text)

    # Start Deepgram connection
    deepgram = DeepgramManager(session_id=session_id, on_transcript=on_transcript)
    await deepgram.start()

    heartbeat_task = asyncio.create_task(_heartbeat_loop(websocket, stop_event))
    timeout_task = asyncio.create_task(_session_timeout(websocket, session_id, stop_event))

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

    # Deliver the opening turns via TTS on initial connect: every leading bot
    # turn up to and including the first "question" turn. Only the final turn
    # signals the candidate's turn / starts the silence monitor.
    if session.get("state") == "WAITING_FOR_CANDIDATE":
        leading: list[dict] = []
        for t in transcript_raw:
            if t.get("speaker") != "bot":
                break
            leading.append(t)
            if t.get("type") == "question":
                break
        if leading:
            from src.services.interview.voice_turn_processor import get_or_create_turn_state
            turn_state = get_or_create_turn_state(session_id, websocket)
            for i, entry in enumerate(leading):
                is_last = i == len(leading) - 1
                await turn_state.stream_response(
                    entry["text"],
                    entry_type=entry.get("type", "question"),
                    signal_turn_end=is_last,
                )

    try:
        while True:
            message = await websocket.receive()
            message_type = message.get("type")

            if message_type == "websocket.disconnect":
                logger.info(
                    "Voice WS disconnect message session=%s code=%s",
                    session_id,
                    message.get("code"),
                )
                break

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
                should_continue = await _handle_control(
                    websocket,
                    session_id,
                    data,
                    debounce_task,
                    flush_accumulated_now,
                )
                if not should_continue:
                    break

    except WebSocketDisconnect:
        logger.info("Voice WS disconnected session=%s", session_id)
    except Exception as exc:
        logger.error("Voice WS error session=%s: %s", session_id, exc, exc_info=True)
    finally:
        from src.services.interview.voice_turn_processor import remove_turn_state

        stop_event.set()
        heartbeat_task.cancel()
        timeout_task.cancel()
        if debounce_task[0] is not None and not debounce_task[0].done():
            debounce_task[0].cancel()
        if deepgram:
            await deepgram.stop()
        remove_turn_state(session_id)
        pause_voice_session(session_id)
        logger.info("Voice WS connection cleanup finished session=%s", session_id)


async def _handle_control(
    ws: WebSocket,
    session_id: str,
    data: dict[str, Any],
    debounce_task: list[Optional[asyncio.Task]] = None,  # type: ignore[type-arg]
    flush_accumulated_now: Optional[Callable[[], Awaitable[None]]] = None,
) -> bool:
    event = data.get("event", "")

    if event == "pong":
        return True

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
        return True

    elif event == "speech_end":
        # Don't set PROCESSING — let the debounce timer decide when processing starts.
        if flush_accumulated_now is not None:
            await flush_accumulated_now()
        await _send_json(ws, {"event": "ack", "for": "speech_end"})
        return True

    elif event == "tts_complete":
        from src.services.interview.voice_turn_processor import get_or_create_turn_state

        turn_state = get_or_create_turn_state(session_id, ws)
        turn_state.open_candidate_turn_after_playback()
        await _send_json(ws, {"event": "turn", "speaker": "candidate"})
        return True

    elif event == "barge_in_ack":
        increment_voice_field(session_id, "barge_in_count")
        return True

    elif event == "end_session":
        logger.info("Voice session end requested session=%s", session_id)
        await _send_json(ws, {"event": "session_ending"})
        if flush_accumulated_now is not None:
            await flush_accumulated_now()
        await _send_json(ws, {"event": "evaluating"})
        report = await finalize_voice_session(session_id)
        if report is None:
            logger.error("Voice session finalization failed session=%s", session_id)
            await _send_json(ws, {
                "event": "error",
                "message": "Interview finalization failed before the report was ready.",
            })
            return False
        logger.info("Voice session finalized before close session=%s", session_id)
        await _send_json(ws, {
            "event": "interview_complete",
            "report_url": f"/report/{session_id}",
        })
        await ws.close(code=1000)
        return False

    return True


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


async def _handle_wait_request(ws: WebSocket, session_id: str, transcript: str) -> None:
    """A thinking-time request is not an answer and must not enter scoring.

    After the ack, the candidate is back in thinking mode. The wait-ack path
    does NOT go through the browser tts_complete handshake, so we must start
    the silence monitor here directly (in grace mode so the first nudge is
    delayed). Without this a candidate who stays silent after asking for time
    would sit in dead air forever.
    """
    from src.services.interview.voice_turn_processor import get_or_create_turn_state

    turn_state = get_or_create_turn_state(session_id, ws)
    turn_state.cancel_silence_monitor()
    append_transcript_turn(session_id, "candidate", transcript, entry_type="wait_request")

    await turn_state.stream_response(
        WAIT_REQUEST_ACK,
        entry_type="wait_ack",
        signal_turn_end=False,
    )
    set_voice_field(session_id, "state", "WAITING_FOR_CANDIDATE")
    await _send_json(ws, {"event": "turn", "speaker": "candidate"})

    # Start silence monitor in grace mode so the candidate gets SILENCE_GRACE_SECS
    # before the first nudge fires. Also write the flag so that if the connection
    # re-establishes (tts_complete arrives), open_candidate_turn_after_playback
    # also picks up grace mode.
    set_voice_field(session_id, "silence_grace_pending", "1")
    turn_state._start_silence_monitor(grace=True)


async def _stream_bot_message(
    ws: WebSocket, session_id: str, text: str, entry_type: str
) -> None:
    """Stream a bot message through TTS without an LLM call (used for repeat/soft-confirm)."""
    from src.services.interview.voice_turn_processor import get_or_create_turn_state
    turn_state = get_or_create_turn_state(session_id, ws)
    await turn_state.stream_response(text, entry_type=entry_type)
