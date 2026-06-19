"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { startInterview, startFromConfig, ApiClientError } from "@/services/api";
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

const LEVELS: { value: ExperienceLevel; label: string; description: string }[] =
  [
    { value: "junior", label: "Junior", description: "0–2 years" },
    { value: "mid", label: "Mid-Level", description: "2–5 years" },
    { value: "senior", label: "Senior", description: "5–8 years" },
    { value: "staff", label: "Staff", description: "8+ years" },
  ];

function StartInterviewForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const configId = searchParams.get("configId");
  const [candidateName, setCandidateName] = useState("");
  const [jobRole, setJobRole] = useState(ROLES[2]);
  const [customRole, setCustomRole] = useState("");
  const [experienceLevel, setExperienceLevel] =
    useState<ExperienceLevel>("mid");
  const [skillsInput, setSkillsInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const effectiveRole = jobRole === "Other" ? customRole.trim() : jobRole;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    // Role is governed by the config when starting from a preset.
    if (!configId && !effectiveRole) {
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
      const res = configId
        ? await startFromConfig({
            interview_config_id: configId,
            candidate_name: candidateName.trim() || "Candidate",
            resume_details: skills.length ? { skills } : undefined,
          })
        : await startInterview({
            candidate_name: candidateName.trim() || "Candidate",
            job_role: effectiveRole,
            experience_level: experienceLevel,
            required_skills: skills,
          });
      sessionStorage.setItem(`interview_session_${res.session_id}`, JSON.stringify(res));
      router.push(`/interview/${res.session_id}`);
    } catch (err) {
      if (err instanceof ApiClientError) {
        setError(err.detail ?? err.message);
      } else {
        setError("Failed to start interview. Is the backend running?");
      }
      setLoading(false);
    }
  };

  return (
    <div className="max-w-xl mx-auto">
      <h1 className="text-2xl font-bold text-slate-900 mb-2">
        Start Interview
      </h1>
      <p className="text-slate-500 mb-8">
        Fill in your details and we&apos;ll select the right questions for you.
      </p>

      {configId && (
        <div className="bg-blue-50 border border-blue-200 text-blue-700 rounded-lg px-4 py-3 text-sm mb-6">
          Starting from a preset interview configuration. The role and questions are
          already set — just enter your name to begin.
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-6">
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">
            Your Name
          </label>
          <input
            type="text"
            value={candidateName}
            onChange={(e) => setCandidateName(e.target.value)}
            placeholder="e.g. Alex Chen"
            className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>

        {!configId && (
        <>
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">
            Job Role
          </label>
          <select
            value={jobRole}
            onChange={(e) => setJobRole(e.target.value)}
            className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          {jobRole === "Other" && (
            <input
              type="text"
              value={customRole}
              onChange={(e) => setCustomRole(e.target.value)}
              placeholder="Enter role name"
              className="mt-2 w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
              required
            />
          )}
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-2">
            Experience Level
          </label>
          <div className="grid grid-cols-2 gap-3">
            {LEVELS.map((level) => (
              <button
                key={level.value}
                type="button"
                onClick={() => setExperienceLevel(level.value)}
                className={`p-3 rounded-lg border-2 text-left transition-colors ${
                  experienceLevel === level.value
                    ? "border-blue-500 bg-blue-50 text-blue-700"
                    : "border-slate-200 bg-white text-slate-700 hover:border-slate-300"
                }`}
              >
                <div className="font-medium">{level.label}</div>
                <div className="text-xs opacity-70">{level.description}</div>
              </button>
            ))}
          </div>
        </div>
        </>
        )}

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">
            Key Skills{" "}
            <span className="text-slate-400 font-normal">(optional)</span>
          </label>
          <input
            type="text"
            value={skillsInput}
            onChange={(e) => setSkillsInput(e.target.value)}
            placeholder="e.g. React, Node.js, PostgreSQL"
            className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
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
          className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white font-semibold py-3 rounded-xl text-base transition-colors"
        >
          {loading ? "Starting…" : "Begin Interview →"}
        </button>
      </form>
    </div>
  );
}

export default function StartInterviewPage() {
  return (
    <Suspense
      fallback={
        <div className="max-w-xl mx-auto text-slate-500 text-sm">Loading…</div>
      }
    >
      <StartInterviewForm />
    </Suspense>
  );
}
