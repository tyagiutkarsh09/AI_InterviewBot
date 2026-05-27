import os
import json
from typing import Optional
from src.types.interview import SessionState, Question, TurnRecord

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "prompts")


def _load_prompt(filename: str) -> str:
    path = os.path.join(_PROMPTS_DIR, filename)
    with open(path, encoding="utf-8") as f:
        return f.read()


def build_system_prompt() -> str:
    return _load_prompt("system_prompt.txt")


def build_answer_evaluation_prompt(
    question: Question,
    answer: str,
    session: SessionState,
) -> str:
    recent_turns = session.transcript[-6:]
    transcript_text = _format_transcript(recent_turns)

    return f"""
<candidate_info>
  Name: {session.candidate_name}
  Role: {session.job_role}
  Level: {session.experience_level.value}
  Required skills: {', '.join(session.required_skills) or 'general'}
</candidate_info>

<interview_progress>
  Current question: {session.current_question_idx + 1} of {len(session.questions)}
  Follow-ups used: {session.follow_up_count}
</interview_progress>

<current_question>
  Question: {question.question_text}
  Topic: {question.topic}
  Difficulty: {question.difficulty}
  Rubric: {json.dumps(question.rubric)}
</current_question>

<conversation_history>
{transcript_text}
</conversation_history>

<running_scores>
{json.dumps(session.running_scores, indent=2)}
</running_scores>

<turn_instruction>
The candidate just answered the current question. Evaluate their answer, provide a score for the topic "{question.topic}", and determine whether to ask a follow-up or acknowledge and prepare to move on.

If the answer is comprehensive and sufficient, set action to "acknowledge" and next_state to "questioning" (or "evaluating" if this is the last question).
If the answer needs elaboration, set action to "follow_up" and include a follow-up question in spoken_text.
</turn_instruction>

Candidate's answer: {answer}
"""


def build_final_evaluation_prompt(session: SessionState) -> str:
    template = _load_prompt("evaluation_prompt.txt")

    question_results_text = json.dumps(
        [qr.model_dump() for qr in session.question_results], indent=2
    )
    transcript_text = _format_transcript(session.transcript)

    return (
        template
        .replace("{candidate_name}", session.candidate_name)
        .replace("{job_role}", session.job_role)
        .replace("{experience_level}", session.experience_level.value)
        .replace("{question_results}", question_results_text)
        .replace("{transcript}", transcript_text)
    )


def _format_transcript(turns: list[TurnRecord]) -> str:
    if not turns:
        return "(no conversation yet)"
    lines = []
    for t in turns:
        prefix = "INTERVIEWER" if t.speaker == "bot" else "CANDIDATE"
        lines.append(f"{prefix}: {t.text}")
    return "\n".join(lines)
