import json
from unittest.mock import patch, MagicMock

import pytest

from src.services.llm.interview_planner import plan_interview, PlannerError
from src.types.interview import ExperienceLevel


def _fake_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.content = [MagicMock(text=json.dumps(payload))]
    return resp


_GOOD = {
    "role_title": "Sr. Mechanical Design Engineer",
    "skills": ["GD&T", "Creo", "NPD"],
    "questions": [
        {"competency": "Creo", "source": "jd", "question_text": "Do you use Creo daily?",
         "difficulty": "easy", "rubric_keypoints": ["named modules", "real parts"], "time_budget_sec": 60},
        {"competency": "GD&T", "source": "jd", "question_text": "Walk me through a tolerance stack-up.",
         "difficulty": "hard", "rubric_keypoints": ["datum order", "modifiers", "inspection"], "time_budget_sec": 180},
    ],
    "project_question_text": "Walk me through a medical-device part you designed end to end.",
}


def test_plan_interview_parses_structured_plan():
    client = MagicMock()
    client.messages.create.return_value = _fake_response(_GOOD)
    with patch("src.services.llm.interview_planner.get_anthropic_client", return_value=client):
        draft = plan_interview("a JD", None, "Mechanical Engineer", ExperienceLevel.SENIOR, num_questions=2)
    assert draft.role_title == "Sr. Mechanical Design Engineer"
    assert [q.source for q in draft.questions] == ["jd", "jd"]
    assert draft.questions[1].rubric_keypoints == ["datum order", "modifiers", "inspection"]
    assert draft.project_question_text


def test_plan_interview_raises_on_no_questions():
    bad = {"role_title": "X", "skills": [], "questions": [], "project_question_text": "p"}
    client = MagicMock()
    client.messages.create.return_value = _fake_response(bad)
    with patch("src.services.llm.interview_planner.get_anthropic_client", return_value=client):
        with pytest.raises(PlannerError):
            plan_interview("thin jd", None, "role", ExperienceLevel.MID, num_questions=5)


def test_plan_interview_raises_on_malformed_json():
    client = MagicMock()
    client.messages.create.return_value = MagicMock(content=[MagicMock(text="not json")])
    with patch("src.services.llm.interview_planner.get_anthropic_client", return_value=client):
        with pytest.raises(PlannerError):
            plan_interview("jd", None, "role", ExperienceLevel.MID, num_questions=5)
