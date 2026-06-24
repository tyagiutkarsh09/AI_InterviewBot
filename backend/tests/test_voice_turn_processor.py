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


import src.services.interview.voice_turn_processor as vtp
from tests.conftest import make_question
from src.services.audio.voice_session import increment_voice_field


class _RecordingTTS:
    """Captures each sentence handed to TTS so tests can assert what was spoken."""

    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def stream_sentence(self, text: str, ws) -> None:
        self.spoken.append(text)


@pytest.mark.asyncio
async def test_silence_monitor_speaks_both_nudges(fake_ws, monkeypatch):
    """The check-ins must be SPOKEN (streamed to TTS), not just emitted as JSON.
    The original bug was that prompts were sent as text-only events the candidate
    never heard — a test asserting only the JSON event would have passed against it."""
    monkeypatch.setattr(vtp, "SILENCE_PROMPT_SECS", 0.01)
    monkeypatch.setattr(vtp, "SILENCE_CHECKIN_SECS", 0.02)
    monkeypatch.setattr(vtp, "SILENCE_STRIKE_SECS", 100)  # never reach advance here

    seed_voice_session("s-nudge", [make_question("q1", "python"), make_question("q2", "sql")])
    state = vtp.VoiceTurnState("s-nudge", fake_ws)
    tts = _RecordingTTS()
    state.tts = tts  # type: ignore[assignment]

    state._start_silence_monitor()
    await asyncio.sleep(0.1)
    state.cancel_silence_monitor()

    joined = " ".join(tts.spoken)
    assert "Take your time" in joined, "first nudge was not spoken"
    assert "Are you still there" in joined, "second nudge was not spoken"


@pytest.mark.asyncio
async def test_speech_start_cancels_pending_nudges(fake_ws, monkeypatch):
    """A candidate who resumes speaking mid-ladder must not be talked over by a
    queued nudge or advance — cancel_silence_monitor stops the rest of the ladder."""
    monkeypatch.setattr(vtp, "SILENCE_PROMPT_SECS", 0.01)
    monkeypatch.setattr(vtp, "SILENCE_CHECKIN_SECS", 0.05)
    monkeypatch.setattr(vtp, "SILENCE_STRIKE_SECS", 0.09)

    seed_voice_session("s-cancel", [make_question("q1", "python"), make_question("q2", "sql")])
    state = vtp.VoiceTurnState("s-cancel", fake_ws)
    tts = _RecordingTTS()
    state.tts = tts  # type: ignore[assignment]

    state._start_silence_monitor()
    await asyncio.sleep(0.02)   # first nudge has fired, second has not
    state.cancel_silence_monitor()
    await asyncio.sleep(0.1)    # give any (wrongly) pending nudge time to fire

    joined = " ".join(tts.spoken)
    assert "Take your time" in joined
    assert "Are you still there" not in joined, "second nudge fired after cancel"
    assert int(get_voice_session("s-cancel")["current_question_idx"]) == 0, "advanced after cancel"


@pytest.mark.asyncio
async def test_advance_after_silence_moves_to_next_question(fake_ws):
    """Continued silence must actually progress the interview: bump the question
    index and SPEAK the next question. A test that only checked silence_strikes
    would have passed against the old no-op."""
    seed_voice_session("s-adv", [make_question("q1", "python"), make_question("q2", "sql")])
    state = vtp.VoiceTurnState("s-adv", fake_ws)
    tts = _RecordingTTS()
    state.tts = tts  # type: ignore[assignment]

    await state._advance_after_silence()
    state.cancel_silence_monitor()  # stop the fresh monitor stream_response started

    assert int(get_voice_session("s-adv")["current_question_idx"]) == 1, "did not advance"
    assert "sql" in " ".join(tts.spoken).lower(), "next question was not spoken"


@pytest.mark.asyncio
async def test_advance_after_silence_enters_wrap_up_at_last_question(fake_ws):
    """When the last question times out, the AI wraps up instead of advancing into
    an empty question list."""
    seed_voice_session("s-wrap", [make_question("q1", "python")])
    state = vtp.VoiceTurnState("s-wrap", fake_ws)
    tts = _RecordingTTS()
    state.tts = tts  # type: ignore[assignment]

    await state._advance_after_silence()
    state.cancel_silence_monitor()

    assert get_voice_session("s-wrap")["interview_phase"] == "wrap_up"
    joined = " ".join(tts.spoken).lower()
    assert "anything you'd like to ask" in joined, "wrap-up invite was not spoken"


@pytest.mark.asyncio
async def test_silence_monitor_triggers_advance_and_strike(fake_ws, monkeypatch):
    """The monitor's final tier must increment silence_strikes AND hand off to the
    advance path — proving the strike counter and progression are wired together."""
    monkeypatch.setattr(vtp, "SILENCE_PROMPT_SECS", 0.01)
    monkeypatch.setattr(vtp, "SILENCE_CHECKIN_SECS", 0.02)
    monkeypatch.setattr(vtp, "SILENCE_STRIKE_SECS", 0.03)

    seed_voice_session("s-trig", [make_question("q1", "python"), make_question("q2", "sql")])
    state = vtp.VoiceTurnState("s-trig", fake_ws)
    tts = _RecordingTTS()
    state.tts = tts  # type: ignore[assignment]

    advanced = asyncio.Event()

    async def fake_advance() -> None:
        advanced.set()

    state._advance_after_silence = fake_advance  # type: ignore[method-assign]

    state._start_silence_monitor()
    await asyncio.wait_for(advanced.wait(), timeout=1)
    state.cancel_silence_monitor()

    assert int(get_voice_session("s-trig")["silence_strikes"]) == 1
    assert any(m.get("event") == "silence_strike" for m in fake_ws.json_messages)
