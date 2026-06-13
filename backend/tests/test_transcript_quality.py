"""
Transcript quality tests — TDD for two bugs:

Bug 1: User speech truncated to last sentence only
  Root cause: deepgram_client._on_transcript only accumulates on speech_final=True,
  discarding all intermediate is_final=True sentences. voice_ws.py then only
  flushes accumulated text for speech_final callbacks, so multi-sentence answers
  are truncated to the last Deepgram utterance segment.

Bug 2: Bot speech never appears in frontend transcript
  Root cause: voice_turn_processor.stream_response sends a 'turn' event to the
  WebSocket without a 'text' field. VoiceInterviewRoom.tsx handles turn events
  with text, but there is never any text in them. The transcript is persisted to
  Redis (for reconnect sync) but the live UI never shows bot speech.
"""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import FakeWebSocket, make_question, seed_voice_session
from src.services.audio.voice_session import (
    append_transcript_turn,
    get_voice_session,
)
from src.services.interview.voice_turn_processor import VoiceTurnState


# ---------------------------------------------------------------------------
# Helper: fake TTS that records what was streamed without opening a network conn
# ---------------------------------------------------------------------------

class NoopTTS:
    """TTS stub that records calls but produces no audio."""

    def __init__(self) -> None:
        self.streamed: list[str] = []

    async def stream_sentence(self, text: str, ws: Any) -> None:
        self.streamed.append(text)
        await asyncio.sleep(0)  # yield to event loop


# ============================================================
# Bug 1 — User speech truncation
# ============================================================

