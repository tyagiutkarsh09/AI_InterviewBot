"""Assemble a frozen, deterministic InterviewPlan from config inputs.

Order: [core...] -> [jd...] -> behavioral -> project_deepdive.
Fails loud if the bank cannot supply the required core count or there are not
enough JD question ideas.
"""
from src.services.interview.plan_math import compute_split
from src.services.interview.special_questions import (
    build_behavioral_question,
    build_jd_question,
    build_planned_question,
    build_project_question,
)
from src.services.questions.question_bank import get_question_set
from src.types.config import InterviewPlan, JDSummary
from src.types.interview import ExperienceLevel, Question
from src.types.planning import InterviewPlanDraft


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


_DIFFICULTY_RANK = {"easy": 0, "medium": 1, "hard": 2}


def order_easy_first(questions: list[Question]) -> list[Question]:
    """Return questions with the two easiest moved to the front (stable), rest in order."""
    if len(questions) <= 2:
        return list(questions)
    ranked = sorted(
        range(len(questions)),
        key=lambda i: (_DIFFICULTY_RANK.get(questions[i].difficulty.lower(), 1), i),
    )
    lead_idx = set(ranked[:2])
    lead = [questions[i] for i in ranked[:2]]
    rest = [q for i, q in enumerate(questions) if i not in lead_idx]
    return lead + rest


def assemble_voice_plan(draft: InterviewPlanDraft, usable_count: int) -> InterviewPlan:
    """Draft -> frozen plan: easy-first technical (jd+resume) -> behavioral -> project.

    usable_count caps the technical questions (the floor math decides it). Behavioral
    is fixed; the project deep-dive is grounded in the draft's project_question_text.
    """
    technical = [
        build_planned_question(pq, index=i)
        for i, pq in enumerate(draft.questions[:usable_count])
    ]
    technical = order_easy_first(technical)
    questions = technical + [
        build_behavioral_question(),
        build_project_question(draft.project_question_text),
    ]
    return InterviewPlan(questions=questions)
