from datetime import datetime, timezone
from typing import Optional
from src.types.interview import SessionState, Question, QuestionResult, InterviewState
from src.services.interview.state_machine import next_state_for_answer
from src.services.interview import session_manager
from src.services.llm import llm_service


class TurnResult:
    def __init__(
        self,
        state: InterviewState,
        spoken_text: str,
        score: Optional[float],
        score_reasoning: Optional[str],
        next_question: Optional[str],
        question_number: Optional[int],
        total_questions: Optional[int],
        topic: Optional[str],
        is_complete: bool,
    ):
        self.state = state
        self.spoken_text = spoken_text
        self.score = score
        self.score_reasoning = score_reasoning
        self.next_question = next_question
        self.question_number = question_number
        self.total_questions = total_questions
        self.topic = topic
        self.is_complete = is_complete


async def process_answer(session: SessionState, answer_text: str) -> TurnResult:
    current_q = session.questions[session.current_question_idx]

    session_manager.record_turn(
        session, speaker="candidate", text=answer_text, question_id=current_q.id
    )

    llm_result = await llm_service.evaluate_answer(
        question=current_q,
        answer=answer_text,
        session=session,
    )

    _record_question_result(session, current_q, answer_text, llm_result)

    if llm_result.score is not None:
        session.running_scores[current_q.topic] = llm_result.score

    if llm_result.flags:
        session.flags.extend(llm_result.flags)

    next_state = next_state_for_answer(
        session.current_question_idx, len(session.questions)
    )

    if next_state == InterviewState.EVALUATING:
        session.state = InterviewState.EVALUATING
        session_manager.update_session(session)
        return TurnResult(
            state=session.state,
            spoken_text=llm_result.spoken_text,
            score=llm_result.score,
            score_reasoning=llm_result.reasoning,
            next_question=None,
            question_number=None,
            total_questions=len(session.questions),
            topic=current_q.topic,
            is_complete=False,
        )

    session_manager.advance_question(session)
    next_q = session.questions[session.current_question_idx]
    session.state = InterviewState.QUESTIONING
    session_manager.update_session(session)

    session_manager.record_turn(
        session, speaker="bot", text=next_q.question_text, question_id=next_q.id
    )

    return TurnResult(
        state=session.state,
        spoken_text=llm_result.spoken_text,
        score=llm_result.score,
        score_reasoning=llm_result.reasoning,
        next_question=next_q.question_text,
        question_number=session.current_question_idx + 1,
        total_questions=len(session.questions),
        topic=next_q.topic,
        is_complete=False,
    )


def _record_question_result(
    session: SessionState,
    question: Question,
    answer_text: str,
    llm_result: "llm_service.EvaluationResult",
) -> None:
    result = QuestionResult(
        question_id=question.id,
        question_text=question.question_text,
        topic=question.topic,
        answer_text=answer_text,
        score=llm_result.score,
        score_reasoning=llm_result.reasoning,
        follow_up_count=session.follow_up_count,
    )
    session.question_results.append(result)