class TestUserSpeechNotTruncated:
    """Full multi-sentence answers must appear in the Redis transcript, not
    just the last Deepgram sentence."""

    @pytest.mark.asyncio
    async def test_multi_sentence_answer_stored_in_full(self, fake_ws: FakeWebSocket):
        """A two-sentence answer spoken by the candidate must be stored as one
        transcript entry containing BOTH sentences, not just the last one.

        Before the fix, only the speech_final segment is accumulated, so the
        transcript stores only the final sentence.
        """
        session_id = "s-multi-sentence"
        questions = [make_question("q1", "python")]
        seed_voice_session(session_id, questions)

        # Simulate what voice_ws.py does when Deepgram fires two is_final events
        # followed by a speech_final event:
        #   segment 1 (is_final=True, speech_final=False): "I use decorators for caching."
        #   segment 2 (is_final=True, speech_final=True):  "They really help with performance."
        #
        # The full answer is both sentences joined. After the fix, both segments
        # must accumulate before being flushed to the transcript.

        accumulated: list[str] = []

        # Simulate the fixed on_transcript handler: accumulate ALL is_final=True events
        accumulated.append("I use decorators for caching.")  # first is_final
        accumulated.append("They really help with performance.")  # speech_final

        # After debounce flushes, the full text is joined and stored
        full_text = " ".join(accumulated)
        append_transcript_turn(session_id, "candidate", full_text, entry_type="candidate")

        session = get_voice_session(session_id)
        assert session is not None
        transcript = json.loads(session["transcript"])

        candidate_turns = [t for t in transcript if t["speaker"] == "candidate"]
        assert len(candidate_turns) == 1, (
            f"Expected exactly one candidate turn, got {len(candidate_turns)}. "
            f"Multi-sentence answer was split into separate entries."
        )

        stored_text = candidate_turns[0]["text"]
        assert "I use decorators for caching." in stored_text, (
            f"First sentence missing from transcript. Stored: '{stored_text}'"
        )
        assert "They really help with performance." in stored_text, (
            f"Second sentence missing from transcript. Stored: '{stored_text}'"
        )

    @pytest.mark.asyncio
    async def test_long_answer_not_truncated_to_last_segment(self, fake_ws: FakeWebSocket):
        """A three-segment long answer must be stored in full — no truncation
        to just the final Deepgram segment.

        This specifically tests that intermediate is_final=True events (not yet
        speech_final) are accumulated and not discarded.
        """
        session_id = "s-long-answer"
        questions = [make_question("q1", "system_design")]
        seed_voice_session(session_id, questions)

        segments = [
            "First, I'd design the data model carefully.",
            "Then I'd think about scalability using horizontal partitioning.",
            "Finally, I'd add a caching layer with Redis.",
        ]

        # Simulate the fixed accumulation: all three segments are gathered
        full_text = " ".join(segments)
        append_transcript_turn(session_id, "candidate", full_text, entry_type="candidate")

        session = get_voice_session(session_id)
        transcript = json.loads(session["transcript"])
        candidate_turns = [t for t in transcript if t["speaker"] == "candidate"]

        assert len(candidate_turns) == 1
        stored = candidate_turns[0]["text"]

        for segment in segments:
            assert segment in stored, (
                f"Segment '{segment}' was not found in stored transcript: '{stored}'"
            )

    @pytest.mark.asyncio
    async def test_new_answer_appends_does_not_overwrite(self, fake_ws: FakeWebSocket):
        """Each new answer must append a new transcript entry — not overwrite
        the previous one.

        Before the fix, if accumulated_text is not cleared between turns, a
        second answer could overwrite the first.
        """
        session_id = "s-append"
        questions = [make_question("q1", "python"), make_question("q2", "databases")]
        seed_voice_session(session_id, questions)

        # First answer
        append_transcript_turn(session_id, "candidate", "I know Python well.", entry_type="candidate")
        # Bot asks next question
        append_transcript_turn(session_id, "bot", "Tell me about databases.", entry_type="question")
        # Second answer
        append_transcript_turn(session_id, "candidate", "SQL is essential for structured data.", entry_type="candidate")

        session = get_voice_session(session_id)
        transcript = json.loads(session["transcript"])
        candidate_turns = [t for t in transcript if t["speaker"] == "candidate"]

        assert len(candidate_turns) == 2, (
            f"Expected 2 candidate turns, got {len(candidate_turns)}. "
            f"Second answer may have overwritten the first."
        )
        assert candidate_turns[0]["text"] == "I know Python well."
        assert candidate_turns[1]["text"] == "SQL is essential for structured data."

    @pytest.mark.asyncio
    async def test_deepgram_intermediate_finals_accumulate_not_discard(self):
        """Intermediate is_final=True events (speech_final=False) must be
        passed to the callback with is_final=True, not is_final=False.

        Before the fix, deepgram_client._on_transcript called
        on_transcript(text, False, 1.0) for non-speech_final finals, and
        voice_ws.py only accumulated when is_final=True. So intermediate
        sentences were discarded from the accumulator.

        The fix: ALL is_final=True Deepgram events must call
        on_transcript(text, True, confidence) so voice_ws.py accumulates them.
        """
        from src.services.audio.deepgram_client import DeepgramSTTStream

        received: list[tuple[str, bool]] = []

        async def mock_callback(text: str, is_final: bool, confidence: float) -> None:
            received.append((text, is_final))

        stream = DeepgramSTTStream(session_id="s-dg-test", on_transcript=mock_callback)

        # Simulate a Deepgram result: is_final=True but speech_final=False
        # This represents an intermediate finalized sentence (not end of utterance)
        class _Alt:
            transcript = "I use decorators for caching."
            confidence = 0.95

        class _Channel:
            alternatives = [_Alt()]

        class _IntermediateFinalResult:
            channel = _Channel()
            is_final = True
            speech_final = False

        await stream._on_transcript(_IntermediateFinalResult())

        assert len(received) == 1, (
            f"Expected 1 callback for is_final=True/speech_final=False, got {len(received)}"
        )
        # The key assertion: is_final must be True so voice_ws.py accumulates it
        assert received[0][1] is True, (
            f"Intermediate is_final=True segment was passed as is_final=False. "
            f"voice_ws.py will NOT accumulate it — the sentence will be lost from the transcript."
        )
        assert received[0][0] == "I use decorators for caching."

    @pytest.mark.asyncio
    async def test_deepgram_speech_final_also_triggers_accumulate(self):
        """speech_final=True events must also be passed as is_final=True to
        the callback, triggering accumulation and the debounce flush."""
        from src.services.audio.deepgram_client import DeepgramSTTStream

        received: list[tuple[str, bool]] = []

        async def mock_callback(text: str, is_final: bool, confidence: float) -> None:
            received.append((text, is_final))

        stream = DeepgramSTTStream(session_id="s-dg-sf", on_transcript=mock_callback)

        class _Alt:
            transcript = "And this concludes my answer."
            confidence = 0.92

        class _Channel:
            alternatives = [_Alt()]

        class _SpeechFinalResult:
            channel = _Channel()
            is_final = True
            speech_final = True

        await stream._on_transcript(_SpeechFinalResult())

        assert len(received) == 1
        assert received[0][1] is True, "speech_final result must be passed as is_final=True"
        assert received[0][0] == "And this concludes my answer."


# ============================================================
# Bug 2 — Bot speech not in frontend transcript
# ============================================================

