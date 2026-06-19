"""WRAP_UP state transitions.

WHY: The interview must offer a closing candidate-Q&A phase before evaluation
instead of jumping straight to COMPLETE. WRAP_UP is forward-only — adding a
backward edge would violate the forward-only state-machine invariant.
"""
import pytest
from src.types.interview import InterviewState
from src.services.interview.state_machine import can_transition, transition


def test_questioning_can_enter_wrapup():
    assert can_transition(InterviewState.QUESTIONING, InterviewState.WRAP_UP)


def test_wrapup_self_loop_allowed():
    assert can_transition(InterviewState.WRAP_UP, InterviewState.WRAP_UP)


def test_wrapup_advances_to_evaluating():
    assert can_transition(InterviewState.WRAP_UP, InterviewState.EVALUATING)


def test_wrapup_cannot_go_back_to_questioning():
    assert not can_transition(InterviewState.WRAP_UP, InterviewState.QUESTIONING)


def test_wrapup_cannot_skip_to_complete():
    assert not can_transition(InterviewState.WRAP_UP, InterviewState.COMPLETE)


def test_transition_raises_on_backward_edge():
    with pytest.raises(ValueError):
        transition(InterviewState.WRAP_UP, InterviewState.QUESTIONING)
