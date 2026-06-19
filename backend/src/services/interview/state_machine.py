from src.types.interview import InterviewState

_TRANSITIONS: dict[InterviewState, set[InterviewState]] = {
    InterviewState.IDLE: {InterviewState.STARTED},
    InterviewState.STARTED: {InterviewState.WARMUP, InterviewState.QUESTIONING},
    InterviewState.WARMUP: {InterviewState.QUESTIONING},
    InterviewState.QUESTIONING: {
        InterviewState.QUESTIONING,
        InterviewState.WRAP_UP,
        InterviewState.EVALUATING,
    },
    InterviewState.WRAP_UP: {InterviewState.WRAP_UP, InterviewState.EVALUATING},
    InterviewState.EVALUATING: {InterviewState.COMPLETE},
    InterviewState.COMPLETE: set(),
}


def can_transition(current: InterviewState, target: InterviewState) -> bool:
    return target in _TRANSITIONS.get(current, set())


def transition(current: InterviewState, target: InterviewState) -> InterviewState:
    if not can_transition(current, target):
        raise ValueError(
            f"Invalid state transition: {current.value} -> {target.value}"
        )
    return target


def next_state_for_answer(
    current_question_idx: int, total_questions: int
) -> InterviewState:
    if current_question_idx + 1 >= total_questions:
        return InterviewState.EVALUATING
    return InterviewState.QUESTIONING
