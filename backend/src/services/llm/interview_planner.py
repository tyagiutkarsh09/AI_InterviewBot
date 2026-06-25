"""Combined JD+resume interview planner via LLM. Fails loud.

ONE call: reads the JD (primary) and optional resume, returns a structured
InterviewPlanDraft (role title, skills, technical questions with per-question
difficulty + rubric key points + time budget, and a grounded project question).
Sync — callers in the async voice path must offload via asyncio.to_thread, like
analyze_jd. Mirrors jd_analysis.py: extraction/generation only, never routing.
"""
import json
import logging
import os

from src.lib.anthropic_client import get_anthropic_client, get_model_for_task
from src.types.interview import ExperienceLevel
from src.types.planning import InterviewPlanDraft, PlannedQuestion

logger = logging.getLogger(__name__)

_PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "prompts", "interview_planner_prompt.txt"
)
_VALID_DIFFICULTY = {"easy", "medium", "hard"}


class PlannerError(RuntimeError):
    """Raised when the planner cannot produce a usable plan."""


def plan_interview(
    jd_text: str,
    resume_text: str | None,
    job_role: str,
    experience_level: ExperienceLevel,
    num_questions: int,
) -> InterviewPlanDraft:
    with open(_PROMPT_PATH, encoding="utf-8") as f:
        template = f.read()
    prompt = (
        template
        .replace("{num_questions}", str(num_questions))
        .replace("{experience_level}", experience_level.value)
        .replace("{job_role}", job_role)
        .replace("{jd_text}", jd_text)
        .replace("{resume_text}", resume_text or "(none provided)")
    )

    try:
        client = get_anthropic_client()
        response = client.messages.create(
            model=get_model_for_task("planner"),
            max_tokens=2500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
    except Exception as exc:
        logger.error("Interview planner LLM call failed: %s", exc)
        raise PlannerError(f"Interview planner LLM call failed: {exc}") from exc

    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise PlannerError("Planner returned no JSON object")
    try:
        data = json.loads(raw[start:end])
    except json.JSONDecodeError as exc:
        raise PlannerError(f"Planner returned invalid JSON: {exc}") from exc

    questions: list[PlannedQuestion] = []
    for q in data.get("questions", []):
        text = str(q.get("question_text", "")).strip()
        if not text:
            continue
        difficulty = str(q.get("difficulty", "medium")).lower()
        if difficulty not in _VALID_DIFFICULTY:
            difficulty = "medium"
        source = "resume" if str(q.get("source", "")).lower() == "resume" else "jd"
        try:
            budget = int(q.get("time_budget_sec", 120))
        except (TypeError, ValueError):
            budget = 120
        budget = max(45, min(budget, 240))
        questions.append(PlannedQuestion(
            competency=str(q.get("competency", "")).strip() or "role-specific",
            source=source,
            question_text=text,
            difficulty=difficulty,
            rubric_keypoints=[str(k).strip() for k in q.get("rubric_keypoints", []) if str(k).strip()][:5],
            time_budget_sec=budget,
        ))

    if not questions:
        raise PlannerError("Planner produced no usable questions")

    return InterviewPlanDraft(
        role_title=str(data.get("role_title", "")).strip() or job_role,
        skills=[str(s).strip() for s in data.get("skills", []) if str(s).strip()][:8],
        questions=questions,
        project_question_text=str(data.get("project_question_text", "")).strip(),
    )
