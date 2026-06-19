"use client";

import { useState } from "react";
import Link from "next/link";
import AdminGuard from "@/components/AdminGuard";
import { createConfig, AdminApiError } from "@/services/admin-api";
import type { ConfigResponse } from "@/types/admin";
import type { ExperienceLevel } from "@/types/interview";

const LEVELS: { value: ExperienceLevel; label: string }[] = [
  { value: "junior", label: "Junior" },
  { value: "mid", label: "Mid-Level" },
  { value: "senior", label: "Senior" },
  { value: "staff", label: "Staff" },
];

export default function NewConfigPage() {
  const [title, setTitle] = useState("");
  const [role, setRole] = useState("");
  const [experienceLevel, setExperienceLevel] = useState<ExperienceLevel>("mid");
  const [jobDescription, setJobDescription] = useState("");
  const [totalQuestions, setTotalQuestions] = useState(6);
  const [coreRatio, setCoreRatio] = useState(0.8);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<ConfigResponse | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!jobDescription.trim()) {
      setError("A job description is required.");
      return;
    }
    setLoading(true);
    setError(null);

    try {
      const res = await createConfig({
        title: title.trim(),
        role: role.trim(),
        experience_level: experienceLevel,
        job_description: jobDescription,
        total_questions: totalQuestions,
        core_question_ratio: coreRatio,
      });
      setCreated(res);
    } catch (err) {
      if (err instanceof AdminApiError) {
        setError(err.detail ?? err.message);
      } else {
        setError("Failed to create config. Is the backend running?");
      }
    } finally {
      setLoading(false);
    }
  };

  if (created) {
    return (
      <AdminGuard>
        <div className="max-w-xl mx-auto space-y-6">
          <div className="bg-green-50 border border-green-200 rounded-lg p-4 text-sm text-green-800">
            Config <span className="font-mono">{created.id}</span> created.
          </div>
          <div className="flex gap-3">
            <Link
              href={`/interview/start?configId=${created.id}`}
              className="bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold px-4 py-2 rounded-lg transition-colors"
            >
              Start a candidate interview
            </Link>
            <Link
              href="/admin/configs"
              className="border border-slate-200 text-slate-600 hover:bg-slate-50 text-sm font-medium px-4 py-2 rounded-lg transition-colors"
            >
              Back to configs
            </Link>
          </div>
        </div>
      </AdminGuard>
    );
  }

  return (
    <AdminGuard>
      <div className="max-w-xl mx-auto">
        <h1 className="text-2xl font-bold text-slate-900 mb-2">New Interview Config</h1>
        <p className="text-slate-500 mb-8">
          Anchored to a job description — the question plan is frozen at creation.
        </p>

        <form onSubmit={handleSubmit} className="space-y-6">
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Title</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Backend Hiring — Q3"
              className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
              required
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Role</label>
            <input
              type="text"
              value={role}
              onChange={(e) => setRole(e.target.value)}
              placeholder="e.g. Backend Engineer"
              className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
              required
            />
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
                      ? "border-blue-500 bg-blue-50 text-blue-700"
                      : "border-slate-200 bg-white text-slate-700 hover:border-slate-300"
                  }`}
                >
                  <div className="font-medium">{level.label}</div>
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Job Description <span className="text-red-500">*</span>
            </label>
            <textarea
              value={jobDescription}
              onChange={(e) => setJobDescription(e.target.value)}
              placeholder="Paste the full job description here…"
              rows={8}
              className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
              required
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Total Questions</label>
              <input
                type="number"
                min={4}
                max={20}
                value={totalQuestions}
                onChange={(e) => setTotalQuestions(Number(e.target.value))}
                className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
              />
              <p className="text-xs text-slate-400 mt-1">Includes 2 reserved slots</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Core Ratio</label>
              <input
                type="number"
                min={0.05}
                max={0.95}
                step={0.05}
                value={coreRatio}
                onChange={(e) => setCoreRatio(Number(e.target.value))}
                className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
              />
              <p className="text-xs text-slate-400 mt-1">Core vs JD split</p>
            </div>
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
            {loading ? "Creating…" : "Create Config"}
          </button>
        </form>
      </div>
    </AdminGuard>
  );
}
