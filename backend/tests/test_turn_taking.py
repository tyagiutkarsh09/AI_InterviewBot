"""Tests for turn-taking: debounce timing, semantic detection, and control handling.

Each test encodes WHY the behavior matters, not just WHAT it does.
"""

import asyncio
from typing import Any, Optional
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import FakeWebSocket, make_question, seed_voice_session

from src.routes.voice_ws import (
    DEBOUNCE_COMPLETE_SECS,
    DEBOUNCE_INCOMPLETE_SECS,
    DEBOUNCE_SECS,
    STT_LOW_CONFIDENCE,
    _handle_control,
    _looks_complete,
    _looks_incomplete,
)
from src.services.audio.voice_session import get_voice_session, set_voice_field


# ---- Unit tests for semantic detection ----


class TestLooksComplete:
    """Completion phrases must be reliably detected — false negatives mean
    the bot waits the full 3.7s after the user already signaled they're done."""

    def test_explicit_phrases(self):
        assert _looks_complete("I think Python is great. That's my answer")
        assert _looks_complete("I used decorators for that. That's all")
        assert _looks_complete("that's it")
        assert _looks_complete("Yeah that's it")
        assert _looks_complete("I don't have anything else")
        assert _looks_complete("Sorry I may not know")
        assert _looks_complete("That is what I think")

    def test_phrase_in_last_60_chars(self):
        long_text = "x " * 40 + "That's my answer"
        assert _looks_complete(long_text)

    def test_normal_speech_not_detected(self):
        assert not _looks_complete("I think Python is a great language")
        assert not _looks_complete("I used decorators and context managers")
        assert not _looks_complete("The answer depends on the use case")
        assert not _looks_complete("")

    def test_case_insensitive(self):
        assert _looks_complete("THAT'S MY ANSWER")
        assert _looks_complete("That's All")


class TestLooksIncomplete:
    """Trailing conjunctions/prepositions mean the speaker was mid-thought.
    Flushing here produces an incoherent partial answer that gets a bad score."""

    def test_trailing_conjunctions(self):
        assert _looks_incomplete("The reason I chose Python is because")
        assert _looks_incomplete("I implemented it using and")
        assert _looks_incomplete("We needed this feature but")
        assert _looks_incomplete("I think we should use the")

    def test_complete_sentences_not_detected(self):
        assert not _looks_incomplete("I finished the project")
        assert not _looks_incomplete("That's all")
        assert not _looks_incomplete("Python is great for backend work")

    def test_strips_trailing_punctuation(self):
        assert _looks_incomplete("The reason is because,")
        assert _looks_incomplete("I used it for.")

    def test_empty_text(self):
        assert not _looks_incomplete("")


# ---- Integration tests for debounce and control ----


@pytest.mark.asyncio
async def test_speech_start_cancels_debounce(fake_ws: FakeWebSocket):
    """If the candidate resumes speaking during the debounce window, the
    partial answer must not be flushed. Without this, the backend races:
    the flush fires on stale partial text while the user is still talking."""
    session_id = "s-cancel-debounce"
    seed_voice_session(session_id, [make_question("q1", "python")])

    debounce_task: list[Optional[asyncio.Task]] = [None]
    flushed = {"called": False}

    async def fake_flush():
        await asyncio.sleep(DEBOUNCE_SECS)
        flushed["called"] = True

    debounce_task[0] = asyncio.create_task(fake_flush())

    await _handle_control(
        fake_ws, session_id,
        {"event": "speech_start"},
        debounce_task,
    )

    # Let the event loop process the cancellation
    await asyncio.sleep(0.05)

    assert debounce_task[0].cancelled(), \
        "Debounce task should be cancelled when user resumes speaking"
    assert not flushed["called"], \
        "Debounce flush ran despite speech_start cancellation"


@pytest.mark.asyncio
async def test_speech_end_does_not_set_processing(fake_ws: FakeWebSocket):
    """Browser VAD fires speech_end on short pauses. If it triggers PROCESSING,
    the state machine gets confused during mid-answer pauses. Only the debounce
    flush should transition to PROCESSING."""
    session_id = "s-no-processing"
    seed_voice_session(session_id, [make_question("q1", "python")])
    set_voice_field(session_id, "state", "CANDIDATE_SPEAKING")

    await _handle_control(fake_ws, session_id, {"event": "speech_end"})

    session = get_voice_session(session_id)
    assert session["state"] != "PROCESSING", \
        "speech_end should not set state to PROCESSING"


@pytest.mark.asyncio
async def test_speech_start_cancels_silence_monitor(fake_ws: FakeWebSocket):
    """If the silence monitor runs while the user is actively speaking, it
    sends a 'Take your time' prompt mid-answer — confusing and disruptive."""
    session_id = "s-cancel-silence"
    seed_voice_session(session_id, [make_question("q1", "python")])

    from src.services.interview.voice_turn_processor import get_or_create_turn_state
    turn_state = get_or_create_turn_state(session_id, fake_ws)
    turn_state._start_silence_monitor()

    assert turn_state._silence_task is not None

    await _handle_control(
        fake_ws, session_id,
        {"event": "speech_start"},
        [None],
    )

    assert turn_state._silence_task is None, \
        "Silence monitor should be cancelled when user starts speaking"


# ---- Tests for adaptive debounce timing ----


class TestAdaptiveDebounce:
    """The debounce delay must adapt to transcript content. A fixed delay
    either responds too slowly (completion phrases) or interrupts mid-thought
    (trailing conjunctions)."""

    def test_completion_phrase_gets_short_delay(self):
        assert DEBOUNCE_COMPLETE_SECS < DEBOUNCE_SECS, \
            "Completion phrase debounce should be shorter than standard"
        assert DEBOUNCE_COMPLETE_SECS <= 1.0, \
            "Completion phrase should trigger fast response"

    def test_incomplete_phrase_gets_long_delay(self):
        assert DEBOUNCE_INCOMPLETE_SECS > DEBOUNCE_SECS, \
            "Incomplete phrase debounce should be longer than standard"

    def test_standard_debounce_is_reasonable(self):
        assert DEBOUNCE_SECS >= 2.0, \
            "Standard debounce too short for interview pauses"
        assert DEBOUNCE_SECS <= 5.0, \
            "Standard debounce too long — bot will feel unresponsive"


# ---- Tests for confidence thresholds ----


class TestConfidenceThresholds:
    """Aggressive repeat requests frustrate candidates. The threshold should
    only trigger on genuinely failed transcriptions, not slightly imperfect ones."""

    def test_low_confidence_threshold_is_conservative(self):
        assert STT_LOW_CONFIDENCE <= 0.55, \
            "Low confidence threshold too high — will trigger repeat requests on usable transcripts"

    @pytest.mark.asyncio
    async def test_low_confidence_repeat_fires_only_once(self, fake_ws: FakeWebSocket):
        """After one repeat request, the bot should process with what it has.
        Multiple 'please repeat' messages waste interview time and frustrate candidates."""
        session_id = "s-repeat-once"
        seed_voice_session(session_id, [make_question("q1", "python")])

        from src.routes.voice_ws import MAX_REPEAT_REQUESTS
        assert MAX_REPEAT_REQUESTS == 1, \
            "MAX_REPEAT_REQUESTS should be 1 to avoid frustrating candidates"
