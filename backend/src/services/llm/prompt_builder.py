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

    key_points = question.rubric.get("key_points") if isinstance(question.rubric, dict) else None
    key_points_text = json.dumps(key_points) if key_points else json.dumps(question.rubric)

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
  Expected answer key_points: {key_points_text}
</current_question>

<conversation_history>
{transcript_text}
</conversation_history>

<running_scores>
{json.dumps(session.running_scores, indent=2)}
</running_scores>

<turn_instruction>
Score the candidate's answer for topic "{question.topic}" by checking it against the
expected key_points above — reward the points they actually covered, not confident phrasing.
The score MUST reflect only what the CANDIDATE said: if they did not know, score low even if
you go on to explain the answer. Never let your own explanation raise the score.

Then decide the next action:
- If the answer is comprehensive, set action "acknowledge" (or "evaluating" if last question).
- If a key point is missing and probing is worthwhile, set action "follow_up" and put ONE
  follow-up in spoken_text that targets the MISSING key point, pitched to a {session.experience_level.value}
  candidate. Stay within this question's topic — never introduce a topic outside the job description.
- If the candidate concedes or has been probed enough, acknowledge warmly ("no worries"),
  optionally give a brief 2-3 sentence explanation built from the key_points (more for juniors,
  usually skip for seniors), and prepare to move on.
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
