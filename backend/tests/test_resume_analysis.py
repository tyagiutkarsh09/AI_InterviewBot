"""Resume analysis extraction (LLM).

WHY: Resume analysis is extraction (allowed LLM use). It must parse the model's
JSON into (skills, resume question dicts) and FAIL LOUD (raise) on LLM/parse
failure so the interview is not started half-built. It must never surface
protected-class probing — enforced by the prompt and asserted at the call site.
"""
from unittest.mock import MagicMock, patch
import pytest
from src.services.llm.resume_analysis import analyze_resume, ResumeAnalysisError


def _mock_response(text: str):
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    return resp


VALID_JSON = """
{
  "skills": ["python", "kubernetes"],
  "resume_questions": [
    {"question_text": "Walk me through the billing service you built at Acme.", "topic": "billing"},
    {"question_text": "You list Kubernetes — describe a rollout you owned.", "topic": "kubernetes"}
  ]
}
"""


def test_analyze_resume_parses_skills_and_questions():
    client = MagicMock()
    client.messages.create.return_value = _mock_response(VALID_JSON)
    with patch("src.services.llm.resume_analysis.get_anthropic_client", return_value=client):
        skills, questions = analyze_resume("Acme — Backend Engineer. Python, Kubernetes.")
    assert skills == ["python", "kubernetes"]
    assert len(questions) == 2
    assert questions[0]["topic"] == "billing"


def test_analyze_resume_raises_on_malformed_output():
    client = MagicMock()
    client.messages.create.return_value = _mock_response("not json")
    with patch("src.services.llm.resume_analysis.get_anthropic_client", return_value=client):
        with pytest.raises(ResumeAnalysisError):
            analyze_resume("resume text")


def test_analyze_resume_raises_on_client_error():
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("API down")
    with patch("src.services.llm.resume_analysis.get_anthropic_client", return_value=client):
        with pytest.raises(ResumeAnalysisError):
            analyze_resume("resume text")


def test_analyze_resume_truncates_to_requested_count():
    over = """
    {
      "skills": ["go"],
      "resume_questions": [
        {"question_text": "Q1", "topic": "a"},
        {"question_text": "Q2", "topic": "b"},
        {"question_text": "Q3", "topic": "c"}
      ]
    }
    """
    client = MagicMock()
    client.messages.create.return_value = _mock_response(over)
    with patch("src.services.llm.resume_analysis.get_anthropic_client", return_value=client):
        _, questions = analyze_resume("resume text", num_questions=2)
    assert len(questions) == 2
    assert [q["question_text"] for q in questions] == ["Q1", "Q2"]


def test_analyze_resume_raises_when_no_questions():
    client = MagicMock()
    client.messages.create.return_value = _mock_response('{"skills": ["go"], "resume_questions": []}')
    with patch("src.services.llm.resume_analysis.get_anthropic_client", return_value=client):
        with pytest.raises(ResumeAnalysisError):
            analyze_resume("resume text")
