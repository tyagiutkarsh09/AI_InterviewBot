"""Bank eligibility capacity per level.

WHY: With no JD uploaded, every technical question comes from the bank. The count
selector must be capped to what the candidate's level can actually draw, or the
plan builder will raise InsufficientQuestionsError after the admin has already
uploaded. This helper must mirror get_question_set's exact eligibility gate.
"""
from src.services.questions.question_bank import eligible_question_count
from src.types.interview import ExperienceLevel


def test_junior_capacity_matches_bank():
    # Bank today: 3 junior + 2 "all" are eligible for a junior candidate.
    assert eligible_question_count(ExperienceLevel.JUNIOR) == 5


def test_higher_levels_have_more_capacity():
    jr = eligible_question_count(ExperienceLevel.JUNIOR)
    mid = eligible_question_count(ExperienceLevel.MID)
    senior = eligible_question_count(ExperienceLevel.SENIOR)
    assert mid > jr           # mid can also draw mid-level questions
    assert senior >= mid
