"use client";

import { useState } from "react";

interface TranscriptTurn {
  speaker: string;
  text: string;
  timestamp?: string;
  timestamp_ms?: number;
}

interface TranscriptTimelineProps {
  transcript: TranscriptTurn[];
}

export default function TranscriptTimeline({ transcript }: TranscriptTimelineProps) {
  const [copied, setCopied] = useState(false);

  const copyTranscript = () => {
    const text = transcript
      .map((t) => {
        const label = t.speaker === "bot" ? "Interviewer" : "Candidate";
        return `[${label}]: ${t.text}`;
      })
      .join("\n\n");
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const downloadJson = () => {
    const blob = new Blob([JSON.stringify(transcript, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "interview-transcript.json";
    a.click();
    URL.revokeObjectURL(url);
  };

  if (transcript.length === 0) return null;

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-semibold text-slate-900">Transcript</h3>
        <div className="flex gap-2">
          <button
            onClick={copyTranscript}
            className="text-xs px-3 py-1.5 rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50"
          >
            {copied ? "Copied!" : "Copy"}
          </button>
          <button
            onClick={downloadJson}
            className="text-xs px-3 py-1.5 rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50"
          >
            Download JSON
          </button>
        </div>
      </div>
      <div className="space-y-3 max-h-96 overflow-y-auto pr-2">
        {transcript.map((turn, i) => {
          const isBot = turn.speaker === "bot";
          return (
            <div
              key={i}
              className={`flex gap-3 ${isBot ? "" : "flex-row-reverse"}`}
            >
              <div
                className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${
                  isBot
                    ? "bg-blue-100 text-blue-700"
                    : "bg-green-100 text-green-700"
                }`}
              >
                {isBot ? "AI" : "C"}
              </div>
              <div
                className={`max-w-[75%] rounded-lg px-3 py-2 text-sm ${
                  isBot
                    ? "bg-slate-50 text-slate-700"
                    : "bg-blue-50 text-slate-700"
                }`}
              >
                {turn.text}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
