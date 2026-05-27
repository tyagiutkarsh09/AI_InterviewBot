export type InterviewState =
  | "idle"
  | "started"
  | "questioning"
  | "evaluating"
  | "complete";

export type ExperienceLevel = "junior" | "mid" | "senior" | "staff";

export interface StartInterviewRequest {
  candidate_name: string;
  job_role: string;
  experience_level: ExperienceLevel;
  required_skills: string[];
}

export interface StartInterviewResponse {
  session_id: string;
  state: InterviewState;
  question_text: string;
  question_number: number;
  total_questions: number;
  topic: string;
  candidate_name: string;
}

export interface SubmitAnswerRequest {
  session_id: string;
  answer: string;
}

export interface SubmitAnswerResponse {
  session_id: string;
  state: InterviewState;
  score: number | null;
  score_reasoning: string | null;
  next_question: string | null;
  question_number: number | null;
  total_questions: number | null;
  topic: string | null;
  is_complete: boolean;
  feedback: string | null;
}

export interface QuestionResult {
  question_id: string;
  question_text: string;
  topic: string;
  answer_text: string;
  score: number | null;
  score_reasoning: string | null;
  follow_up_count: number;
}

export interface TranscriptTurn {
  turn_idx: number;
  speaker: string;
  text: string;
  timestamp: string;
  question_id: string | null;
}

export interface ReportResponse {
  session_id: string;
  candidate_name: string;
  job_role: string;
  experience_level: string;
  overall_score: number;
  recommendation: string;
  strengths: string[];
  weaknesses: string[];
  summary: string;
  per_question: QuestionResult[];
  topic_scores: Record<string, number>;
  transcript: TranscriptTurn[];
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
}

export interface ApiError {
  error?: string;
  detail?: string | { msg: string; type: string }[];
}
