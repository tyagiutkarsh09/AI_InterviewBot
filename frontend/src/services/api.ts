import type {
  StartInterviewRequest,
  StartInterviewResponse,
  StartFromConfigRequest,
  SubmitAnswerRequest,
  SubmitAnswerResponse,
  ReportResponse,
  ApiError,
} from "@/types/interview";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

class ApiClientError extends Error {
  constructor(
    message: string,
    public status: number,
    public detail?: string
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });

  if (!res.ok) {
    let detail: string | undefined;
    try {
      const body = (await res.json()) as ApiError;
      if (typeof body.detail === "string") {
        detail = body.detail;
      } else if (Array.isArray(body.detail)) {
        detail = body.detail.map((d) => d.msg).join(", ");
      } else {
        detail = body.error;
      }
    } catch {
      detail = res.statusText;
    }
    throw new ApiClientError(`HTTP ${res.status}`, res.status, detail);
  }

  return res.json() as Promise<T>;
}

export async function startInterview(
  body: StartInterviewRequest
): Promise<StartInterviewResponse> {
  return request<StartInterviewResponse>("/api/v1/interview/start", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function startFromConfig(
  body: StartFromConfigRequest
): Promise<StartInterviewResponse> {
  return request<StartInterviewResponse>("/api/v1/interview/start-from-config", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function submitAnswer(
  body: SubmitAnswerRequest
): Promise<SubmitAnswerResponse> {
  return request<SubmitAnswerResponse>("/api/v1/interview/answer", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function getReport(sessionId: string): Promise<ReportResponse> {
  return request<ReportResponse>(`/api/v1/interview/report/${sessionId}`);
}

export { ApiClientError };
