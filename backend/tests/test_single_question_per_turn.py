"""Tests for the single-question-per-turn guardrail.

Covers:
- validate_single_question in response_parser.py
- System prompt rule presence
"""

import os

import pytest

from src.services.llm.response_parser import validate_single_question

# ---------------------------------------------------------------------------
# validate_single_question — core cases
# ---------------------------------------------------------------------------


def test_single_question_passes_through():
    """A plain single question must be returned unchanged."""
    text = "Can you describe your Python experience?"
    assert validate_single_question(text) == text


def test_compound_question_with_and_also_truncated():
    """Two unrelated questions joined by 'and also' must be truncated."""
    text = "Describe your Python experience, and also tell me about a challenging project?"
    result = validate_single_question(text)
    assert result == "Describe your Python experience?"


def test_and_also_compound_detected():
    """'and also' is a compound conjunction — second question must be dropped."""
    text = "What is your notice period, and also are you open to relocation?"
    result = validate_single_question(text)
    assert result == "What is your notice period?"


def test_as_well_as_compound_detected():
    """'as well as' between two questions must cause truncation after the first '?'."""
    text = "Can you describe your leadership style? As well as how do you handle conflict?"
    result = validate_single_question(text)
    assert result == "Can you describe your leadership style?"


def test_along_with_compound_detected():
    """'along with' between two questions must cause truncation after the first '?'."""
    text = "What is your preferred tech stack, along with why did you choose it?"
    result = validate_single_question(text)
    assert result == "What is your preferred tech stack?"


def test_clarifying_subclause_not_truncated():
    """A single question with a clarifying sub-clause (one '?') must be unchanged."""
    text = "Can you explain X, specifically how Y works?"
    result = validate_single_question(text)
    assert result == text


def test_multiple_question_marks_without_conjunction_truncated():
    """Two '?' with no conjunction keyword still get truncated after the first."""
    text = "Tell me about Python? What frameworks have you used?"
    result = validate_single_question(text)
    assert result == "Tell me about Python?"


def test_empty_string_unchanged():
    """Empty string must pass through without error."""
    assert validate_single_question("") == ""


def test_no_question_mark_unchanged():
    """A bot acknowledgement with no '?' must pass through unchanged."""
    text = "Thank you for your answer. Let's move on."
    assert validate_single_question(text) == text


def test_bare_and_not_falsely_truncated():
    """Bare 'and' in a single question must NOT trigger truncation.
    'What experience do you have with React and Node?' is one question."""
    text = "What experience do you have with React and Node?"
    assert validate_single_question(text) == text


def test_bare_and_listing_two_topics_unchanged():
    """A question listing related items with 'and' is a single question."""
    text = "Can you describe your experience with microservices and distributed systems?"
    assert validate_single_question(text) == text


# ---------------------------------------------------------------------------
# System prompt rule presence
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "src",
    "prompts",
    "system_prompt.txt",
)


def test_system_prompt_contains_single_question_rule():
    """The system prompt must contain an explicit one-question-per-turn rule."""
    with open(_SYSTEM_PROMPT_PATH, encoding="utf-8") as fh:
        content = fh.read()
    assert "ONE question per turn" in content or "exactly ONE question" in content, (
        "system_prompt.txt is missing the single-question-per-turn rule. "
        "Add rule 6 to the ## INTERVIEW RULES section."
    )
