"""
Deepgram streaming STT — server-side proxy only.  API key never reaches the browser.

nova-2, 16kHz, linear16, interim_results=True
endpointing=700, utterance_end_ms=2000, smart_format=True

Partial transcripts → {event:"transcript", is_final:false}
speech_final=True   → triggers LLM processing
"""

import asyncio
import logging
from typing import Any, Callable, Coroutine, Optional

from src.lib.settings import get_settings

logger = logging.getLogger(__name__)

OnTranscriptCallback = Callable[
    [str, bool, float],  # (transcript_text, is_final, confidence)
    Coroutine[Any, Any, None],
]

KEEPALIVE_INTERVAL = 8  # seconds — Deepgram times out at ~12s of silence


class DeepgramSTTStream:
    def __init__(
        self,
        session_id: str,
        on_transcript: OnTranscriptCallback,
    ) -> None:
        self.session_id = session_id
        self.on_transcript = on_transcript
        self._connection: Optional[Any] = None
        self._connected = False
        self._last_committed: str = ""
        self._keepalive_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

    async def connect(self) -> None:
        settings = get_settings()
        if not settings.deepgram_api_key:
            logger.warning(
                "DEEPGRAM_API_KEY not set — STT stream disabled for session %s",
                self.session_id,
            )
            return

        try:
            from deepgram import (  # type: ignore[import-untyped]
                DeepgramClient,
                LiveOptions,
                LiveTranscriptionEvents,
            )
        except ImportError:
            logger.error("deepgram-sdk not installed. Run: pip install deepgram-sdk")
            return

        client = DeepgramClient(settings.deepgram_api_key)
        self._connection = client.listen.asynclive.v("1")  # type: ignore[attr-defined]

        options = LiveOptions(
            model="nova-2",
            language="en-US",
            smart_format=True,
            interim_results=True,
            endpointing=700,
            utterance_end_ms="2000",
            vad_events=True,
            encoding="linear16",
            sample_rate=16000,
            channels=1,
        )

        self._connection.on(LiveTranscriptionEvents.Transcript, self._on_transcript)
        self._connection.on(LiveTranscriptionEvents.Error, self._on_error)
        self._connection.on(LiveTranscriptionEvents.Close, self._on_close)

        started = await self._connection.start(options)
        if not started:
            logger.error("Failed to start Deepgram connection for session %s", self.session_id)
            return

        self._connected = True
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        logger.info("Deepgram connected for session %s", self.session_id)

    async def send_audio(self, pcm_bytes: bytes) -> None:
        if self._connected and self._connection:
            try:
                await self._connection.send(pcm_bytes)
            except Exception as exc:
                logger.warning("Deepgram send failed session=%s: %s", self.session_id, exc)
                self._connected = False

    async def close(self) -> None:
        self._connected = False
        if self._keepalive_task:
            self._keepalive_task.cancel()
            self._keepalive_task = None
        if self._connection:
            try:
                await self._connection.finish()
            except Exception:
                pass
            self._connection = None
        logger.info("Deepgram closed for session %s", self.session_id)

    async def _keepalive_loop(self) -> None:
        """Send periodic keepalive to prevent Deepgram from timing out during silence."""
        try:
            while self._connected and self._connection:
                await asyncio.sleep(KEEPALIVE_INTERVAL)
                if not self._connected or not self._connection:
                    break
                try:
                    await self._connection.keep_alive()
                except Exception:
                    break
        except asyncio.CancelledError:
            pass

    async def _on_transcript(self, _self_or_result: Any, result: Any = None, **_: Any) -> None:
        # SDK v3 passes (self, result, **kwargs) — handle both positional styles
        if result is None:
            result = _self_or_result
        try:
            alt = result.channel.alternatives[0]
            text: str = alt.transcript
            is_final: bool = result.is_final
            speech_final: bool = result.speech_final
            try:
                confidence = float(alt.confidence)
                if not (0.0 <= confidence <= 1.0):
                    confidence = 1.0
            except (AttributeError, TypeError, ValueError):
                confidence = 1.0
        except (AttributeError, IndexError):
            return

        if not text:
            return

        if speech_final:
            if text == self._last_committed or self._last_committed.startswith(text):
                return
            self._last_committed = text
            await self.on_transcript(text, True, confidence)
        elif is_final:
            # Intermediate finalised sentence (not yet end-of-utterance).
            # Pass is_final=True so voice_ws.py accumulates it alongside
            # speech_final segments — preventing multi-sentence truncation.
            await self.on_transcript(text, True, confidence)

    async def _on_error(self, *args: Any, **_: Any) -> None:
        error = args[0] if args else "unknown"
        logger.error("Deepgram error session=%s: %s", self.session_id, error)

    async def _on_close(self, *args: Any, **_: Any) -> None:
        self._connected = False
        logger.info("Deepgram connection closed session=%s", self.session_id)


class DeepgramManager:
    """Per-WS session Deepgram connection manager with reconnect logic."""

    MAX_RETRIES = 3

    def __init__(
        self,
        session_id: str,
        on_transcript: OnTranscriptCallback,
    ) -> None:
        self.session_id = session_id
        self.on_transcript = on_transcript
        self._stream: Optional[DeepgramSTTStream] = None
        self._retry_count = 0

    async def start(self) -> None:
        await self._create_and_connect()

    async def send(self, audio: bytes) -> None:
        if self._stream and not self._stream._connected:
            logger.info("Deepgram reconnecting on send for session %s", self.session_id)
            await self._reconnect()
        if self._stream:
            await self._stream.send_audio(audio)

    async def stop(self) -> None:
        if self._stream:
            await self._stream.close()
            self._stream = None

    async def _reconnect(self) -> None:
        """Tear down dead stream and create a fresh one."""
        if self._stream:
            await self._stream.close()
        self._retry_count = 0
        await self._create_and_connect()

    async def _create_and_connect(self) -> None:
        self._stream = DeepgramSTTStream(
            session_id=self.session_id,
            on_transcript=self._handle_transcript,
        )
        try:
            await self._stream.connect()
            self._retry_count = 0
        except Exception as exc:
            logger.error(
                "Deepgram connect failed session=%s attempt=%d: %s",
                self.session_id, self._retry_count, exc,
            )
            if self._retry_count < self.MAX_RETRIES:
                self._retry_count += 1
                delay = 2 ** self._retry_count
                await asyncio.sleep(delay)
                await self._create_and_connect()

    async def _handle_transcript(self, text: str, is_final: bool, confidence: float = 1.0) -> None:
        await self.on_transcript(text, is_final, confidence)
