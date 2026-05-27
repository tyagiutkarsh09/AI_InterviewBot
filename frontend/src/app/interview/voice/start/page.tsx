"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { startVoiceSession } from "@/services/voice-api";
import { ApiClientError } from "@/services/api";
import type { ExperienceLevel } from "@/types/interview";

const ROLES = [
  "Software Engineer (Backend)",
  "Software Engineer (Frontend)",
  "Software Engineer (Full Stack)",
  "Data Scientist",
  "DevOps / SRE",
  "Product Manager",
  "Other",
];

const LEVELS: { value: ExperienceLevel; label: string; description: string }[] = [
  { value: "junior", label: "Junior", description: "0–2 years" },
  { value: "mid", label: "Mid-Level", description: "2–5 years" },
  { value: "senior", label: "Senior", description: "5–8 years" },
  { value: "staff", label: "Staff", description: "8+ years" },
];

export default function VoiceStartPage() {
  const router = useRouter();
  const [candidateName, setCandidateName] = useState("");
  const [jobRole, setJobRole] = useState(ROLES[2]);
  const [customRole, setCustomRole] = useState("");
  const [experienceLevel, setExperienceLevel] = useState<ExperienceLevel>("mid");
  const [skillsInput, setSkillsInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const effectiveRole = jobRole === "Other" ? customRole.trim() : jobRole;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!effectiveRole) {
      setError("Please specify a job role.");
      return;
    }
    setLoading(true);
    setError(null);

    const skills = skillsInput
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);

    try {
      const res = await startVoiceSession({
        candidate_name: candidateName.trim() || "Candidate",
        job_role: effectiveRole,
        experience_level: experienceLevel,
        required_skills: skills,
      });
      // Store WS URL and token for the room
      sessionStorage.setItem(
        `voice_session_${res.session_id}`,
        JSON.stringify(res)
      );
      router.push(`/interview/voice/${res.session_id}`);
    } catch (err) {
      if (err instanceof ApiClientError) {
        setError(err.detail ?? err.message);
      } else {
        setError("Failed to start voice session. Is the backend running?");
      }
      setLoading(false);
    }
  };

  return (
    <div className="max-w-xl mx-auto">
      <div className="flex items-center gap-3 mb-6">
        <span className="text-3xl">🎙</span>
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Voice Interview</h1>
          <p className="text-slate-500 text-sm">Speak your answers — AI responds with voice</p>
        </div>
      </div>

      <div className="bg-violet-50 border border-violet-200 rounded-xl px-4 py-3 mb-6 text-sm text-violet-700">
        Your browser will ask for microphone permission when the interview starts.
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">Your Name</label>
          <input
            type="text"
            value={candidateName}
            onChange={(e) => setCandidateName(e.target.value)}
            placeholder="e.g. Alex Chen"
            className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-violet-500"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">Job Role</label>
          <select
            value={jobRole}
            onChange={(e) => setJobRole(e.target.value)}
            className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-violet-500 bg-white"
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
          {jobRole === "Other" && (
            <input
              type="text"
              value={customRole}
              onChange={(e) => setCustomRole(e.target.value)}
              placeholder="Enter role name"
              className="mt-2 w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-violet-500"
              required
            />
          )}
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-2">Experience Level</label>
          <div className="grid grid-cols-2 gap-3">
            {LEVELS.map((level) => (
              <button
                key={level.value}
                type="button"
                onClick={() => setExperienceLevel(level.value)}
                className={`p-3 rounded-lg border-2 text-left transition-colors ${
                  experienceLevel === level.value
                    ? "border-violet-500 bg-violet-50 text-violet-700"
                    : "border-slate-200 bg-white text-slate-700 hover:border-slate-300"
                }`}
              >
                <div className="font-medium">{level.label}</div>
                <div className="text-xs opacity-70">{level.description}</div>
              </button>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">
            Key Skills <span className="text-slate-400 font-normal">(optional)</span>
          </label>
          <input
            type="text"
            value={skillsInput}
            onChange={(e) => setSkillsInput(e.target.value)}
            placeholder="e.g. React, Node.js, PostgreSQL"
            className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-violet-500"
          />
          <p className="text-xs text-slate-400 mt-1">Comma-separated</p>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={loading}
          className="w-full bg-violet-600 hover:bg-violet-700 disabled:bg-violet-400 text-white font-semibold py-3 rounded-xl text-base transition-colors"
        >
          {loading ? "Starting…" : "Begin Voice Interview →"}
        </button>
      </form>
    </div>
  );
}
