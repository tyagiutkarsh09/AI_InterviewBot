"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import VoiceInterviewRoom from "@/components/VoiceInterviewRoom";
import type { VoiceSessionStartResponse } from "@/types/voice-interview";

export default function VoiceInterviewRoomPage() {
  const params = useParams();
  const router = useRouter();
  const sessionId = params.sessionId as string;

  const [sessionData, setSessionData] =
    useState<VoiceSessionStartResponse | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    const raw = sessionStorage.getItem(`voice_session_${sessionId}`);
    if (!raw) {
      setMissing(true);
      return;
    }
    try {
      setSessionData(JSON.parse(raw) as VoiceSessionStartResponse);
    } catch {
      setMissing(true);
    }
  }, [sessionId]);

  if (missing) {
    return (
      <div className="text-center py-20">
        <h2 className="text-xl font-semibold text-slate-700 mb-2">
          Session not found
        </h2>
        <p className="text-slate-500 mb-6">
          This voice session has expired or does not exist.
        </p>
        <button
          onClick={() => router.push("/interview/voice/start")}
          className="bg-violet-600 text-white px-6 py-3 rounded-lg hover:bg-violet-700"
        >
          Start a new voice interview
        </button>
      </div>
    );
  }

  if (!sessionData) {
    return (
      <div className="text-center py-20 text-slate-500">Loading session…</div>
    );
  }

  return (
    <VoiceInterviewRoom sessionId={sessionId} wsUrl={sessionData.ws_url} />
  );
}
