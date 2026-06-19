"""Frozen plan assembly.

WHY: The plan must be deterministic given fixed inputs, honor the 80/20 split with
floors, place the 2 special questions last in fixed order, and fail loud if the bank
cannot supply enough core questions.
"""
from unittest.mock import patch

import pytest

from src.services.interview.plan_builder import build_plan, InsufficientQuestionsError
from src.types.config import JDSummary
from src.types.interview import ExperienceLevel, Question, QuestionType


def _bank_q(qid: str) -> Question:
    return Question(
        id=qid, topic=f"topic_{qid}", difficulty="medium",
        question_type=QuestionType.CONCEPTUAL, experience_level="mid",
        question_text=f"Bank question {qid}", rubric={"criteria": []}, tags=["core"],
    )


_JD_IDEAS = [
    {"question_text": "JD Q1", "topic": "t1"},
    {"question_text": "JD Q2", "topic": "t2"},
    {"question_text": "JD Q3", "topic": "t3"},
]
_SUMMARY = JDSummary(skills=["python"], responsibilities=["x"], seniority_signals=["mid"])


def _build(total=6):
    # get_question_set returns exactly `count` bank questions
    def fake_get_question_set(role, level, skills, count):
        return [_bank_q(f"c{i}") for i in range(count)]

    with patch("src.services.interview.plan_builder.get_question_set", side_effect=fake_get_question_set):
        return build_plan(
            role="backend engineer", experience_level=ExperienceLevel.MID,
            jd_summary=_SUMMARY, jd_question_ideas=_JD_IDEAS,
            total_questions=total, core_ratio=0.8,
        )


def test_plan_question_count_matches_total():
    plan = _build(total=6)
    assert len(plan.questions) == 6


def test_plan_order_core_jd_behavioral_project():
    plan = _build(total=6)  # core=3, jd=1, +behavioral +project
    tags = [q.tags[0] for q in plan.questions]
    assert tags == ["core", "core", "core", "jd_generated", "behavioral", "project_deepdive"]


def test_plan_is_deterministic():
    a = _build(total=6)
    b = _build(total=6)
    assert [q.question_text for q in a.questions] == [q.question_text for q in b.questions]


def test_plan_fails_loud_on_insufficient_bank_questions():
    def short_bank(role, level, skills, count):
        return [_bank_q("c0")]  # only 1, fewer than requested
    with patch("src.services.interview.plan_builder.get_question_set", side_effect=short_bank):
        with pytest.raises(InsufficientQuestionsError):
            build_plan(
                role="r", experience_level=ExperienceLevel.MID, jd_summary=_SUMMARY,
                jd_question_ideas=_JD_IDEAS, total_questions=6, core_ratio=0.8,
            )


def test_plan_fails_loud_on_insufficient_jd_ideas():
    def fake_get_question_set(role, level, skills, count):
        return [_bank_q(f"c{i}") for i in range(count)]
    with patch("src.services.interview.plan_builder.get_question_set", side_effect=fake_get_question_set):
        with pytest.raises(InsufficientQuestionsError):
            build_plan(
                role="r", experience_level=ExperienceLevel.MID, jd_summary=_SUMMARY,
                jd_question_ideas=[], total_questions=6, core_ratio=0.8,
            )
