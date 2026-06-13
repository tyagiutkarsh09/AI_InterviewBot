"""Tests for the interview warm-up flow.

Verifies:
- start_interview returns a non-technical warm-up question
- submit_answer in WARMUP state transitions to QUESTIONING and returns first technical question
- Warm-up answer and both bot turns are recorded in the transcript in order
- generate_warmup_question covers all template categories
- The bot never opens with a technical question
"""

import random
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.services.interview.warmup import generate_warmup_question
from src.types.api import StartInterviewRequest, SubmitAnswerRequest
from src.types.interview import ExperienceLevel, InterviewState, Question, QuestionType

TECHNICAL_KEYWORDS = {"algorithm", "implement", "complexity", "explain how", "design a", "write a"}


def _make_question(qid: str, topic: str) -> Question:
    return Question(
        id=qid,
        topic=topic,
        difficulty="medium",
        question_type=QuestionType.CONCEPTUAL,
        experience_level="mid",
        question_text=f"Explain the {topic} algorithm in detail.",
        rubric={"criteria": []},
    )


_FAKE_QUESTIONS = [
    _make_question("q1", "sorting"),
    _make_question("q2", "graphs"),
    _make_question("q3", "dynamic_programming"),
]

_STORED: dict[str, Any] = {}


def _fake_set_json(key: str, value: Any, ttl: int = 0) -> None:
    _STORED[key] = value


def _fake_get_json(key: str) -> Any:
    return _STORED.get(key)


@pytest.fixture(autouse=True)
def reset_store():
    _STORED.clear()
    yield
    _STORED.clear()


@pytest.fixture()
def redis_patch():
    with (
        patch("src.lib.redis_client.set_json", side_effect=_fake_set_json),
        patch("src.lib.redis_client.get_json", side_effect=_fake_get_json),
        patch("src.services.questions.question_bank.get_question_set", return_value=_FAKE_QUESTIONS),
    ):
        yield


# ---------------------------------------------------------------------------
# generate_warmup_question
# ---------------------------------------------------------------------------

class TestGenerateWarmupQuestion:
    def test_contains_candidate_name(self):
        q = generate_warmup_question("Utkarsh", "backend engineer")
        assert "Utkarsh" in q

    def test_returns_string(self):
        q = generate_warmup_question("Alice", "data scientist")
        assert isinstance(q, str) and len(q) > 0

    def test_all_four_categories_reachable(self):
        seen: set[str] = set()
        for seed in range(200):
            random.seed(seed)
            q = generate_warmup_question("Bob", "frontend engineer")
            seen.add(q)
        assert len(seen) == 4, f"Expected 4 distinct templates, got {len(seen)}: {seen}"

    def test_job_role_interpolated_in_education_template(self):
        results: set[str] = set()
        for seed in range(200):
            random.seed(seed)
            q = generate_warmup_question("Ana", "machine learning")
            results.add(q)
        education_templates = [t for t in results if "machine learning" in t]
        assert education_templates, "Education template (job_role interpolation) never generated"


# ---------------------------------------------------------------------------
# start_interview endpoint
# ---------------------------------------------------------------------------

class TestStartInterview:
    @pytest.mark.asyncio
    async def test_returns_warmup_state(self, redis_patch):
        from src.routes.interview import start_interview
        req = StartInterviewRequest(
            candidate_name="Utkarsh",
            job_role="backend engineer",
            experience_level=ExperienceLevel.MID,
            required_skills=["python"],
        )
        resp = await start_interview(req)
        assert resp.state == InterviewState.WARMUP

    @pytest.mark.asyncio
    async def test_is_warmup_flag_true(self, redis_patch):
        from src.routes.interview import start_interview
        req = StartInterviewRequest(
            candidate_name="Utkarsh",
            job_role="backend engineer",
            experience_level=ExperienceLevel.MID,
            required_skills=["python"],
        )
        resp = await start_interview(req)
        assert resp.is_warmup is True

    @pytest.mark.asyncio
    async def test_topic_is_warmup(self, redis_patch):
        from src.routes.interview import start_interview
        req = StartInterviewRequest(
            candidate_name="Utkarsh",
            job_role="backend engineer",
            experience_level=ExperienceLevel.MID,
            required_skills=["python"],
        )
        resp = await start_interview(req)
        assert resp.topic == "warmup"

    @pytest.mark.asyncio
    async def test_question_number_is_zero(self, redis_patch):
        from src.routes.interview import start_interview
        req = StartInterviewRequest(
            candidate_name="Utkarsh",
            job_role="backend engineer",
            experience_level=ExperienceLevel.MID,
            required_skills=["python"],
        )
        resp = await start_interview(req)
        assert resp.question_number == 0

    @pytest.mark.asyncio
    async def test_warmup_question_not_in_question_bank(self, redis_patch):
        from src.routes.interview import start_interview
        req = StartInterviewRequest(
            candidate_name="Utkarsh",
            job_role="backend engineer",
            experience_level=ExperienceLevel.MID,
            required_skills=["python"],
        )
        resp = await start_interview(req)
        bank_texts = {q.question_text for q in _FAKE_QUESTIONS}
        assert resp.question_text not in bank_texts

    @pytest.mark.asyncio
    async def test_warmup_question_contains_candidate_name(self, redis_patch):
        from src.routes.interview import start_interview
        req = StartInterviewRequest(
            candidate_name="Utkarsh",
            job_role="backend engineer",
            experience_level=ExperienceLevel.MID,
            required_skills=["python"],
        )
        resp = await start_interview(req)
        assert "Utkarsh" in resp.question_text

    @pytest.mark.asyncio
    async def test_warmup_question_is_not_technical(self, redis_patch):
        from src.routes.interview import start_interview
        results: list[str] = []
        for seed in range(20):
            random.seed(seed)
            req = StartInterviewRequest(
                candidate_name="Utkarsh",
                job_role="backend engineer",
                experience_level=ExperienceLevel.MID,
                required_skills=["python"],
            )
            resp = await start_interview(req)
            results.append(resp.question_text.lower())

        for text in results:
            for kw in TECHNICAL_KEYWORDS:
                assert kw not in text, f"Technical keyword '{kw}' found in warmup: {text!r}"


