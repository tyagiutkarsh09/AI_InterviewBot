"""Tests for VoiceTurnState — TTS streaming order and barge-in cancellation."""

import asyncio

import pytest

from tests.conftest import FakeWebSocket, seed_voice_session

from src.services.audio.voice_session import get_voice_session
from src.services.interview.voice_turn_processor import VoiceTurnState


@pytest.mark.asyncio
async def test_stream_response_plays_sentences_sequentially(fake_ws: FakeWebSocket):
    """Sentences must stream one at a time — concurrent streams interleave
    MP3 bytes on the single socket and garble the audio."""
    seed_voice_session("s-seq", [])
    state = VoiceTurnState("s-seq", fake_ws)

    order: list[str] = []
    concurrent = {"now": 0, "max": 0}

    class RecordingTTS:
        async def stream_sentence(self, text: str, ws) -> None:
            concurrent["now"] += 1
            concurrent["max"] = max(concurrent["max"], concurrent["now"])
            order.append(text)
            await asyncio.sleep(0.01)
            concurrent["now"] -= 1

    state.tts = RecordingTTS()  # type: ignore[assignment]

    try:
        await state.stream_response("First one. Second one. Third one.")
    finally:
        state.cancel_silence_monitor()

    assert concurrent["max"] == 1, "two sentences streamed at the same time"
    assert order == ["First one.", "Second one.", "Third one."]


@pytest.mark.asyncio
async def test_barge_in_cancels_tts_and_opens_mic(fake_ws: FakeWebSocket):
    """A barge-in must cancel the in-flight TTS, reopen the mic, and count it —
    without crashing the turn task."""
    seed_voice_session("s-barge", [])
    state = VoiceTurnState("s-barge", fake_ws)

    started = asyncio.Event()
    cancelled = {"hit": False}

    class SlowTTS:
        async def stream_sentence(self, text: str, ws) -> None:
            started.set()
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled["hit"] = True
                raise

    state.tts = SlowTTS()  # type: ignore[assignment]

    task = asyncio.create_task(state.stream_response("Hello there. Long answer."))
    await asyncio.wait_for(started.wait(), timeout=1)
    assert state.bot_speaking is True

    await state.handle_barge_in()

    # The turn task must complete cleanly (not surface CancelledError).
    await asyncio.wait_for(task, timeout=1)
    state.cancel_silence_monitor()

    assert cancelled["hit"] is True, "in-flight TTS was not cancelled"
    assert state.bot_speaking is False
    assert any(m.get("event") == "barge_in" for m in fake_ws.json_messages)
    assert any(
        m.get("event") == "turn" and m.get("speaker") == "candidate"
        for m in fake_ws.json_messages
    )
    assert int(get_voice_session("s-barge")["barge_in_count"]) == 1
