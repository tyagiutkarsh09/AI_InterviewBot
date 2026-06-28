"""
Regression tests for the voice evaluation prompt — running scores injection.

Guards against the "double-pass scoring" bug where the evaluator cold-re-scores
every answer from the transcript instead of using the live per-answer scores
that were recorded during the interview.
"""

import json

from tests.conftest import seed_voice_session
from src.services.audio.voice_session import get_voice_session, set_voice_field
from src.services.interview.voice_evaluation import _build_prompt, _compute_metrics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _prompt_for(session_id: str, scores: dict) -> str:
    """Seed a session with the given running scores and return the rendered prompt."""
    set_voice_field(session_id, "running_scores", json.dumps(scores))
    voice_data = get_voice_session(session_id)
    metrics = _compute_metrics(voice_data)
    return _build_prompt(voice_data, metrics)


# ---------------------------------------------------------------------------
# Core regression guard
# ---------------------------------------------------------------------------


def test_running_scores_appear_in_rendered_prompt():
    """Live per-topic scores MUST be injected into the evaluation prompt.

    This is the primary regression guard for the double-pass scoring bug.
    Before the fix, running_scores was never passed to .format(), so the
    evaluator had no live scores and cold-re-scored every answer — producing
    scores that were systematically stricter than the live per-answer scoring.
    This test fails against the unfixed code because the score values do not
    appear in the rendered prompt at all.
    """
    session_id = "eval-scores-inject"
    seed_voice_session(session_id, [])
    prompt = _prompt_for(session_id, {"python": 7.0, "sql": 6.0})

    assert "python" in prompt, "topic 'python' missing from rendered prompt"
    assert "7" in prompt, "score '7' missing from rendered prompt"
    assert "sql" in prompt, "topic 'sql' missing from rendered prompt"
    assert "6" in prompt, "score '6' missing from rendered prompt"


# ---------------------------------------------------------------------------
# Calibration instruction
# ---------------------------------------------------------------------------


def test_partial_credit_calibration_instruction_present():
    """The prompt must instruct the evaluator to award partial credit and
    calibrate to the candidate's stated experience level.

    Spoken interviews warrant more generous partial credit than written ones
    because real-time verbal recall is harder than written recall.
    """
    session_id = "eval-calibration"
    seed_voice_session(session_id, [])
    prompt = _prompt_for(session_id, {"python": 8.0})

    prompt_lower = prompt.lower()
    assert "partial credit" in prompt_lower, (
        "partial-credit instruction missing from prompt"
    )
    assert "experience level" in prompt_lower, (
        "experience-level calibration instruction missing from prompt"
    )


# ---------------------------------------------------------------------------
# No-re-derive instruction
# ---------------------------------------------------------------------------


def test_no_re_derive_instruction_present():
    """The prompt must explicitly instruct the evaluator NOT to re-derive
    per-question scores from the transcript.

    Without this guard, an LLM will silently ignore the provided live scores
    and re-score from scratch, causing the double-pass discrepancy.
    """
    session_id = "eval-no-re-derive"
    seed_voice_session(session_id, [])
    prompt = _prompt_for(session_id, {"python": 5.0})

    prompt_lower = prompt.lower()
    assert "authoritative" in prompt_lower or "do not re-derive" in prompt_lower, (
        "Prompt must state that running scores are AUTHORITATIVE and must not be "
        "re-derived from the transcript"
    )


# ---------------------------------------------------------------------------
# Sentinel path — empty / absent running scores
# ---------------------------------------------------------------------------


def test_build_prompt_does_not_raise_when_running_scores_empty():
    """_build_prompt must succeed and emit a clear sentinel when running_scores
    is an empty JSON object (the default for a freshly created session).

    Also guards against KeyError / IndexError inside .format() when no
    placeholder value is supplied.
    """
    session_id = "eval-no-scores"
    seed_voice_session(session_id, [])
    # seed_voice_session initialises running_scores to "{}" — do not override
    voice_data = get_voice_session(session_id)
    metrics = _compute_metrics(voice_data)

    # Must not raise
    prompt = _build_prompt(voice_data, metrics)

    assert "(none recorded)" in prompt, (
        "sentinel text '(none recorded)' missing when running_scores is empty"
    )


def test_build_prompt_does_not_raise_when_running_scores_explicit_empty():
    """Same as above but explicitly sets running_scores to '{}' to be explicit
    that the sentinel path works regardless of how the empty state arrived."""
    session_id = "eval-empty-scores"
    seed_voice_session(session_id, [])
    set_voice_field(session_id, "running_scores", "{}")
    voice_data = get_voice_session(session_id)
    metrics = _compute_metrics(voice_data)

    prompt = _build_prompt(voice_data, metrics)

    assert "(none recorded)" in prompt, (
        "sentinel text '(none recorded)' missing for explicitly empty running_scores"
    )
