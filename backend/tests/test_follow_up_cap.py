from src.services.interview.voice_llm_orchestrator import max_follow_ups_for
from src.types.interview import Question, QuestionType


def _q(diff):
    return Question(id="x", topic="t", difficulty=diff, question_type=QuestionType.SCENARIO,
                    experience_level="all", question_text="q")


def test_hard_question_allows_two_follow_ups():
    assert max_follow_ups_for(_q("hard")) == 2


def test_non_hard_questions_allow_one():
    assert max_follow_ups_for(_q("medium")) == 1
    assert max_follow_ups_for(_q("easy")) == 1
