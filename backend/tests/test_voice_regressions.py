"""
Regression tests for voice interview pipeline — bugs surfaced after commit 65d14dc.

Each test targets a specific root cause and is written to FAIL against the
current codebase.  The fix for each bug should make its test pass without
breaking the others.

Bug ranking (see docstrings for details):
  P0  — test_voice_ws_sends_first_question_after_connect
  P1  — test_create_voice_session_sets_started_at
  P2  — test_final_evaluation_does_not_block_spoken_response
  P3  — test_questions_answered_does_not_exceed_total_questions
  P4  — test_sqlite_connection_health_check
"""

import asyncio
import json
import time
from typing import Any, Optional
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import FakeWebSocket, make_question, seed_voice_session

from src.services.audio.voice_session import (
    create_voice_session,
    get_voice_session,
    set_voice_field,
)
from src.services.interview import voice_llm_orchestrator
from src.services.interview.voice_llm_orchestrator import run_llm_turn
from src.services.interview.voice_turn_processor import (
    VoiceTurnState,
    process_voice_turn,
    get_or_create_turn_state,
)
from src.services.interview.voice_evaluation import _compute_metrics
from src.models.interview_report import InterviewMetrics


# ---------------------------------------------------------------------------
# Helpers — fake LLM plumbing (same pattern as existing tests)
# ---------------------------------------------------------------------------

class _Content:
    def __init__(self, text: str) -> None:
        self.text = text


class _Response:
    def __init__(self, text: str) -> None:
        self.content = [_Content(text)]


class _Messages:
    def __init__(self, text: str, delay: float = 0.0) -> None:
        self._text = text
        self._delay = delay

    async def create(self, **_: object) -> _Response:
        if self._delay:
            await asyncio.sleep(self._delay)
        return _Response(self._text)


class FakeAsyncAnthropic:
    def __init__(self, text: str, delay: float = 0.0) -> None:
        self.messages = _Messages(text, delay)


def _patch_llm(monkeypatch, xml: str, delay: float = 0.0) -> None:
    monkeypatch.setattr(
        voice_llm_orchestrator,
        "get_async_anthropic_client",
        lambda: FakeAsyncAnthropic(xml, delay),
    )


# Standard XML fixtures
ACKNOWLEDGE_XML = """
<interviewer_response>
  <action>acknowledge</action>
  <spoken_text>Great answer.</spoken_text>
  <internal_notes>solid</internal_notes>
  <score_update><topic>python</topic><score>7</score><reasoning>good</reasoning></score_update>
  <confidence>0.85</confidence>
  <next_state>questioning</next_state>
  <flags></flags>
</interviewer_response>
"""

FOLLOW_UP_XML = """
<interviewer_response>
  <action>follow_up</action>
  <spoken_text>Can you elaborate?</spoken_text>
  <internal_notes>needs depth</internal_notes>
  <next_state>questioning</next_state>
  <flags></flags>
</interviewer_response>
"""

EVALUATION_JSON = json.dumps({
    "summary": "Good interview.",
    "strengths": ["Knows Python"],
    "weaknesses": ["Shallow on databases"],
    "communication_clarity": {"score": 7, "explanation": "Clear", "evidence": "quote"},
    "technical_depth": {"score": 6, "explanation": "OK", "evidence": "quote"},
    "confidence_consistency": {"score": 7, "explanation": "Steady", "evidence": "quote"},
    "relevance": {"score": 8, "explanation": "On topic", "evidence": "quote"},
    "overall_score": 7.0,
    "best_answer": {"question": "python", "summary": "decorators", "why": "detailed"},
    "weakest_answer": {"question": "databases", "summary": "vague", "why": "no examples"},
    "red_flags": [],
    "hiring_recommendation": "yes",
    "per_question": [],
    "topic_scores": {"python": 7.0},
})


# ===================================================================
# P0: Voice WS never sends the first question after connect
# ===================================================================

