import type {
  PlanPreviewResponse,
  StartFromDraftRequest,
  VoiceSessionStartRequest,
  VoiceSessionStartResponse,
} from "@/types/voice-interview";
import { ApiClientError } from "@/services/api";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function logVoiceApiDebug(message: string, context: Record<string, unknown> = {}) {
  console.debug(`[voice-api] ${message}`, {
    at: new Date().toISOString(),
    ...context,
  });
}

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

const ADMIN_KEY = process.env.NEXT_PUBLIC_ADMIN_API_KEY ?? "change-me-admin-key";

export async function previewPlan(
  form: FormData
): Promise<PlanPreviewResponse> {
  const startedAt = performance.now();
  logVoiceApiDebug("preview-plan-request", {
    url: `${API_BASE}/api/v1/voice/plan/preview`,
    formKeys: Array.from(form.keys()),
  });
  const res = await fetch(`${API_BASE}/api/v1/voice/plan/preview`, {
    method: "POST",
    headers: { "X-Admin-Key": ADMIN_KEY },
    body: form,
  });
  logVoiceApiDebug("preview-plan-response", {
    status: res.status,
    ok: res.ok,
    elapsedMs: Math.round(performance.now() - startedAt),
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
  const body = (await res.json()) as PlanPreviewResponse;
  logVoiceApiDebug("preview-plan-payload", {
    draftId: body.draft_id,
    roleTitle: body.role_title,
    questionCount: body.questions.length,
    usableCount: body.usable_count,
    needsConfirmation: body.needs_confirmation,
  });
  return body;
}

export async function startFromDraft(
  body: StartFromDraftRequest
): Promise<VoiceSessionStartResponse> {
  return request<VoiceSessionStartResponse>(
    "/api/v1/voice/session/start-from-draft",
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-Key": ADMIN_KEY,
      },
      body: JSON.stringify(body),
    }
  );
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
