import logging
from fastapi import APIRouter, HTTPException, status
from src.types.api import (
    StartInterviewRequest,
    StartInterviewResponse,
    SubmitAnswerRequest,
    SubmitAnswerResponse,
    GetReportResponse,
)
from src.types.interview import InterviewState, FinalReport
from src.services.interview import session_manager, turn_manager
from src.services.llm import llm_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/interview", tags=["interview"])


@router.post("/start", response_model=StartInterviewResponse, status_code=status.HTTP_201_CREATED)
async def start_interview(body: StartInterviewRequest) -> StartInterviewResponse:
    session = session_manager.create_session(
        candidate_name=body.candidate_name,
        job_role=body.job_role,
        experience_level=body.experience_level,
        required_skills=body.required_skills,
    )

    if not session.questions:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No questions available for the selected role and level.",
        )

    session.state = InterviewState.QUESTIONING
    first_q = session.questions[0]
    session_manager.update_session(session)
    session_manager.record_turn(session, speaker="bot", text=first_q.question_text, question_id=first_q.id)

    return StartInterviewResponse(
        session_id=session.session_id,
        state=session.state,
        question_text=first_q.question_text,
        question_number=1,
        total_questions=len(session.questions),
        topic=first_q.topic,
        candidate_name=session.candidate_name,
    )


@router.post("/answer", response_model=SubmitAnswerResponse)
async def submit_answer(body: SubmitAnswerRequest) -> SubmitAnswerResponse:
    session = session_manager.get_session(body.session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")

    if session.state == InterviewState.COMPLETE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Interview is already complete.")

    if session.state == InterviewState.EVALUATING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Interview is being evaluated.")

    if session.state not in (InterviewState.QUESTIONING, InterviewState.STARTED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot submit answer in state: {session.state.value}",
        )

    result = await turn_manager.process_answer(session, body.answer)

    if result.state == InterviewState.EVALUATING:
        evaluation = await llm_service.generate_final_evaluation(session)
        session = session_manager.get_session(body.session_id)
        session.evaluation = evaluation
        session.state = InterviewState.COMPLETE
        session_manager.end_session(session)

        return SubmitAnswerResponse(
            session_id=body.session_id,
            state=InterviewState.COMPLETE,
            score=result.score,
            score_reasoning=result.score_reasoning,
            is_complete=True,
            feedback=evaluation.summary,
        )

    return SubmitAnswerResponse(
        session_id=body.session_id,
        state=result.state,
        score=result.score,
        score_reasoning=result.score_reasoning,
        next_question=result.next_question,
        question_number=result.question_number,
        total_questions=result.total_questions,
        topic=result.topic,
        is_complete=False,
    )


@router.get("/report/{session_id}", response_model=GetReportResponse)
async def get_report(session_id: str) -> GetReportResponse:
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")

    if session.state != InterviewState.COMPLETE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Interview is not complete yet. Current state: {session.state.value}",
        )

    eval_ = session.evaluation
    if eval_ is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Evaluation missing.")

    started = session.started_at
    ended = session.ended_at
    duration = None
    if started and ended:
        from datetime import datetime
        duration = int(
            (datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds()
        )

    return GetReportResponse(
        session_id=session_id,
        candidate_name=session.candidate_name,
        job_role=session.job_role,
        experience_level=session.experience_level.value,
        overall_score=eval_.overall_score,
        recommendation=eval_.recommendation,
        strengths=eval_.strengths,
        weaknesses=eval_.weaknesses,
        summary=eval_.summary,
        per_question=[qr.model_dump() for qr in eval_.per_question],
        topic_scores=eval_.topic_scores,
        transcript=[t.model_dump() for t in session.transcript],
        started_at=started,
        ended_at=ended,
        duration_seconds=duration,
    )
