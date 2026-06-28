from src.types.interview import Question, QuestionType


def test_question_carries_optional_time_budget():
    # time_budget_sec is the soft per-question pacing hint the planner emits;
    # it must be optional so bank/legacy questions (which have none) still load.
    q = Question(
        id="jd_0", topic="GD&T", difficulty="medium",
        question_type=QuestionType.SCENARIO, experience_level="all",
        question_text="Walk me through a tolerance stack-up you analyzed.",
        time_budget_sec=150,
    )
    assert q.time_budget_sec == 150

    legacy = Question(
        id="q1", topic="x", difficulty="easy", question_type=QuestionType.CONCEPTUAL,
        experience_level="all", question_text="hi",
    )
    assert legacy.time_budget_sec is None
