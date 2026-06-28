"""
Voice turn processor — orchestrates one full voice interview turn.

Per-session state:
  bot_speaking          : bool  — True while TTS is streaming
  current_tts_task      : asyncio.Task | None

Barge-in: speech during bot_speaking → cancel TTS, send stop signal.

Silence timeouts (managed via _silence_monitor, all spoken via TTS):
  12s  → gentle nudge ("Take your time…")
  30s  → check-in ("Are you still there?…")
  60s  → silence_strike++, then deterministically advance to the next question
         (or enter wrap-up if none remain) — no LLM call
"""

import asyncio
import json
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
from src.types.interview import Question

logger = logging.getLogger(__name__)

SILENCE_PROMPT_SECS = 12    # first gentle nudge
SILENCE_CHECKIN_SECS = 30   # "are you still there" check-in
SILENCE_STRIKE_SECS = 60    # strike + advance to next question
SILENCE_GRACE_SECS = 30     # accept-thinking grace — delays first nudge when set
COMPLETION_WAIT_TIMEOUT_SECS = 90.0
COMPLETION_POLL_INTERVAL_SECS = 0.25
MAX_CONSECUTIVE_SILENCE_STRIKES = 3

# Spoken silence check-ins (deterministic — never LLM-generated).
SILENCE_PROMPT_1 = "Take your time — I'm here whenever you're ready."
SILENCE_PROMPT_2 = "Are you still there? No rush — take all the time you need."
SILENCE_ADVANCE = "No problem — let's move on to the next question."


async def _wait_for_report_ready(session_id: str) -> bool:
    deadline = asyncio.get_running_loop().time() + COMPLETION_WAIT_TIMEOUT_SECS
    while asyncio.get_running_loop().time() < deadline:
        session_data = get_voice_session(session_id)
        if session_data is None:
            return False
        if session_data.get("evaluation_report"):
            return True
        state = session_data.get("state")
        if state not in {"EVALUATING", "COMPLETE"}:
            return False
        await asyncio.sleep(COMPLETION_POLL_INTERVAL_SECS)
    return False


