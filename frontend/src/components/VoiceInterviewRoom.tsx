"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import type { VoiceCaptureState, TranscriptEntry } from "@/types/voice-interview";

interface Props {
  sessionId: string;
  wsUrl: string;
}

const STATE_LABELS: Record<VoiceCaptureState, string> = {
  idle: "Waiting for your response…",
  speaking: "Listening…",
  processing: "Processing…",
  bot_speaking: "AI is speaking",
};

const STATE_COLORS: Record<VoiceCaptureState, string> = {
  idle: "bg-slate-100 text-slate-600",
  speaking: "bg-green-100 text-green-700",
  processing: "bg-yellow-100 text-yellow-700",
  bot_speaking: "bg-violet-100 text-violet-700",
};

export default function VoiceInterviewRoom({ sessionId, wsUrl }: Props) {
  const router = useRouter();
  const [captureState, setCaptureState] = useState<VoiceCaptureState>("idle");
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [liveText, setLiveText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [started, setStarted] = useState(false);

  const captureRef = useRef<import("@/lib/voice-capture").VoiceCapture | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const rafRef = useRef<number>(0);
  const transcriptEndRef = useRef<HTMLDivElement>(null);

  // ---- Waveform animation ----
  const drawWaveform = useCallback(() => {
    const canvas = canvasRef.current;
    const analyser = analyserRef.current;
    if (!canvas || !analyser) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const bufferLength = analyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);
    analyser.getByteTimeDomainData(dataArray);

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const isActive = captureState === "speaking";
    ctx.strokeStyle = isActive ? "#7c3aed" : "#cbd5e1";
    ctx.lineWidth = 2;
    ctx.beginPath();

    const sliceWidth = canvas.width / bufferLength;
    let x = 0;

    for (let i = 0; i < bufferLength; i++) {
      const v = dataArray[i] / 128.0;
      const y = (v * canvas.height) / 2;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
      x += sliceWidth;
    }
    ctx.lineTo(canvas.width, canvas.height / 2);
    ctx.stroke();

    rafRef.current = requestAnimationFrame(drawWaveform);
  }, [captureState]);

  // ---- Start interview (mic permission + WS) ----
  const handleStart = useCallback(async () => {
    setError(null);
    try {
      const { VoiceCapture } = await import("@/lib/voice-capture");
      const vc = new VoiceCapture(wsUrl);

      vc.onStateChange = (state) => setCaptureState(state);

      vc.onTranscript = (text, isFinal) => {
        if (isFinal) {
          setLiveText("");
          setTranscript((prev) => {
            const withoutPartial = prev.filter((t) => t.isFinal || t.speaker !== "candidate");
            return [
              ...withoutPartial,
              { speaker: "candidate", text, isFinal: true, timestamp: Date.now() },
            ];
          });
        } else {
          setLiveText(text);
        }
      };

      vc.onControlMessage = (data) => {
        const event = data.event as string;
        if (event === "interview_complete") {
          const reportUrl = data.report_url as string;
          router.push(reportUrl);
          return;
        }
        if (event === "interviewer_prompt" || event === "turn") {
          const text = data.text as string | undefined;
          if (text) {
            setTranscript((prev) => [
              ...prev,
              { speaker: "bot", text, isFinal: true, timestamp: Date.now() },
            ]);
          }
        }
      };

      vc.onError = (err) => setError(err.message);

      await vc.start();
      captureRef.current = vc;

      // Wire analyser for waveform
      // We need to hook into the AudioContext after start
      // VoiceCapture exposes audioCtx — access via a small workaround
      const vcAny = vc as unknown as { audioCtx: AudioContext | null };
      if (vcAny.audioCtx) {
        const analyser = vcAny.audioCtx.createAnalyser();
        analyser.fftSize = 256;
        analyserRef.current = analyser;
        // Connect the media stream source to analyser for visualization
        const stream = (vc as unknown as { mediaStream: MediaStream | null }).mediaStream;
        if (stream && vcAny.audioCtx) {
          const src = vcAny.audioCtx.createMediaStreamSource(stream);
          src.connect(analyser);
        }
      }

      setStarted(true);
      rafRef.current = requestAnimationFrame(drawWaveform);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start capture");
    }
  }, [wsUrl, drawWaveform]);

  // ---- Stop / cleanup ----
  const handleStop = useCallback(() => {
    captureRef.current?.stop();
    captureRef.current = null;
    cancelAnimationFrame(rafRef.current);
    setStarted(false);
    setCaptureState("idle");
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      captureRef.current?.stop();
      cancelAnimationFrame(rafRef.current);
    };
  }, []);

  // Auto-scroll transcript
  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcript, liveText]);

  // Restart waveform animation when state changes
  useEffect(() => {
    if (!started) return;
    cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(drawWaveform);
  }, [captureState, started, drawWaveform]);

  return (
    <div className="max-w-2xl mx-auto space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xl">🎙</span>
          <span className="font-semibold text-slate-800">Voice Interview</span>
          <span className="text-xs text-slate-400 font-mono">{sessionId.slice(0, 8)}…</span>
        </div>
        {started && (
          <button
            onClick={handleStop}
            className="text-sm text-red-600 hover:text-red-700 font-medium"
          >
            End Interview
          </button>
        )}
      </div>

      {/* Turn indicator */}
      <div
        className={`rounded-xl px-4 py-3 text-sm font-medium flex items-center gap-2 transition-colors ${STATE_COLORS[captureState]}`}
      >
        <span>{captureState === "speaking" ? "🔴" : captureState === "bot_speaking" ? "🔊" : captureState === "processing" ? "⏳" : "⏸"}</span>
        {STATE_LABELS[captureState]}
      </div>

      {/* Waveform */}
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-4">
        <canvas
          ref={canvasRef}
          width={560}
          height={80}
          className="w-full h-20 rounded-lg bg-slate-50"
        />
      </div>

      {/* Live transcript panel */}
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-100 text-xs font-medium text-slate-500 uppercase tracking-wide">
          Transcript
        </div>
        <div className="p-4 space-y-3 max-h-72 overflow-y-auto">
          {transcript.length === 0 && !liveText && (
            <p className="text-sm text-slate-400 text-center py-4">
              {started ? "Conversation will appear here…" : "Start the interview to begin."}
            </p>
          )}
          {transcript.map((entry, i) => (
            <div
              key={i}
              className={`flex gap-2 ${entry.speaker === "bot" ? "" : "flex-row-reverse"}`}
            >
              <div
                className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 ${
                  entry.speaker === "bot"
                    ? "bg-violet-600 text-white"
                    : "bg-green-600 text-white"
                }`}
              >
                {entry.speaker === "bot" ? "AI" : "You"}
              </div>
              <div
                className={`rounded-xl px-3 py-2 text-sm max-w-xs ${
                  entry.speaker === "bot"
                    ? "bg-violet-50 text-violet-900"
                    : "bg-green-50 text-green-900"
                }`}
              >
                {entry.text}
              </div>
            </div>
          ))}
          {liveText && (
            <div className="flex gap-2 flex-row-reverse">
              <div className="w-7 h-7 rounded-full bg-green-600 flex items-center justify-center text-xs font-bold text-white flex-shrink-0">
                You
              </div>
              <div className="rounded-xl px-3 py-2 text-sm max-w-xs bg-green-50 text-green-700 italic opacity-80">
                {liveText}
              </div>
            </div>
          )}
          <div ref={transcriptEndRef} />
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
          {error}
        </div>
      )}

      {/* Start button */}
      {!started && (
        <button
          onClick={handleStart}
          className="w-full bg-violet-600 hover:bg-violet-700 text-white font-semibold py-4 rounded-xl text-base transition-colors"
        >
          🎙 Start Voice Interview
        </button>
      )}

      <p className="text-xs text-slate-400 text-center">
        Speak naturally. The AI will ask questions and respond with voice.
        {started && " Interrupt the AI at any time by speaking."}
      </p>
    </div>
  );
}
