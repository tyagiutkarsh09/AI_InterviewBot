from enum import Enum
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field


class InterviewState(str, Enum):
    IDLE = "idle"
    STARTED = "started"
    QUESTIONING = "questioning"
    EVALUATING = "evaluating"
    COMPLETE = "complete"


class ExperienceLevel(str, Enum):
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    STAFF = "staff"


class QuestionType(str, Enum):
    CONCEPTUAL = "conceptual"
    CODING = "coding"
    BEHAVIORAL = "behavioral"
    SCENARIO = "scenario"


class Question(BaseModel):
    id: str
    topic: str
    difficulty: str
    question_type: QuestionType
    experience_level: str
    question_text: str
    follow_up_texts: list[str] = Field(default_factory=list)
    rubric: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class TurnRecord(BaseModel):
    turn_idx: int
    speaker: str
    text: str
    timestamp: str
    question_id: Optional[str] = None


class ScoreUpdate(BaseModel):
    topic: str
    score: float = Field(ge=0, le=10)
    reasoning: str


class QuestionResult(BaseModel):
    question_id: str
    question_text: str
    topic: str
    answer_text: str
    score: Optional[float] = None
    score_reasoning: Optional[str] = None
    follow_up_count: int = 0
    time_spent_seconds: Optional[int] = None


class Evaluation(BaseModel):
    overall_score: float = Field(ge=0, le=10)
    recommendation: str
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    summary: str
    per_question: list[QuestionResult] = Field(default_factory=list)
    topic_scores: dict[str, float] = Field(default_factory=dict)


class SessionState(BaseModel):
    session_id: str
    state: InterviewState = InterviewState.IDLE
    candidate_name: str
    job_role: str
    experience_level: ExperienceLevel
    required_skills: list[str] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    current_question_idx: int = 0
    transcript: list[TurnRecord] = Field(default_factory=list)
    question_results: list[QuestionResult] = Field(default_factory=list)
    running_scores: dict[str, float] = Field(default_factory=dict)
    flags: list[str] = Field(default_factory=list)
    follow_up_count: int = 0
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    evaluation: Optional[Evaluation] = None


class FinalReport(BaseModel):
    session_id: str
    candidate_name: str
    job_role: str
    experience_level: str
    evaluation: Evaluation
    transcript: list[TurnRecord]
    started_at: Optional[str]
    ended_at: Optional[str]
    duration_seconds: Optional[int]