# ---------------------------------------------------------------------------
# submit_answer in WARMUP state
# ---------------------------------------------------------------------------

class TestSubmitWarmupAnswer:
    async def _start(self) -> str:
        from src.routes.interview import start_interview
        req = StartInterviewRequest(
            candidate_name="Utkarsh",
            job_role="backend engineer",
            experience_level=ExperienceLevel.MID,
            required_skills=["python"],
        )
        resp = await start_interview(req)
        return resp.session_id

    @pytest.mark.asyncio
    async def test_transitions_to_questioning(self, redis_patch):
        from src.routes.interview import submit_answer
        session_id = await self._start()
        ans_req = SubmitAnswerRequest(session_id=session_id, answer="It was great, thanks!")
        resp = await submit_answer(ans_req)
        assert resp.state == InterviewState.QUESTIONING

    @pytest.mark.asyncio
    async def test_returns_first_technical_question(self, redis_patch):
        from src.routes.interview import submit_answer
        session_id = await self._start()
        ans_req = SubmitAnswerRequest(session_id=session_id, answer="Good day!")
        resp = await submit_answer(ans_req)
        assert resp.next_question == _FAKE_QUESTIONS[0].question_text

    @pytest.mark.asyncio
    async def test_question_number_becomes_one(self, redis_patch):
        from src.routes.interview import submit_answer
        session_id = await self._start()
        ans_req = SubmitAnswerRequest(session_id=session_id, answer="Good day!")
        resp = await submit_answer(ans_req)
        assert resp.question_number == 1

    @pytest.mark.asyncio
    async def test_is_warmup_false_after_transition(self, redis_patch):
        """Once warmup is consumed, is_warmup should be False — the response
        carries the first technical question, not a warmup."""
        from src.routes.interview import submit_answer
        session_id = await self._start()
        ans_req = SubmitAnswerRequest(session_id=session_id, answer="Good day!")
        resp = await submit_answer(ans_req)
        assert resp.is_warmup is False

    @pytest.mark.asyncio
    async def test_no_score_for_warmup_answer(self, redis_patch):
        from src.routes.interview import submit_answer
        session_id = await self._start()
        ans_req = SubmitAnswerRequest(session_id=session_id, answer="Good day!")
        resp = await submit_answer(ans_req)
        assert resp.score is None

    @pytest.mark.asyncio
    async def test_transcript_order(self, redis_patch):
        """Transcript must be: bot warmup → candidate answer → bot first technical question."""
        from src.routes.interview import submit_answer
        from src.services.interview.session_manager import get_session
        session_id = await self._start()
        ans_req = SubmitAnswerRequest(session_id=session_id, answer="Doing great!")
        await submit_answer(ans_req)

        session = get_session(session_id)
        assert session is not None
        transcript = session.transcript
        assert len(transcript) == 3, f"Expected 3 turns, got {len(transcript)}"
        assert transcript[0].speaker == "bot"
        assert transcript[1].speaker == "candidate"
        assert transcript[1].text == "Doing great!"
        assert transcript[2].speaker == "bot"
        assert transcript[2].text == _FAKE_QUESTIONS[0].question_text

    @pytest.mark.asyncio
    async def test_second_answer_goes_through_normal_flow(self, redis_patch):
        """After warmup, subsequent answers go through turn_manager (normal path)."""
        from unittest.mock import AsyncMock, patch
        from src.routes.interview import submit_answer
        from src.services.interview import turn_manager
        from src.types.interview import InterviewState

        session_id = await self._start()
        # consume warmup
        await submit_answer(SubmitAnswerRequest(session_id=session_id, answer="Good day!"))

        mock_result = MagicMock()
        mock_result.state = InterviewState.QUESTIONING
        mock_result.spoken_text = "Good answer."
        mock_result.score = 7.0
        mock_result.score_reasoning = "solid"
        mock_result.reasoning = "solid"
        mock_result.next_question = _FAKE_QUESTIONS[1].question_text
        mock_result.question_number = 2
        mock_result.total_questions = 3
        mock_result.topic = _FAKE_QUESTIONS[1].topic
        mock_result.is_complete = False

        with patch.object(turn_manager, "process_answer", new=AsyncMock(return_value=mock_result)) as mock_pa:
            await submit_answer(SubmitAnswerRequest(session_id=session_id, answer="Binary search works by..."))
            mock_pa.assert_called_once()
