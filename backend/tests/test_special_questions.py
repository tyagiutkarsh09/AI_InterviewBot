"""Fixed, deterministic special questions.

WHY: The behavioral (disagreement) and project deep-dive questions must be
deterministic (no LLM) so the same config yields the same plan. JD questions are
LLM-sourced but need a usable rubric so the existing evaluator path works unchanged.
None may probe family or protected-class topics.
"""
from src.services.interview.special_questions import (
    build_behavioral_question,
    build_project_question,
    build_jd_question,
)

PROTECTED = {"family", "married", "children", "religion", "age", "nationality", "gender"}


def test_behavioral_is_about_disagreement():
    q = build_behavioral_question()
    assert "disagree" in q.question_text.lower()
    assert q.tags == ["behavioral"]
    assert q.rubric  # non-empty rubric


def test_project_is_deep_dive():
    q = build_project_question()
    assert "project" in q.question_text.lower()
    assert q.tags == ["project_deepdive"]
    assert q.rubric


def test_special_questions_are_deterministic():
    assert build_behavioral_question().question_text == build_behavioral_question().question_text
    assert build_project_question().question_text == build_project_question().question_text


def test_special_questions_avoid_protected_topics():
    for q in (build_behavioral_question(), build_project_question()):
        text = q.question_text.lower()
        for word in PROTECTED:
            assert word not in text


def test_build_jd_question_has_rubric_and_tag():
    q = build_jd_question("How would you design a rate limiter?", "rate limiting", index=0)
    assert q.id == "jd_0"
    assert q.question_text == "How would you design a rate limiter?"
    assert q.topic == "rate limiting"
    assert q.tags == ["jd_generated"]
    assert q.rubric
