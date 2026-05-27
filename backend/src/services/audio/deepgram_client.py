"""
Deepgram streaming STT — server-side proxy only.  API key never reaches the browser.

nova-2, 16kHz, linear16, interim_results=True
endpointing=300, utterance_end_ms=1000, smart_format=True

Partial transcripts → {event:"transcript", is_final:false}
speech_final=True   → triggers LLM processing
"""

import asyncio
import logging
from typing import Any, Callable, Coroutine, Optional

from src.lib.settings import get_settings

logger = logging.getLogger(__name__)

OnTranscriptCallback = Callable[
    [str, bool],  # (transcript_text, is_final)
    Coroutine[Any, Any, None],
]


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
            endpointing=300,
            utterance_end_ms="1000",
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
        logger.info("Deepgram connected for session %s", self.session_id)

    async def send_audio(self, pcm_bytes: bytes) -> None:
        if self._connected and self._connection:
            try:
                await self._connection.send(pcm_bytes)
            except Exception as exc:
                logger.warning("Deepgram send failed session=%s: %s", self.session_id, exc)

    async def close(self) -> None:
        self._connected = False
        if self._connection:
            try:
                await self._connection.finish()
            except Exception:
                pass
            self._connection = None
        logger.info("Deepgram closed for session %s", self.session_id)

    async def _on_transcript(self, result: Any, **_: Any) -> None:
        try:
            alt = result.channel.alternatives[0]
            text: str = alt.transcript
            is_final: bool = result.is_final
            speech_final: bool = result.speech_final
        except (AttributeError, IndexError):
            return

        if not text:
            return

        if speech_final:
            # Deduplicate: discard if identical or prefix of previous committed
            if text == self._last_committed or self._last_committed.startswith(text):
                return
            self._last_committed = text
            await self.on_transcript(text, is_final=True)
        elif is_final:
            # Partial committed transcript — update live display only
            await self.on_transcript(text, is_final=False)

    async def _on_error(self, error: Any, **_: Any) -> None:
        logger.error("Deepgram error session=%s: %s", self.session_id, error)

    async def _on_close(self, close: Any, **_: Any) -> None:
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
        if self._stream:
            await self._stream.send_audio(audio)

    async def stop(self) -> None:
        if self._stream:
            await self._stream.close()
            self._stream = None

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

    async def _handle_transcript(self, text: str, is_final: bool) -> None:
        await self.on_transcript(text, is_final)