class TestFirstQuestionDelivery:
    """After a voice WS connects, the interviewer MUST speak the first
    question so the candidate knows what to answer.  Without this the
    interview is stuck at INITIALIZING — the candidate hears nothing and
    the silence monitor never starts (it only starts after a bot
    response)."""

    @pytest.mark.asyncio
    async def test_voice_ws_sends_first_question_after_connect(self):
        """The server should send the first question text via a 'turn'
        event with speaker='bot' shortly after the WS connect event."""
        questions = [make_question("q1", "python"), make_question("q2", "databases")]
        session_id = "s-first-q"
        seed_voice_session(session_id, questions)

        ws = FakeWebSocket()

        # Simulate what voice_ws.py does after accept:
        # 1. send connected event
        # 2. send transcript_sync
        # Then... nothing. The first question is never sent.

        session = get_voice_session(session_id)
        assert session is not None

        # After connection, we expect the state to advance from INITIALIZING
        # and the first question to appear in the WS messages.
        # This is what SHOULD happen — build the expected behavior:

        # The server should have sent a turn event with the first question
        first_q_text = questions[0].question_text  # "Tell me about python."

        # Check that the voice session transcript includes the first question
        # as a bot turn (i.e., the server asked it).
        transcript_raw = json.loads(session.get("transcript", "[]"))

        # ASSERTION: The first question must be in the transcript after connect.
        # This FAILS because the current code never sends/records the first question.
        assert any(
            t.get("speaker") == "bot" and first_q_text in t.get("text", "")
            for t in transcript_raw
        ), (
            f"First question '{first_q_text}' was never sent to the candidate. "
            f"Transcript is empty: {transcript_raw}. "
            f"Session state is still '{session.get('state')}' — interview is stuck."
        )

    @pytest.mark.asyncio
    async def test_voice_session_state_advances_past_initializing(self):
        """When a voice session is created with questions, the state must
        advance past INITIALIZING to WAITING_FOR_CANDIDATE so the
        candidate can begin speaking."""
        questions = [make_question("q1", "python")]
        session_id = "s-init-stuck"
        seed_voice_session(session_id, questions)

        session = get_voice_session(session_id)
        assert session["state"] == "WAITING_FOR_CANDIDATE", (
            f"Expected WAITING_FOR_CANDIDATE after session creation with questions, "
            f"got '{session['state']}'"
        )


# ===================================================================
# P1: create_voice_session missing started_at timestamp
# ===================================================================

class TestVoiceSessionTimestamps:
    """Voice sessions must have a started_at timestamp so that reports
    can compute duration and the admin page shows correct data."""

    def test_create_voice_session_sets_started_at(self):
        """create_voice_session must record the time the session began."""
        session_id = "s-timestamps"
        create_voice_session(
            session_id=session_id,
            candidate_name="Alice",
            job_role="backend",
            experience_level="mid",
            required_skills=["python"],
            questions_json="[]",
        )
        session = get_voice_session(session_id)
        assert session is not None

        # FAILS: create_voice_session never sets started_at
        started = session.get("started_at")
        assert started is not None, (
            "Voice session has no started_at timestamp. "
            "Reports will show started_at=None and duration_seconds=None."
        )

    def test_voice_session_started_at_is_valid_iso_timestamp(self):
        """started_at must be a parseable ISO 8601 timestamp."""
        session_id = "s-ts-format"
        create_voice_session(
            session_id=session_id,
            candidate_name="Bob",
            job_role="frontend",
            experience_level="junior",
            required_skills=[],
            questions_json="[]",
        )
        session = get_voice_session(session_id)
        started = session.get("started_at")

        # FAILS because started_at is None
        assert started is not None, "started_at is missing"

        from datetime import datetime
        try:
            datetime.fromisoformat(started)
        except (ValueError, TypeError) as exc:
            pytest.fail(f"started_at '{started}' is not a valid ISO timestamp: {exc}")


# ===================================================================
# P2: Final evaluation blocks the spoken response on last question
# ===================================================================

class TestEvaluationDoesNotBlockResponse:
    """When the candidate answers the last question, run_llm_turn must
    return the spoken_text quickly.  The evaluation pipeline (which makes
    a SECOND LLM call) must not block the return of the completion message.

    Currently _trigger_final_evaluation is awaited inside run_llm_turn,
    so the candidate hears nothing for the entire evaluation duration."""

    @pytest.mark.asyncio
    async def test_final_evaluation_does_not_block_spoken_response(self, monkeypatch):
        """run_llm_turn should return the spoken text within a short time
        even when the evaluation takes long."""
        questions = [make_question("q1", "python")]
        seed_voice_session("s-eval-block", questions)

        _patch_llm(monkeypatch, ACKNOWLEDGE_XML)

        # Replace _trigger_final_evaluation with a slow mock that simulates
        # a real evaluation pipeline (LLM call + metrics + DB save = ~10s).
        eval_started = asyncio.Event()

        async def _slow_evaluation(session_id: str, voice_data: dict) -> None:
            eval_started.set()
            await asyncio.sleep(5.0)
            set_voice_field(session_id, "state", "COMPLETE")

        monkeypatch.setattr(
            voice_llm_orchestrator,
            "_trigger_final_evaluation",
            _slow_evaluation,
        )

        start = time.monotonic()
        spoken = await run_llm_turn("s-eval-block", "Decorators are great for caching.")
        elapsed = time.monotonic() - start

        # FAILS: run_llm_turn does `await _trigger_final_evaluation(...)` inline,
        # so it blocks for the full 5s evaluation before returning the spoken text.
        # The fix: fire-and-forget the evaluation (asyncio.create_task) so the
        # spoken response returns immediately.
        assert elapsed < 2.0, (
            f"run_llm_turn took {elapsed:.1f}s — it blocked on the evaluation pipeline. "
            f"The candidate heard nothing for {elapsed:.1f}s after their last answer."
        )
        assert spoken is not None and len(spoken) > 0


# ===================================================================
# P3: questions_answered count exceeds total_questions
# ===================================================================

