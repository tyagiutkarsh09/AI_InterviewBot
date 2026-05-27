const WS_BASE = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000";

export function buildInterviewWsUrl(sessionId: string): string {
  return `${WS_BASE}/ws/interview/${sessionId}`;
}
