"""Deterministic question builders for the non-bank plan slots.

Behavioral + project questions are fixed templates (no LLM). JD questions are
LLM-sourced text wrapped into the Question model with a generic competency rubric
so the existing evaluator works unchanged.
"""
from src.types.interview import Question, QuestionType

_GENERIC_RUBRIC = {
    "criteria": [
        "Clarity and structure of the answer",
        "Concrete, specific examples over generalities",
        "Depth of reasoning and trade-off awareness",
    ]
}

_BEHAVIORAL_TEXT = (
    "Tell me about a time you disagreed with a colleague on a technical decision. "
    "How did you handle it, and what was the outcome?"
)

_PROJECT_TEXT = (
    "Walk me through a recent project you're proud of — what problem it solved, "
    "the key technical decisions you made, and what you'd do differently now."
)


def build_behavioral_question() -> Question:
    return Question(
        id="behavioral_0",
        topic="collaboration",
        difficulty="medium",
        question_type=QuestionType.BEHAVIORAL,
        experience_level="all",
        question_text=_BEHAVIORAL_TEXT,
        rubric=_GENERIC_RUBRIC,
        tags=["behavioral"],
    )


def build_project_question() -> Question:
    return Question(
        id="project_0",
        topic="project deep-dive",
        difficulty="medium",
        question_type=QuestionType.SCENARIO,
        experience_level="all",
        question_text=_PROJECT_TEXT,
        rubric=_GENERIC_RUBRIC,
        tags=["project_deepdive"],
    )


def build_jd_question(question_text: str, topic: str, index: int) -> Question:
    return Question(
        id=f"jd_{index}",
        topic=topic or "role-specific",
        difficulty="medium",
        question_type=QuestionType.CONCEPTUAL,
        experience_level="all",
        question_text=question_text,
        rubric=_GENERIC_RUBRIC,
        tags=["jd_generated"],
    )
