export type VoiceCaptureState = "idle" | "speaking" | "processing" | "bot_speaking";

export type TurnSpeaker = "candidate" | "bot";

export interface TranscriptEntry {
  speaker: TurnSpeaker;
  text: string;
  isFinal: boolean;
  timestamp: number;
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
