import json
import logging
from dataclasses import dataclass, field
from typing import Optional
from src.types.interview import SessionState, Question, Evaluation
from src.lib.anthropic_client import get_anthropic_client, get_model_for_task
from src.services.llm.prompt_builder import (
    build_system_prompt,
    build_answer_evaluation_prompt,
    build_final_evaluation_prompt,
)
from src.services.llm.response_parser import parse_xml_response

logger = logging.getLogger(__name__)


@dataclass
class EvaluationResult:
    spoken_text: str
    score: Optional[float]
    reasoning: Optional[str]
    flags: list[str] = field(default_factory=list)
    internal_notes: str = ""


async def evaluate_answer(
    question: Question,
    answer: str,
    session: SessionState,
) -> EvaluationResult:
    system_prompt = build_system_prompt()
    user_prompt = build_answer_evaluation_prompt(question, answer, session)

    try:
        client = get_anthropic_client()
        response = client.messages.create(
            model=get_model_for_task("interview"),
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw_text = response.content[0].text
        parsed = parse_xml_response(raw_text)

        return EvaluationResult(
            spoken_text=parsed.spoken_text or _fallback_acknowledgement(parsed.score),
            score=parsed.score,
            reasoning=parsed.reasoning,
            flags=parsed.flags,
            internal_notes=parsed.internal_notes,
        )
    except Exception as exc:
        logger.error("LLM evaluation failed: %s", exc)
        return EvaluationResult(
            spoken_text="Thank you for your answer. Let's continue.",
            score=None,
            reasoning=None,
            flags=[],
        )


async def generate_final_evaluation(session: SessionState) -> Evaluation:
    prompt = build_final_evaluation_prompt(session)

    try:
        client = get_anthropic_client()
        response = client.messages.create(
            model=get_model_for_task("evaluation"),
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON found in evaluation response")

        data = json.loads(raw[start:end])

        return Evaluation(
            overall_score=_clamp(float(data.get("overall_score", 5.0))),
            recommendation=data.get("recommendation", "no"),
            strengths=data.get("strengths", []),
            weaknesses=data.get("weaknesses", []),
            summary=data.get("summary", ""),
            per_question=session.question_results,
            topic_scores={k: _clamp(float(v)) for k, v in data.get("topic_scores", {}).items()},
        )
    except Exception as exc:
        logger.error("Final evaluation failed: %s", exc)
        return _fallback_evaluation(session)


def _clamp(value: float) -> float:
    return max(0.0, min(10.0, value))


def _fallback_acknowledgement(score: Optional[float]) -> str:
    if score is None:
        return "Thank you for your answer. Let's move on."
    if score >= 8:
        return "Excellent answer! You demonstrated strong understanding. Let's continue."
    if score >= 6:
        return "Good answer. You covered the main points. Let's continue."
    if score >= 4:
        return "Thank you. There are some areas to explore further. Let's continue."
    return "Thank you for your response. Let's continue."


def _fallback_evaluation(session: SessionState) -> Evaluation:
    scores = [qr.score for qr in session.question_results if qr.score is not None]
    overall = sum(scores) / len(scores) if scores else 5.0

    return Evaluation(
        overall_score=round(overall, 2),
        recommendation="no" if overall < 6 else "yes",
        strengths=["Completed the interview"],
        weaknesses=["Evaluation could not be generated automatically"],
        summary="Automated evaluation was unavailable. Please review transcript manually.",
        per_question=session.question_results,
        topic_scores=session.running_scores,
    )