class VoiceTurnState:
    """Per-connection mutable state (lives for the lifetime of a WS connection)."""

    def __init__(self, session_id: str, ws: WebSocket) -> None:
        self.session_id = session_id
        self.ws = ws
        self.bot_speaking = False
        self.current_tts_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
        self.tts = ElevenLabsTTS()
        self._silence_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
        self._silence_advance_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

    def _track_task(self, attr_name: str, task: asyncio.Task) -> asyncio.Task:
        setattr(self, attr_name, task)

        def _clear(done_task: asyncio.Task) -> None:
            if getattr(self, attr_name) is done_task:
                setattr(self, attr_name, None)

        task.add_done_callback(_clear)
        return task

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

    async def stream_response(
        self, text: str, entry_type: str = "response", signal_turn_end: bool = True
    ) -> None:
        """Stream LLM response through TTS sentence by sentence.

        signal_turn_end=False is used for non-final opening turns (e.g. the intro
        spoken just before the first question) so they don't prematurely open the
        mic or start the silence monitor.
        """
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

        # Final interview turns stay in evaluation mode until the durable report exists.
        session_data = get_voice_session(self.session_id)
        if session_data and session_data.get("state") == "COMPLETE":
            await _send_json(self.ws, {
                "event": "interview_complete",
                "report_url": f"/report/{self.session_id}",
            })
            return
        if session_data and session_data.get("state") == "EVALUATING":
            await _send_json(self.ws, {"event": "evaluating"})
            if await _wait_for_report_ready(self.session_id):
                await _send_json(self.ws, {
                    "event": "interview_complete",
                    "report_url": f"/report/{self.session_id}",
                })
            else:
                logger.error("Voice report wait timed out session=%s", self.session_id)
                await _send_json(self.ws, {
                    "event": "error",
                    "message": "Interview evaluation is taking longer than expected.",
                })
            return

        if not signal_turn_end:
            return

        await _send_json(self.ws, {"event": "tts_turn_complete"})

    def open_candidate_turn_after_playback(self) -> None:
        """Open the response window after the browser confirms audio playback ended.

        If the voice session has ``silence_grace_pending`` set (written by the
        wait-request path), the silence monitor starts in grace mode so the
        first nudge is delayed by SILENCE_GRACE_SECS instead of SILENCE_PROMPT_SECS.
        The flag is cleared immediately after reading it.
        """
        set_voice_field(self.session_id, "state", "WAITING_FOR_CANDIDATE")
        session_data = get_voice_session(self.session_id)
        grace = bool(session_data and session_data.get("silence_grace_pending"))
        if grace:
            set_voice_field(self.session_id, "silence_grace_pending", "")
        self._start_silence_monitor(grace=grace)

    async def _speak_silence_prompt(self, text: str) -> None:
        """Voice a silence check-in through TTS.

        Streams via the same TTS path as a normal turn so the candidate actually
        HEARS the prompt. Deliberately does NOT restart the silence monitor —
        the single monitor coroutine keeps walking its ladder, and reusing
        stream_response (which restarts the monitor) would cancel the very
        coroutine that called this.
        """
        sentences = split_into_sentences(text)
        if not sentences:
            return

        self.bot_speaking = True
        set_voice_field(self.session_id, "state", "BOT_SPEAKING")
        await _send_json(self.ws, {
            "event": "interviewer_prompt",
            "text": text,
            "type": "silence_prompt",
        })
        append_transcript_turn(self.session_id, "bot", text, entry_type="silence_prompt")
        try:
            for sentence in sentences:
                await self.tts.stream_sentence(sentence, self.ws)
        finally:
            self.bot_speaking = False
            set_voice_field(self.session_id, "state", "WAITING_FOR_CANDIDATE")

    def _start_silence_monitor(self, grace: bool = False) -> None:
        if self._silence_task and not self._silence_task.done():
            self._silence_task.cancel()
        self._track_task("_silence_task", asyncio.create_task(self._silence_monitor(grace=grace)))

    def cancel_silence_monitor(self) -> None:
        if self._silence_task and not self._silence_task.done():
            self._silence_task.cancel()
        if self._silence_advance_task and not self._silence_advance_task.done():
            self._silence_advance_task.cancel()
        self._silence_task = None
        self._silence_advance_task = None

    async def _advance_after_silence(self) -> None:
        """Deterministically advance to the next question after the candidate
        stays silent through the full nudge ladder. No LLM call — mirrors the
        advance branch of run_llm_turn using code only.
        """
        try:
            voice_data = get_voice_session(self.session_id)
            if voice_data is None:
                return

            questions = [Question(**q) for q in json.loads(voice_data.get("questions", "[]"))]
            current_idx = int(voice_data.get("current_question_idx", 0))
            next_idx = current_idx + 1
            set_voice_field(self.session_id, "current_question_idx", next_idx)
            set_voice_field(self.session_id, "follow_up_count", 0)

            # Lazy import: voice_llm_orchestrator imports this module's siblings;
            # importing it at module load risks a circular import (same pattern as
            # the lazy run_llm_turn import in process_voice_turn).
            from src.services.interview.voice_llm_orchestrator import _enter_wrap_up

            if next_idx >= len(questions):
                invite = _enter_wrap_up(self.session_id, voice_data, lead_in=SILENCE_ADVANCE)
                await self.stream_response(invite, entry_type="wrap_up_invite")
                return

            next_q = questions[next_idx]
            append_transcript_turn(
                self.session_id, "bot", next_q.question_text, entry_type="question"
            )
            spoken = f"{SILENCE_ADVANCE} {next_q.question_text}".strip()
            await self.stream_response(spoken)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Silence advance failed session=%s: %s", self.session_id, exc)
            await _send_json(self.ws, {
                "event": "error",
                "message": "I had trouble moving to the next question.",
            })
            set_voice_field(self.session_id, "state", "WAITING_FOR_CANDIDATE")

    async def _silence_monitor(self, grace: bool = False) -> None:
        try:
            first_wait = SILENCE_GRACE_SECS if grace else SILENCE_PROMPT_SECS
            await asyncio.sleep(first_wait)
            await self._speak_silence_prompt(SILENCE_PROMPT_1)

            await asyncio.sleep(SILENCE_CHECKIN_SECS - SILENCE_PROMPT_SECS)
            await self._speak_silence_prompt(SILENCE_PROMPT_2)

            await asyncio.sleep(SILENCE_STRIKE_SECS - SILENCE_CHECKIN_SECS)
            strikes = increment_voice_field(self.session_id, "silence_strikes")
            logger.info("Silence strike %d session=%s", strikes, self.session_id)

            if strikes >= MAX_CONSECUTIVE_SILENCE_STRIKES:
                logger.warning(
                    "Max silence strikes (%d) reached session=%s — auto-ending",
                    strikes, self.session_id,
                )
                await _send_json(self.ws, {
                    "event": "silence_strike",
                    "count": strikes,
                    "action": "end_session",
                })
                await _send_json(self.ws, {
                    "event": "error",
                    "message": "Session ended due to extended inactivity.",
                })
                try:
                    await self.ws.close(code=1000)
                except Exception:
                    pass
                return

            await _send_json(self.ws, {
                "event": "silence_strike",
                "count": strikes,
                "action": "advance_question",
            })
            self._track_task(
                "_silence_advance_task",
                asyncio.create_task(self._advance_after_silence()),
            )
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

    # Real speech resets the consecutive silence strike counter
    set_voice_field(session_id, "silence_strikes", 0)

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