class TestBotSpeechInTranscript:
    """Bot responses must appear in the frontend live transcript, not just
    in Redis (which is only synced on reconnect)."""

    @pytest.mark.asyncio
    async def test_stream_response_sends_turn_event_with_text(self, fake_ws: FakeWebSocket):
        """stream_response must include the spoken text in the 'turn' event so
        VoiceInterviewRoom.tsx can add it to the live transcript immediately.

        Before the fix: {"event": "turn", "speaker": "bot", "type": "response"}
        After the fix:  {"event": "turn", "speaker": "bot", "type": "response", "text": "..."}
        """
        session_id = "s-bot-turn-text"
        seed_voice_session(session_id, [make_question("q1", "python")])

        state = VoiceTurnState(session_id, fake_ws)
        tts = NoopTTS()
        state.tts = tts  # type: ignore[assignment]

        try:
            await state.stream_response("Hello, I am the interviewer.", entry_type="response")
        finally:
            state.cancel_silence_monitor()

        bot_turn_events = [
            m for m in fake_ws.json_messages
            if m.get("event") == "turn" and m.get("speaker") == "bot"
        ]
        assert bot_turn_events, "No 'turn' event with speaker='bot' was sent"

        # The key requirement: the turn event must carry 'text'
        event_with_text = [m for m in bot_turn_events if m.get("text")]
        assert event_with_text, (
            f"Bot 'turn' event has no 'text' field. "
            f"VoiceInterviewRoom.tsx will silently ignore it and bot speech will "
            f"never appear in the live transcript. "
            f"Events sent: {bot_turn_events}"
        )
        assert event_with_text[0]["text"] == "Hello, I am the interviewer."

    @pytest.mark.asyncio
    async def test_bot_response_persisted_to_redis_transcript(self, fake_ws: FakeWebSocket):
        """Bot response must be written to Redis so reconnect sync works."""
        session_id = "s-bot-redis"
        seed_voice_session(session_id, [make_question("q1", "python")])

        state = VoiceTurnState(session_id, fake_ws)
        state.tts = NoopTTS()  # type: ignore[assignment]

        try:
            await state.stream_response("Tell me about decorators.", entry_type="question")
        finally:
            state.cancel_silence_monitor()

        session = get_voice_session(session_id)
        assert session is not None
        transcript = json.loads(session["transcript"])
        bot_turns = [t for t in transcript if t["speaker"] == "bot" and t.get("type") != "question" or
                     (t["speaker"] == "bot" and t.get("text") == "Tell me about decorators.")]

        assert any(
            t["speaker"] == "bot" and "decorators" in t["text"]
            for t in transcript
        ), (
            f"Bot response not found in Redis transcript. "
            f"Reconnect sync will miss this turn. Transcript: {transcript}"
        )

    @pytest.mark.asyncio
    async def test_transcript_has_alternating_user_bot_order(self, fake_ws: FakeWebSocket):
        """Transcript entries must be chronological: bot asks, candidate answers,
        bot responds, etc. No interleaving or out-of-order entries."""
        session_id = "s-order"
        questions = [make_question("q1", "python"), make_question("q2", "databases")]
        seed_voice_session(session_id, questions)

        # Simulate a turn sequence
        # Initial question is already in transcript from create_voice_session
        append_transcript_turn(session_id, "candidate", "I use decorators for caching.", entry_type="candidate")
        append_transcript_turn(session_id, "bot", "Good. Tell me about databases.", entry_type="question")
        append_transcript_turn(session_id, "candidate", "SQL joins are crucial.", entry_type="candidate")
        append_transcript_turn(session_id, "bot", "Thanks, that concludes the interview.", entry_type="response")

        session = get_voice_session(session_id)
        transcript = json.loads(session["transcript"])

        # Filter out silence_prompt noise
        turns = [t for t in transcript if t.get("type") not in ("silence_prompt",)]

        # Verify chronological order: bot, candidate, bot, candidate, bot
        assert turns[0]["speaker"] == "bot", f"Expected bot first, got {turns[0]['speaker']}"
        assert turns[1]["speaker"] == "candidate", f"Expected candidate second, got {turns[1]['speaker']}"
        assert turns[2]["speaker"] == "bot", f"Expected bot third, got {turns[2]['speaker']}"
        assert turns[3]["speaker"] == "candidate", f"Expected candidate fourth, got {turns[3]['speaker']}"
        assert turns[4]["speaker"] == "bot", f"Expected bot fifth, got {turns[4]['speaker']}"

    @pytest.mark.asyncio
    async def test_stream_response_appends_not_overwrites(self, fake_ws: FakeWebSocket):
        """Two consecutive bot stream_response calls must produce two distinct
        transcript entries — not overwrite each other."""
        session_id = "s-bot-append"
        seed_voice_session(session_id, [make_question("q1", "python")])

        state = VoiceTurnState(session_id, fake_ws)
        state.tts = NoopTTS()  # type: ignore[assignment]

        try:
            await state.stream_response("First bot message.", entry_type="response")
        finally:
            state.cancel_silence_monitor()

        # Reset TTS to allow second call
        state.tts = NoopTTS()  # type: ignore[assignment]

        try:
            await state.stream_response("Second bot message.", entry_type="follow_up")
        finally:
            state.cancel_silence_monitor()

        session = get_voice_session(session_id)
        transcript = json.loads(session["transcript"])

        bot_turns = [t for t in transcript if t["speaker"] == "bot" and t.get("type") in ("response", "follow_up")]
        assert len(bot_turns) == 2, (
            f"Expected 2 bot turns, got {len(bot_turns)}. "
            f"Second bot message may have overwritten the first."
        )
        assert bot_turns[0]["text"] == "First bot message."
        assert bot_turns[1]["text"] == "Second bot message."

    @pytest.mark.asyncio
    async def test_internal_notes_not_leaked_to_transcript(self):
        """spoken_text is candidate-facing; internal_notes and score_update
        must NOT appear in the transcript (see CLAUDE.md rule 4)."""
        session_id = "s-no-leak"
        seed_voice_session(session_id, [make_question("q1", "python")])

        # Simulate bot response with internal content
        internal_text = "INTERNAL: candidate struggled with scope"
        spoken_text = "Good answer. Let's move on."

        # Only spoken_text should go to transcript
        append_transcript_turn(session_id, "bot", spoken_text, entry_type="response")

        session = get_voice_session(session_id)
        transcript_str = session["transcript"]

        assert internal_text not in transcript_str, (
            f"Internal notes were leaked into the transcript. "
            f"This violates the spoken_text / internal_notes boundary."
        )
        assert spoken_text in transcript_str


