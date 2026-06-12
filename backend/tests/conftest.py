"""Shared test fixtures.

Forces voice_session into its in-memory fallback (no Redis needed) and
provides a fake WebSocket that records what the server sends.
"""

import json
from typing import Any

import pytest

from src.types.interview import Question, QuestionType


@pytest.fixture(autouse=True)
def memory_voice_session(monkeypatch):
    """Make voice_session use the in-memory dict instead of Redis."""
    from src.services.audio import voice_session

    monkeypatch.setattr(voice_session, "_client", lambda: None)
    voice_session._MEMORY.clear()
    yield
    voice_session._MEMORY.clear()


class FakeWebSocket:
    """Records JSON control frames and binary audio the server pushes."""

    def __init__(self) -> None:
        self.json_messages: list[dict[str, Any]] = []
        self.binary_messages: list[bytes] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.json_messages.append(data)

    async def send_bytes(self, data: bytes) -> None:
        self.binary_messages.append(data)


@pytest.fixture
def fake_ws() -> FakeWebSocket:
    return FakeWebSocket()


def make_question(qid: str, topic: str) -> Question:
    return Question(
        id=qid,
        topic=topic,
        difficulty="medium",
        question_type=QuestionType.CONCEPTUAL,
        experience_level="mid",
        question_text=f"Tell me about {topic}.",
        rubric={"criteria": []},
    )


def seed_voice_session(session_id: str, questions: list[Question]) -> None:
    from src.services.audio.voice_session import create_voice_session

    create_voice_session(
        session_id=session_id,
        candidate_name="Alice",
        job_role="backend",
        experience_level="mid",
        required_skills=["python"],
        questions_json=json.dumps([q.model_dump() for q in questions]),
    )
