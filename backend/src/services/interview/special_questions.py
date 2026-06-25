"""Deterministic question builders for the non-bank plan slots.

Behavioral + project questions are fixed templates (no LLM). JD questions are
LLM-sourced text wrapped into the Question model with a generic competency rubric
so the existing evaluator works unchanged.
"""
from src.types.interview import Question, QuestionType
from src.types.planning import PlannedQuestion

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


def build_project_question(grounded_text: str = "") -> Question:
    return Question(
        id="project_0",
        topic="project deep-dive",
        difficulty="medium",
        question_type=QuestionType.SCENARIO,
        experience_level="all",
        question_text=grounded_text.strip() or _PROJECT_TEXT,
        rubric=_GENERIC_RUBRIC,
        tags=["project_deepdive"],
    )


def build_planned_question(pq: PlannedQuestion, index: int) -> Question:
    """Wrap a planner question into the Question model the run/eval pipeline consumes.

    rubric_keypoints live under rubric["key_points"] so the existing eval prompt
    (which json.dumps(question.rubric)) feeds them to the scorer unchanged.
    """
    return Question(
        id=f"{pq.source}_{index}",
        topic=pq.competency or "role-specific",
        difficulty=pq.difficulty,
        question_type=QuestionType.SCENARIO,
        experience_level="all",
        question_text=pq.question_text,
        rubric={"key_points": pq.rubric_keypoints},
        tags=[f"{pq.source}_generated"],
        time_budget_sec=pq.time_budget_sec,
    )


# Legacy builder for the text/admin-config flow (build_plan). The voice flow uses
# build_planned_question instead.
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
