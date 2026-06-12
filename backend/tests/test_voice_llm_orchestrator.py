"""Tests for run_llm_turn — the per-turn LLM step.

These also pin the async-client contract: the orchestrator must `await` an
async Anthropic client (a blocking sync call would freeze the event loop).
"""

import pytest

from tests.conftest import seed_voice_session, make_question

from src.services.audio.voice_session import get_voice_session
from src.services.interview import voice_llm_orchestrator
from src.services.interview.voice_llm_orchestrator import run_llm_turn


class _Content:
    def __init__(self, text: str) -> None:
        self.text = text


class _Response:
    def __init__(self, text: str) -> None:
        self.content = [_Content(text)]


class _Messages:
    def __init__(self, text: str) -> None:
        self._text = text

    async def create(self, **_: object) -> _Response:
        return _Response(self._text)


class FakeAsyncAnthropic:
    def __init__(self, text: str) -> None:
        self.messages = _Messages(text)


def _patch_llm(monkeypatch, xml: str) -> None:
    monkeypatch.setattr(
        voice_llm_orchestrator,
        "get_async_anthropic_client",
        lambda: FakeAsyncAnthropic(xml),
    )


ACKNOWLEDGE_XML = """
<interviewer_response>
  <action>acknowledge</action>
  <spoken_text>Thanks, that's clear.</spoken_text>
  <internal_notes>solid answer</internal_notes>
  <score_update><topic>python</topic><score>8</score><reasoning>good</reasoning></score_update>
  <next_state>questioning</next_state>
  <flags></flags>
</interviewer_response>
"""

FOLLOW_UP_XML = """
<interviewer_response>
  <action>follow_up</action>
  <spoken_text>Can you give a concrete example?</spoken_text>
  <internal_notes>needs depth</internal_notes>
  <next_state>questioning</next_state>
  <flags></flags>
</interviewer_response>
"""


@pytest.mark.asyncio
async def test_acknowledge_advances_to_next_question_and_records_score(monkeypatch):
    seed_voice_session(
        "s-ack",
        [make_question("q1", "python"), make_question("q2", "databases")],
    )
    _patch_llm(monkeypatch, ACKNOWLEDGE_XML)

    spoken = await run_llm_turn("s-ack", "I used decorators for caching.")

    session = get_voice_session("s-ack")
    assert int(session["current_question_idx"]) == 1
    import json
    assert json.loads(session["running_scores"]) == {"python": 8.0}
    assert "Thanks, that's clear." in spoken
    assert "databases" in spoken  # next question text appended


@pytest.mark.asyncio
async def test_follow_up_stays_on_question_and_increments_count(monkeypatch):
    seed_voice_session(
        "s-fu",
        [make_question("q1", "python"), make_question("q2", "databases")],
    )
    _patch_llm(monkeypatch, FOLLOW_UP_XML)

    spoken = await run_llm_turn("s-fu", "It's faster.")

    session = get_voice_session("s-fu")
    assert int(session["current_question_idx"]) == 0  # unchanged
    assert int(session["follow_up_count"]) == 1
    assert spoken == "Can you give a concrete example?"
