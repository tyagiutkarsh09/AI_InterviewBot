"""Fixed, deterministic special questions + planner-question wrapping.

WHY: The behavioral (disagreement) and project deep-dive questions must be
deterministic (no LLM) so the same config yields the same plan. Planner-sourced
questions are wrapped into the Question model carrying their generated difficulty,
key points, and time budget so the evaluator scores against them. None may probe
family or protected-class topics.
"""
from src.services.interview.special_questions import (
    build_behavioral_question,
    build_planned_question,
    build_project_question,
)
from src.types.planning import PlannedQuestion

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


def test_build_planned_question_preserves_planner_metadata():
    pq = PlannedQuestion(
        competency="GD&T", source="jd", question_text="Explain datum referencing.",
        difficulty="hard", rubric_keypoints=["datum order", "modifiers"], time_budget_sec=180,
    )
    q = build_planned_question(pq, index=2)
    assert q.id == "jd_2"
    assert q.topic == "GD&T"
    assert q.difficulty == "hard"
    assert q.rubric == {"key_points": ["datum order", "modifiers"]}
    assert q.time_budget_sec == 180
    assert q.tags == ["jd_generated"]


def test_build_planned_question_resume_source_tags_and_id():
    pq = PlannedQuestion(
        competency="payments", source="resume",
        question_text="Walk me through the payments service you built.",
        difficulty="medium", rubric_keypoints=["scale"], time_budget_sec=120,
    )
    q = build_planned_question(pq, index=0)
    assert q.id == "resume_0"
    assert q.tags == ["resume_generated"]


def test_build_project_question_uses_grounded_text_when_given():
    q = build_project_question("Walk me through the fixture you designed at Acme.")
    assert "fixture" in q.question_text
    # falls back to the generic template when text is empty
    assert build_project_question("").question_text
