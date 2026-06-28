from src.services.llm.prompt_builder import build_answer_evaluation_prompt, build_system_prompt
from src.types.interview import Question, QuestionType, SessionState, ExperienceLevel


def _session(level):
    return SessionState(session_id="s", candidate_name="A", job_role="ME",
                        experience_level=level, questions=[], required_skills=["GD&T"])


def test_system_prompt_has_persona_and_teaching_not_hint_ban():
    p = build_system_prompt()
    assert "20 years" in p
    assert "never give away answers or provide hints" not in p
    assert "no worries" in p.lower() or "concede" in p.lower()


def test_eval_prompt_surfaces_keypoints_and_experience():
    q = Question(id="jd_0", topic="GD&T", difficulty="hard", question_type=QuestionType.SCENARIO,
                 experience_level="all", question_text="Explain stack-ups.",
                 rubric={"key_points": ["datum order", "modifiers"]})
    prompt = build_answer_evaluation_prompt(q, "some answer", _session(ExperienceLevel.SENIOR))
    assert "datum order" in prompt
    assert "senior" in prompt
    assert "key_points" in prompt or "key points" in prompt.lower()
