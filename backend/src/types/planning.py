from pydantic import BaseModel, Field


class PlannedQuestion(BaseModel):
    competency: str                         # which JD/resume skill this probes
    source: str                             # "jd" | "resume"
    question_text: str
    difficulty: str                         # "easy" | "medium" | "hard"
    rubric_keypoints: list[str] = Field(default_factory=list)  # 3-5 expected points
    time_budget_sec: int = 120              # soft pacing hint


class InterviewPlanDraft(BaseModel):
    role_title: str                         # derived from JD, for intro + report
    skills: list[str] = Field(default_factory=list)
    questions: list[PlannedQuestion] = Field(default_factory=list)  # technical, jd+resume
    project_question_text: str = ""         # JD/resume-grounded project deep-dive
