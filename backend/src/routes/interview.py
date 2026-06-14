import logging
from datetime import datetime
from typing import Optional

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
from src.services.interview.warmup import generate_warmup_question, generate_warmup_followup, generate_transition_message, generate_introduction
from src.services.llm import llm_service
from src.models.interview_report import (
    InterviewReport,
    InterviewMetrics,
    InterviewAnalysis,
    get_report_by_session,
    save_report as save_report_to_pg,
)

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

    session.state = InterviewState.WARMUP
    intro = generate_introduction(session.candidate_name, session.job_role, len(session.questions))
    warmup_text = generate_warmup_question(session.candidate_name, session.job_role)
    opening = f"{intro} {warmup_text}"
    session_manager.update_session(session)
    session_manager.record_turn(session, speaker="bot", text=opening)

    return StartInterviewResponse(
        session_id=session.session_id,
        state=session.state,
        question_text=opening,
        question_number=0,
        total_questions=len(session.questions),
        topic="warmup",
        candidate_name=session.candidate_name,
        is_warmup=True,
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

    if session.state not in (InterviewState.QUESTIONING, InterviewState.STARTED, InterviewState.WARMUP):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot submit answer in state: {session.state.value}",
        )

    if session.state == InterviewState.WARMUP:
        session_manager.record_turn(session, speaker="candidate", text=body.answer)

        if session.warmup_turns_completed == 0:
            followup = generate_warmup_followup(session.candidate_name, session.job_role)
            session.warmup_turns_completed = 1
            session_manager.update_session(session)
            session_manager.record_turn(session, speaker="bot", text=followup)
            return SubmitAnswerResponse(
                session_id=body.session_id,
                state=InterviewState.WARMUP,
                next_question=followup,
                question_number=0,
                total_questions=len(session.questions),
                topic="warmup",
                is_complete=False,
                is_warmup=True,
            )

        session.state = InterviewState.QUESTIONING
        first_q = session.questions[0]
        transition = generate_transition_message(session.candidate_name)
        combined_text = f"{transition} {first_q.question_text}"
        session_manager.update_session(session)
        session_manager.record_turn(session, speaker="bot", text=combined_text, question_id=first_q.id)
        return SubmitAnswerResponse(
            session_id=body.session_id,
            state=InterviewState.QUESTIONING,
            next_question=combined_text,
            question_number=1,
            total_questions=len(session.questions),
            topic=first_q.topic,
            is_complete=False,
        )

    result = await turn_manager.process_answer(session, body.answer)

    if result.state == InterviewState.EVALUATING:
        evaluation = await llm_service.generate_final_evaluation(session)
        session = session_manager.get_session(body.session_id)
        session.evaluation = evaluation
        session.state = InterviewState.COMPLETE
        session_manager.end_session(session)

        # Persist to PostgreSQL for history
        confidences = [
            qr.confidence for qr in session.question_results
            if qr.confidence is not None
        ]
        avg_eval_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        text_metrics = InterviewMetrics(
            total_questions=len(session.questions),
            questions_answered=len(session.question_results),
            total_candidate_words=sum(
                len(t.text.split()) for t in session.transcript if t.speaker == "candidate"
            ),
            total_bot_words=sum(
                len(t.text.split()) for t in session.transcript if t.speaker == "bot"
            ),
            follow_ups_used=session.follow_up_count,
            avg_transcription_confidence=1.0,
            avg_evaluation_confidence=round(avg_eval_confidence, 3),
            qa_extraction_confidence=1.0,
        )

        text_analysis = InterviewAnalysis(
            summary=evaluation.summary,
            strengths=evaluation.strengths,
            weaknesses=evaluation.weaknesses,
            overall_score=evaluation.overall_score,
            hiring_recommendation=evaluation.recommendation,
            per_question=[qr.model_dump() for qr in evaluation.per_question],
            topic_scores=evaluation.topic_scores,
        )

        started = session.started_at
        ended = session.ended_at
        duration = None
        if started and ended:
            duration = int(
                (datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds()
            )

        text_report = InterviewReport(
            session_id=session.session_id,
            candidate_name=session.candidate_name,
            job_role=session.job_role,
            experience_level=session.experience_level.value,
            interview_type="text",
            started_at=started,
            ended_at=ended,
            duration_seconds=duration,
            transcript=[t.model_dump() for t in session.transcript],
            metrics=text_metrics,
            analysis=text_analysis,
        )

        await save_report_to_pg(text_report)

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


def _report_response_from_interview_report(
    session_id: str, report: InterviewReport,
) -> GetReportResponse:
    return GetReportResponse(
        session_id=session_id,
        candidate_name=report.candidate_name,
        job_role=report.job_role,
        experience_level=report.experience_level,
        overall_score=report.analysis.overall_score,
        recommendation=report.analysis.hiring_recommendation,
        strengths=report.analysis.strengths,
        weaknesses=report.analysis.weaknesses,
        summary=report.analysis.summary,
        per_question=report.analysis.per_question,
        topic_scores=report.analysis.topic_scores,
        transcript=report.transcript,
        started_at=report.started_at,
        ended_at=report.ended_at,
        duration_seconds=report.duration_seconds,
    )


@router.get("/report/{session_id}", response_model=GetReportResponse)
async def get_report(session_id: str) -> GetReportResponse:
    logger.info("Report lookup started session=%s", session_id)
    # Try text-mode session first (existing behavior)
    session = session_manager.get_session(session_id)
    if session is not None:
        logger.info("Report lookup matched text session=%s state=%s", session_id, session.state.value)
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
        duration: Optional[int] = None
        if started and ended:
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

    # Try voice session (Redis evaluation_report field)
    from src.services.audio.voice_session import get_voice_session
    voice_data = get_voice_session(session_id)

    if voice_data is not None:
        state = voice_data.get("state", "")
        logger.info("Report lookup matched voice session=%s state=%s", session_id, state)
        if state == "EVALUATING":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Interview is being evaluated.",
            )
        if state != "COMPLETE":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Interview is not complete yet. Current state: {state}",
            )

        report_json = voice_data.get("evaluation_report")
        if report_json:
            logger.info("Report lookup returning Redis voice report session=%s", session_id)
            report = InterviewReport.model_validate_json(report_json)
            return _report_response_from_interview_report(session_id, report)

    # Try PG as last resort
    pg_report = await get_report_by_session(session_id)
    if pg_report is not None:
        logger.info("Report lookup returning persisted report session=%s", session_id)
        return _report_response_from_interview_report(session_id, pg_report)

    logger.warning("Report lookup failed session=%s", session_id)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
