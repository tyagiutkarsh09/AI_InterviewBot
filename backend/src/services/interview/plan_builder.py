"""Assemble a frozen, deterministic InterviewPlan from config inputs.

Order: [core...] -> [jd...] -> behavioral -> project_deepdive.
Fails loud if the bank cannot supply the required core count or there are not
enough JD question ideas.
"""
from src.services.interview.plan_math import compute_split
from src.services.interview.special_questions import (
    build_behavioral_question,
    build_jd_question,
    build_project_question,
)
from src.services.questions.question_bank import get_question_set
from src.types.config import InterviewPlan, JDSummary
from src.types.interview import ExperienceLevel


class InsufficientQuestionsError(RuntimeError):
    """Raised when the plan cannot be fully populated."""


def build_plan(
    role: str,
    experience_level: ExperienceLevel,
    jd_summary: JDSummary,
    jd_question_ideas: list[dict],
    total_questions: int,
    core_ratio: float,
) -> InterviewPlan:
    core_count, jd_count = compute_split(total_questions, core_ratio)

    core_qs = get_question_set(role, experience_level, jd_summary.skills, core_count)
    if len(core_qs) < core_count:
        raise InsufficientQuestionsError(
            f"Bank supplied {len(core_qs)} core questions, need {core_count}"
        )
    core_qs = core_qs[:core_count]

    if len(jd_question_ideas) < jd_count:
        raise InsufficientQuestionsError(
            f"Have {len(jd_question_ideas)} JD ideas, need {jd_count}"
        )
    jd_qs = [
        build_jd_question(idea["question_text"], idea.get("topic", ""), index=i)
        for i, idea in enumerate(jd_question_ideas[:jd_count])
    ]

    questions = core_qs + jd_qs + [build_behavioral_question(), build_project_question()]
    return InterviewPlan(questions=questions)
