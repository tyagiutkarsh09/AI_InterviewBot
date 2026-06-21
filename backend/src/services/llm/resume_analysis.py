"""Resume analysis via LLM (extraction). Fails loud on error.

Returns (skills, resume question dicts). Raises ResumeAnalysisError on any LLM or
parse failure so the caller refuses to start a half-built interview. Mirrors
jd_analysis.py — extraction only (allowed LLM use), never routing.
"""
import json
import logging
import os

from src.lib.anthropic_client import get_anthropic_client, get_model_for_task

logger = logging.getLogger(__name__)

_PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "prompts", "resume_analysis_prompt.txt"
)


class ResumeAnalysisError(RuntimeError):
    """Raised when resume analysis cannot produce a usable result."""


def analyze_resume(resume_text: str, num_questions: int = 2) -> tuple[list[str], list[dict]]:
    with open(_PROMPT_PATH, encoding="utf-8") as f:
        template = f.read()
    prompt = template.replace("{num_questions}", str(num_questions)).replace(
        "{resume_text}", resume_text
    )

    try:
        client = get_anthropic_client()
        response = client.messages.create(
            model=get_model_for_task("jd_analysis"),  # extraction task -> Haiku
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
    except Exception as exc:
        logger.error("Resume analysis LLM call failed: %s", exc)
        raise ResumeAnalysisError(f"Resume analysis LLM call failed: {exc}") from exc

    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise ResumeAnalysisError("Resume analysis returned no JSON object")
    try:
        data = json.loads(raw[start:end])
    except json.JSONDecodeError as exc:
        raise ResumeAnalysisError(f"Resume analysis returned invalid JSON: {exc}") from exc

    skills = [str(s) for s in data.get("skills", [])][:8]
    questions = [
        {"question_text": str(q.get("question_text", "")).strip(),
         "topic": str(q.get("topic", "")).strip()}
        for q in data.get("resume_questions", [])
        if str(q.get("question_text", "")).strip()
    ]
    if not questions:
        raise ResumeAnalysisError("Resume analysis produced no usable questions")
    # Cap to the requested count — the prompt asks for exactly num_questions, but the
    # model can over-produce; the plan budgets a fixed number of resume slots.
    return skills, questions[:num_questions]