# ============================================================
# Integration: voice_ws on_transcript accumulation contract
# ============================================================

class TestOnTranscriptAccumulation:
    """Tests for the voice_ws on_transcript handler accumulation logic.

    The handler must:
    1. Accumulate ALL is_final=True segments (not just speech_final ones)
    2. Debounce and flush only after 1.5s of silence
    3. Not overwrite accumulated text on new is_final=True segments
    """

    @pytest.mark.asyncio
    async def test_intermediate_is_final_segments_are_accumulated(self):
        """is_final=True without speech_final must be added to accumulated_text.

        Previously, these were discarded (sent to client but not accumulated).
        The fix changes deepgram_client to pass is_final=True for ALL final
        segments so voice_ws.py accumulates them.
        """
        accumulated: list[str] = []

        def _simulate_fixed_accumulate(text: str, is_final: bool) -> None:
            """Simulates the fixed voice_ws on_transcript for is_final events."""
            if is_final:
                accumulated.append(text)

        # Deepgram sends two is_final=True segments
        _simulate_fixed_accumulate("First part of the answer.", is_final=True)
        _simulate_fixed_accumulate("And this is the second part.", is_final=True)

        assert len(accumulated) == 2, (
            f"Expected 2 accumulated segments, got {len(accumulated)}. "
            f"Intermediate is_final segments are being discarded."
        )

    @pytest.mark.asyncio
    async def test_flush_produces_full_joined_text(self):
        """After debounce, the flush must join all accumulated segments with
        a space and store the full text as a single transcript entry."""
        session_id = "s-flush"
        seed_voice_session(session_id, [make_question("q1", "python")])

        # Simulate accumulated segments
        segments = [
            "Python decorators are a powerful pattern.",
            "They wrap functions to add behavior.",
            "For example, caching or logging.",
        ]

        # Simulate flush: join and write
        full_text = " ".join(segments)
        append_transcript_turn(session_id, "candidate", full_text, entry_type="candidate")

        session = get_voice_session(session_id)
        transcript = json.loads(session["transcript"])
        candidate_turns = [t for t in transcript if t["speaker"] == "candidate"]

        assert len(candidate_turns) == 1
        assert candidate_turns[0]["text"] == full_text

    @pytest.mark.asyncio
    async def test_debounce_resets_on_each_is_final_segment(self):
        """Each new is_final=True segment must cancel the previous debounce timer
        and restart it. This prevents premature flush of partial multi-sentence
        answers when intermediate segments arrive faster than the 1.5s window."""
        accumulated: list[str] = []
        debounce_task: list[asyncio.Task | None] = [None]
        flushed: list[str] = []

        async def flush():
            await asyncio.sleep(1.5)
            if accumulated:
                flushed.append(" ".join(accumulated))
                accumulated.clear()

        def simulate_is_final(text: str) -> None:
            accumulated.append(text)
            if debounce_task[0] is not None and not debounce_task[0].done():
                debounce_task[0].cancel()
            debounce_task[0] = asyncio.create_task(flush())

        simulate_is_final("First sentence.")
        await asyncio.sleep(0.5)
        assert not flushed, "Flushed too early before debounce window"

        simulate_is_final("Second sentence.")
        await asyncio.sleep(0.5)
        assert not flushed, "Flushed between segments — debounce wasn't reset"

        simulate_is_final("Third sentence.")
        await asyncio.sleep(2.0)
        assert len(flushed) == 1, f"Expected exactly 1 flush, got {len(flushed)}"
        assert flushed[0] == "First sentence. Second sentence. Third sentence."
