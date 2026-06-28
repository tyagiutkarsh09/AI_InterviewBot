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
{f"""This is a behavioral/project question — do NOT emit <score_update>. Listen to the
candidate's story, acknowledge warmly, and move on. No scoring is needed.

Then decide the next action:
- If the answer is sufficient, set action "acknowledge" (or "evaluating" if last question).
- If you want to hear more detail, set action "follow_up" and put ONE follow-up in spoken_text.
- If the candidate concedes or has been probed enough, acknowledge warmly and prepare to move on.""" if question.id.startswith(("behavioral_", "project_")) else f"""Score the candidate's answer for topic "{question.topic}" by checking it against the
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
  usually skip for seniors), and prepare to move on."""}
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


def build_voice_system_prompt() -> str:
    return _load_prompt("voice_system_prompt.txt")


def build_voice_answer_evaluation_prompt(
    question: Question,
    answer: str,
    session: SessionState,
) -> str:
    """Voice-mode evaluation prompt.

    Identical structure to build_answer_evaluation_prompt but with B′ turn
    instruction: the LLM drives a multi-turn exchange and the code enforces
    clamps. Text-mode build_answer_evaluation_prompt is NOT touched.
    """
    recent_turns = session.transcript[-6:]
    transcript_text = _format_transcript(recent_turns)

    key_points = question.rubric.get("key_points") if isinstance(question.rubric, dict) else None
    key_points_text = json.dumps(key_points) if key_points else json.dumps(question.rubric)

    turn_instruction = f"""The candidate may have asked you a clarifying question, gone off-topic, asked for
time to think, or given a partial or complete answer. Read what just happened and
choose the ONE action that fits:

- answer_clarification: the candidate asked YOU about the question's meaning, scope,
  or an assumption. Answer briefly and naturally — do NOT score; do NOT emit
  <score_update>; do NOT advance.

- accept_thinking: the candidate asked for time to think or said they need a moment.
  Give a brief warm acknowledgement ("Of course, take your time."). Do NOT score;
  do NOT move on.

- redirect: the candidate went off-topic or misunderstood the question. Gently steer
  back. Do NOT score.

- follow_up: the answer is partial and there is a specific key point from the rubric
  worth probing. Ask ONE focused follow-up. Do NOT emit <score_update> yet.

- acknowledge_advance: the candidate has answered (or conceded, or been probed
  enough). Keep spoken_text to a brief acknowledgement — no questions.{f"""
  This is a behavioral/project question — do NOT emit <score_update>. Just acknowledge
  warmly and move on.""" if question.id.startswith(("behavioral_", "project_")) else f"""
  This is the ONLY action that records a score. Emit <score_update> for topic "{question.topic}"
  scoring the WHOLE exchange against the key_points listed above; the score must
  reflect only what the CANDIDATE said. Do NOT let your own explanation inflate it.
  Calibrate depth of expectation to a {session.experience_level.value} candidate —
  award partial credit for direction-correct answers."""}

Never emit <score_update> on any action other than acknowledge_advance."""

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
{turn_instruction}
</turn_instruction>

Candidate's answer: {answer}
"""
