"""
Voice turn processor — orchestrates one full voice interview turn.

Per-session state:
  bot_speaking          : bool  — True while TTS is streaming
  current_tts_task      : asyncio.Task | None

Barge-in: speech during bot_speaking → cancel TTS, send stop signal.

Silence timeouts (managed via _silence_monitor):
  15s  → gentle prompt
  30s  → "Are you still there?"
  45s  → silence_strike++, advance question
"""

import asyncio
import logging
from typing import Any, Optional

from fastapi import WebSocket

from src.services.audio.tts_client import ElevenLabsTTS, split_into_sentences
from src.services.audio.voice_session import (
    get_voice_session,
    increment_voice_field,
    set_voice_field,
    append_transcript_turn,
)

logger = logging.getLogger(__name__)

SILENCE_PROMPT_SECS = 15
SILENCE_CHECKIN_SECS = 30
SILENCE_STRIKE_SECS = 45


class VoiceTurnState:
    """Per-connection mutable state (lives for the lifetime of a WS connection)."""

    def __init__(self, session_id: str, ws: WebSocket) -> None:
        self.session_id = session_id
        self.ws = ws
        self.bot_speaking = False
        self.current_tts_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
        self.tts = ElevenLabsTTS()
        self._silence_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
        self._silence_prompt_count = 0

    async def handle_barge_in(self) -> None:
        """Cancel current TTS and open mic."""
        if not self.bot_speaking:
            return

        if self.current_tts_task and not self.current_tts_task.done():
            self.current_tts_task.cancel()

        self.bot_speaking = False

        await _send_json(self.ws, {"event": "barge_in", "action": "stop_tts"})
        await _send_json(self.ws, {"event": "turn", "speaker": "candidate"})

        increment_voice_field(self.session_id, "barge_in_count")
        logger.info("Barge-in detected session=%s", self.session_id)

    async def stream_response(self, text: str, entry_type: str = "response") -> None:
        """Stream LLM response through TTS sentence by sentence."""
        sentences = split_into_sentences(text)
        if not sentences:
            return

        self.bot_speaking = True
        set_voice_field(self.session_id, "state", "BOT_SPEAKING")
        # Include text so the frontend live transcript can display it immediately
        # without waiting for a reconnect transcript_sync.
        await _send_json(self.ws, {"event": "turn", "speaker": "bot", "type": entry_type, "text": text})
        append_transcript_turn(self.session_id, "bot", text, entry_type=entry_type)

        async def _play() -> None:
            # Stream sentences strictly one at a time. Streaming them
            # concurrently interleaves each sentence's MP3 bytes on the single
            # WebSocket, producing garbled audio on the client.
            for sentence in sentences:
                await self.tts.stream_sentence(sentence, self.ws)

        # Run playback as its own task so handle_barge_in (a separate coroutine)
        # can cancel just the TTS without killing the whole turn.
        self.current_tts_task = asyncio.create_task(_play())
        try:
            await self.current_tts_task
        except asyncio.CancelledError:
            # Barge-in cancelled playback; the mic was already reopened in
            # handle_barge_in, so just unwind without surfacing the cancel.
            logger.debug("TTS stream cancelled by barge-in session=%s", self.session_id)
            return
        finally:
            self.bot_speaking = False
            self.current_tts_task = None

        # Check if evaluation completed (state set to COMPLETE by evaluation pipeline)
        session_data = get_voice_session(self.session_id)
        if session_data and session_data.get("state") == "COMPLETE":
            await _send_json(self.ws, {
                "event": "interview_complete",
                "report_url": f"/report/{self.session_id}",
            })
            return

        set_voice_field(self.session_id, "state", "WAITING_FOR_CANDIDATE")
        await _send_json(self.ws, {"event": "turn", "speaker": "candidate"})
        self._start_silence_monitor()

    def _start_silence_monitor(self) -> None:
        if self._silence_task and not self._silence_task.done():
            self._silence_task.cancel()
        self._silence_task = asyncio.create_task(self._silence_monitor())

    def cancel_silence_monitor(self) -> None:
        if self._silence_task and not self._silence_task.done():
            self._silence_task.cancel()
        self._silence_task = None
        self._silence_prompt_count = 0

    async def _silence_monitor(self) -> None:
        try:
            await asyncio.sleep(SILENCE_PROMPT_SECS)
            if self._silence_prompt_count < 2:
                await _send_json(self.ws, {
                    "event": "interviewer_prompt",
                    "text": "Take your time, I'm listening.",
                    "type": "silence_prompt",
                })
                append_transcript_turn(self.session_id, "bot", "Take your time, I'm listening.", entry_type="silence_prompt")
                self._silence_prompt_count += 1

            await asyncio.sleep(SILENCE_CHECKIN_SECS - SILENCE_PROMPT_SECS)
            if self._silence_prompt_count < 2:
                await _send_json(self.ws, {
                    "event": "interviewer_prompt",
                    "text": "Are you still there?",
                    "type": "silence_prompt",
                })
                append_transcript_turn(self.session_id, "bot", "Are you still there?", entry_type="silence_prompt")
                self._silence_prompt_count += 1

            await asyncio.sleep(SILENCE_STRIKE_SECS - SILENCE_CHECKIN_SECS)
            strikes = increment_voice_field(self.session_id, "silence_strikes")
            logger.info("Silence strike %d session=%s", strikes, self.session_id)

            await _send_json(self.ws, {
                "event": "silence_strike",
                "count": strikes,
                "action": "advance_question",
            })

        except asyncio.CancelledError:
            pass


# ---- Module-level per-session state registry ----
_sessions: dict[str, VoiceTurnState] = {}


def get_or_create_turn_state(session_id: str, ws: WebSocket) -> VoiceTurnState:
    if session_id not in _sessions:
        _sessions[session_id] = VoiceTurnState(session_id, ws)
    else:
        # Update WS reference on reconnect
        _sessions[session_id].ws = ws
    return _sessions[session_id]


def remove_turn_state(session_id: str) -> None:
    state = _sessions.pop(session_id, None)
    if state:
        state.cancel_silence_monitor()


async def process_voice_turn(
    ws: WebSocket,
    session_id: str,
    transcript: str,
) -> None:
    """
    Called when speech_final=True.
    Runs: Redis session → LLM → TTS → state update.
    Scores → Redis; session end → existing evaluation pipeline.

    LLM orchestration is wired in Feature [9].
    """
    turn_state = get_or_create_turn_state(session_id, ws)
    turn_state.cancel_silence_monitor()

    # Handle barge-in: if bot was speaking when speech detected
    if turn_state.bot_speaking:
        await turn_state.handle_barge_in()

    session_data = get_voice_session(session_id)
    if session_data is None:
        await _send_json(ws, {"event": "error", "message": "Session not found."})
        return

    # Delegate to LLM orchestration (Feature [9] wires this in)
    from src.services.interview.voice_llm_orchestrator import run_llm_turn
    try:
        response_text = await run_llm_turn(session_id=session_id, transcript=transcript)
    except Exception as exc:
        logger.error("LLM turn failed session=%s: %s", session_id, exc)
        response_text = "I'm having a moment. Could you please repeat that?"

    # Stream response through TTS
    await turn_state.stream_response(response_text)


async def _send_json(ws: WebSocket, data: dict[str, Any]) -> None:
    try:
        await ws.send_json(data)
    except Exception:
        pass
