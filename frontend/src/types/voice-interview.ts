export type VoiceCaptureState = "idle" | "speaking" | "processing" | "bot_speaking";

export type TurnSpeaker = "candidate" | "bot";

export type TranscriptType =
  | "response"
  | "question"
  | "follow_up"
  | "silence_prompt"
  | "candidate"
  | "system";

export interface TranscriptEntry {
  speaker: TurnSpeaker;
  text: string;
  isFinal: boolean;
  timestamp: number;
  type?: TranscriptType;
}

export interface VoiceSessionStartRequest {
  candidate_name: string;
  job_role: string;
  experience_level: string;
  required_skills: string[];
}

export interface VoiceSessionStartResponse {
  session_id: string;
  token: string;
  state: string;
  ws_url: string;
}

export interface PlanPreviewQuestion {
  competency: string;
  source: string;
  question_text: string;
  difficulty: string;
  rubric_keypoints: string[];
  time_budget_sec: number;
}

export interface PlanPreviewResponse {
  draft_id: string;
  role_title: string;
  questions: PlanPreviewQuestion[];
  requested: number;
  usable_count: number;
  needs_confirmation: boolean;
}

export interface StartFromDraftRequest {
  draft_id: string;
  candidate_name: string;
}