class TestMetricsAccuracy:
    """_compute_metrics must produce accurate counts.  Currently
    questions_answered counts ALL candidate transcript turns, which is
    wrong when follow-ups produce multiple candidate turns per question."""

    def test_questions_answered_does_not_exceed_total_questions(self):
        """questions_answered should never be greater than total_questions."""
        voice_data: dict[str, Any] = {
            "questions": json.dumps([
                {"id": "q1", "topic": "python", "difficulty": "medium",
                 "question_type": "conceptual", "experience_level": "mid",
                 "question_text": "Tell me about python.", "rubric": {}, "tags": []},
                {"id": "q2", "topic": "databases", "difficulty": "medium",
                 "question_type": "conceptual", "experience_level": "mid",
                 "question_text": "Tell me about databases.", "rubric": {}, "tags": []},
            ]),
            "transcript": json.dumps([
                # Q1: initial answer + follow-up answer = 2 candidate turns for 1 question
                {"speaker": "bot", "text": "Tell me about python.", "type": "question"},
                {"speaker": "candidate", "text": "I like python.", "type": "candidate"},
                {"speaker": "bot", "text": "Can you elaborate?", "type": "follow_up"},
                {"speaker": "candidate", "text": "It has decorators.", "type": "candidate"},
                # Q2: initial answer + follow-up answer = 2 more candidate turns
                {"speaker": "bot", "text": "Tell me about databases.", "type": "question"},
                {"speaker": "candidate", "text": "SQL is important.", "type": "candidate"},
                {"speaker": "bot", "text": "What about NoSQL?", "type": "follow_up"},
                {"speaker": "candidate", "text": "MongoDB is popular.", "type": "candidate"},
            ]),
            "follow_up_count": "2",
            "turn_count": "4",
            "barge_in_count": "0",
            "silence_strikes": "0",
            "running_scores": json.dumps({"python": 7.0, "databases": 6.0}),
        }

        metrics = _compute_metrics(voice_data)

        # There are 2 questions. questions_answered should be <= 2.
        # FAILS: _compute_metrics counts len(candidate_turns) = 4
        assert metrics.questions_answered <= metrics.total_questions, (
            f"questions_answered ({metrics.questions_answered}) exceeds "
            f"total_questions ({metrics.total_questions}). "
            f"_compute_metrics counts ALL candidate turns, not unique questions answered."
        )

    def test_questions_answered_with_silence_prompts(self):
        """Silence prompts should not inflate questions_answered count."""
        voice_data: dict[str, Any] = {
            "questions": json.dumps([
                {"id": "q1", "topic": "python", "difficulty": "medium",
                 "question_type": "conceptual", "experience_level": "mid",
                 "question_text": "Tell me about python.", "rubric": {}, "tags": []},
            ]),
            "transcript": json.dumps([
                {"speaker": "bot", "text": "Tell me about python.", "type": "question"},
                {"speaker": "bot", "text": "Take your time.", "type": "silence_prompt"},
                {"speaker": "candidate", "text": "I like python.", "type": "candidate"},
            ]),
            "follow_up_count": "0",
            "turn_count": "1",
            "barge_in_count": "0",
            "silence_strikes": "0",
            "running_scores": json.dumps({"python": 7.0}),
        }

        metrics = _compute_metrics(voice_data)

        # 1 question, 1 candidate answer — should be 1
        assert metrics.questions_answered == 1, (
            f"Expected 1, got {metrics.questions_answered}"
        )


# ===================================================================
# P4: SQLite singleton connection never health-checked
# ===================================================================

class TestSQLiteConnectionHealth:
    """The global _db connection is set once and never verified.
    If it becomes stale (closed, corrupted), all DB operations silently
    fail and reports stop persisting."""

    @pytest.mark.asyncio
    async def test_sqlite_connection_recovers_after_close(self):
        """If the SQLite connection is closed externally, _get_db should
        detect this and create a new connection."""
        from src.models import interview_report

        # Reset module state
        interview_report._db = None

        # Get a connection (creates the DB)
        db = await interview_report._get_db()
        assert db is not None, "Failed to create initial SQLite connection"

        # Simulate the connection dying
        await db.close()
        # _db still points to the closed connection

        # Try to get the connection again — it should detect the closed
        # connection and create a new one.
        db2 = await interview_report._get_db()

        # FAILS: _get_db checks `if _db is not None` and returns the closed
        # connection. It never checks if the connection is still alive.
        assert db2 is not None, "Failed to get DB after close"

        # The real test: can we actually execute a query?
        try:
            cursor = await db2.execute("SELECT COUNT(*) as cnt FROM interview_reports")
            row = await cursor.fetchone()
            assert row is not None
        except Exception as exc:
            pytest.fail(
                f"SQLite connection is stale after close — query failed: {exc}. "
                f"_get_db returned a closed connection without reconnecting."
            )
        finally:
            # Clean up
            interview_report._db = None
