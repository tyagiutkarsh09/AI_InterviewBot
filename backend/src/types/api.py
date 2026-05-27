from typing import Optional
from pydantic import BaseModel, Field
from .interview import ExperienceLevel, InterviewState


class StartInterviewRequest(BaseModel):
    candidate_name: str = Field(min_length=1, max_length=100)
    job_role: str = Field(min_length=1, max_length=100)
    experience_level: ExperienceLevel
    required_skills: list[str] = Field(default_factory=list)


class StartInterviewResponse(BaseModel):
    session_id: str
    state: InterviewState
    question_text: str
    question_number: int
    total_questions: int
    topic: str
    candidate_name: str


class SubmitAnswerRequest(BaseModel):
    session_id: str
    answer: str = Field(min_length=1, max_length=10000)


class SubmitAnswerResponse(BaseModel):
    session_id: str
    state: InterviewState
    score: Optional[float] = None
    score_reasoning: Optional[str] = None
    next_question: Optional[str] = None
    question_number: Optional[int] = None
    total_questions: Optional[int] = None
    topic: Optional[str] = None
    is_complete: bool = False
    feedback: Optional[str] = None


class GetReportResponse(BaseModel):
    session_id: str
    candidate_name: str
    job_role: str
    experience_level: str
    overall_score: float
    recommendation: str
    strengths: list[str]
    weaknesses: list[str]
    summary: str
    per_question: list[dict]
    topic_scores: dict[str, float]
    transcript: list[dict]
    started_at: Optional[str]
    ended_at: Optional[str]
    duration_seconds: Optional[int]


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
