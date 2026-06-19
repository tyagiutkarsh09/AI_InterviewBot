"""Constrained candidate Q&A for the WRAP_UP phase.

The LLM may answer ONLY from the provided JD/config context. Anything it cannot
answer from that context → a fixed recruiter-clarify line. This is drafting from
provided context (allowed), never routing.
"""
import json
import logging

from src.lib.anthropic_client import get_anthropic_client, get_model_for_task

logger = logging.getLogger(__name__)

MAX_OUTRO_QUESTIONS = 3
RECRUITER_FALLBACK = "That's a great question — the recruiter can clarify that for you."

_SYSTEM = (
    "You are wrapping up a job interview and answering the candidate's questions about "
    "the role. Answer ONLY using the job context provided below. If the answer is not "
    "contained in that context, reply EXACTLY with: " + RECRUITER_FALLBACK + " "
    "Keep answers to 1-3 sentences, warm and professional."
)


def answer_candidate_question(question: str, job_role: str, jd_summary: dict) -> str:
    context = json.dumps({"job_role": job_role, "jd_summary": jd_summary or {}})
    try:
        client = get_anthropic_client()
        response = client.messages.create(
            model=get_model_for_task("interview"),
            max_tokens=300,
            system=_SYSTEM,
            messages=[{"role": "user", "content": f"Job context: {context}\n\nCandidate question: {question}"}],
        )
        text = response.content[0].text.strip()
        return text or RECRUITER_FALLBACK
    except Exception as exc:
        logger.error("Outro answer failed: %s", exc)
        return RECRUITER_FALLBACK
