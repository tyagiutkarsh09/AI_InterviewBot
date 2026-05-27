import type {
  VoiceSessionStartRequest,
  VoiceSessionStartResponse,
} from "@/types/voice-interview";
import { ApiClientError } from "@/services/api";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!res.ok) {
    let detail: string | undefined;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : body.error;
    } catch {
      detail = res.statusText;
    }
    throw new ApiClientError(`HTTP ${res.status}`, res.status, detail);
  }
  return res.json() as Promise<T>;
}

export async function startVoiceSession(
  body: VoiceSessionStartRequest
): Promise<VoiceSessionStartResponse> {
  return request<VoiceSessionStartResponse>("/api/v1/voice/session/start", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function getVoiceSessionState(sessionId: string) {
  return request<{
    session_id: string;
    state: string;
    current_question_idx: number;
    turn_count: number;
    connection_state: string;
  }>(`/api/v1/voice/session/${sessionId}`);
}
