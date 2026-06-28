"""
TTS streaming client — ElevenLabs primary, OpenAI TTS-1 fallback.

stream_sentence(text, ws):
  Streams MP3 binary to client WebSocket.
  Sends {event:"tts_sentence_complete"} after each sentence.

ElevenLabs: eleven_turbo_v2, voice 21m00Tcm4TlvDq8ikWAM (Rachel)
Fallback:   OpenAI TTS-1 on ElevenLabs 5xx.
"""

import logging
from typing import Any

import httpx
from fastapi import WebSocket

from src.lib.settings import get_settings

logger = logging.getLogger(__name__)

ELEVENLABS_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
ELEVENLABS_MODEL = "eleven_turbo_v2"
CHUNK_SIZE = 4096
TTS_SESSION_CHAR_BUDGET = 50_000


async def _send_json(ws: WebSocket, data: dict[str, Any]) -> None:
    try:
        await ws.send_json(data)
    except Exception:
        pass


class ElevenLabsTTS:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._chars_used = 0

    @property
    def chars_used(self) -> int:
        return self._chars_used

    @property
    def budget_exhausted(self) -> bool:
        return self._chars_used >= TTS_SESSION_CHAR_BUDGET

    async def stream_sentence(self, text: str, ws: WebSocket) -> None:
        """Stream a single sentence as MP3 binary frames then signal completion."""
        if not text.strip():
            return

        if self._chars_used + len(text) > TTS_SESSION_CHAR_BUDGET:
            logger.warning(
                "TTS character budget exhausted (%d/%d) — skipping ElevenLabs",
                self._chars_used, TTS_SESSION_CHAR_BUDGET,
            )
            await _send_json(ws, {"event": "tts_sentence_complete"})
            return

        if not self.settings.elevenlabs_api_key:
            logger.warning("ELEVENLABS_API_KEY not set — TTS skipped")
            await _send_json(ws, {"event": "tts_sentence_complete"})
            return

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
        payload = {
            "text": text,
            "model_id": ELEVENLABS_MODEL,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "speed": 1.1,
            },
            "output_format": "mp3_44100_64",
        }
        headers = {
            "xi-api-key": self.settings.elevenlabs_api_key,
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    if response.status_code >= 500:
                        logger.warning(
                            "ElevenLabs %d — falling back to OpenAI TTS",
                            response.status_code,
                        )
                        await self._openai_fallback(text, ws)
                        return

                    if response.status_code != 200:
                        logger.error("ElevenLabs error %d", response.status_code)
                        await _send_json(ws, {"event": "tts_sentence_complete"})
                        return

                    self._chars_used += len(text)
                    async for chunk in response.aiter_bytes(chunk_size=CHUNK_SIZE):
                        try:
                            await ws.send_bytes(chunk)
                        except Exception:
                            return

        except httpx.TimeoutException:
            logger.warning("ElevenLabs timeout — falling back to OpenAI TTS")
            await self._openai_fallback(text, ws)
            return
        except Exception as exc:
            logger.error("ElevenLabs stream error: %s", exc)
            await _send_json(ws, {"event": "tts_sentence_complete"})
            return

        await _send_json(ws, {"event": "tts_sentence_complete"})

    async def _openai_fallback(self, text: str, ws: WebSocket) -> None:
        """OpenAI TTS-1 fallback — streams MP3 in chunks."""
        if not self.settings.anthropic_api_key:
            # Use anthropic_api_key as proxy for having OpenAI configured
            pass

        try:
            import openai  # type: ignore[import-untyped]
        except ImportError:
            logger.error("openai package not installed — TTS fallback unavailable")
            await _send_json(ws, {"event": "tts_sentence_complete"})
            return

        openai_key = getattr(self.settings, "openai_api_key", "")
        if not openai_key:
            logger.warning("OPENAI_API_KEY not set — TTS fallback skipped")
            await _send_json(ws, {"event": "tts_sentence_complete"})
            return

        try:
            client = openai.AsyncOpenAI(api_key=openai_key)
            async with client.audio.speech.with_streaming_response.create(
                model="tts-1",
                voice="alloy",
                input=text,
                response_format="mp3",
            ) as response:
                async for chunk in response.iter_bytes(chunk_size=CHUNK_SIZE):
                    try:
                        await ws.send_bytes(chunk)
                    except Exception:
                        return
        except Exception as exc:
            logger.error("OpenAI TTS fallback error: %s", exc)

        await _send_json(ws, {"event": "tts_sentence_complete"})


def split_into_sentences(text: str) -> list[str]:
    """Split LLM response into sentences for streaming TTS."""
    import re
    # Split on sentence-ending punctuation followed by whitespace or end
    parts = re.split(r'(?<=[.?!])\s+', text.strip())
    return [p.strip() for p in parts if p.strip()]
