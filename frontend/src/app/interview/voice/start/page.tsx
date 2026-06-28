"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { previewPlan, startFromDraft } from "@/services/voice-api";
import { ApiClientError } from "@/services/api";
import type { ExperienceLevel } from "@/types/interview";
import type {
  PlanPreviewQuestion,
  PlanPreviewResponse,
} from "@/types/voice-interview";

const LEVELS: {
  value: ExperienceLevel;
  label: string;
  description: string;
}[] = [
  { value: "junior", label: "Junior", description: "0-2 years" },
  { value: "mid", label: "Mid-Level", description: "2-5 years" },
  { value: "senior", label: "Senior", description: "5-8 years" },
  { value: "staff", label: "Staff", description: "8+ years" },
];

const DIFFICULTY_COLORS: Record<string, string> = {
  easy: "bg-emerald-100 text-emerald-700",
  medium: "bg-amber-100 text-amber-700",
  hard: "bg-rose-100 text-rose-700",
};

type PagePhase = "form" | "generating" | "preview" | "starting";

function logPlanDebug(message: string, context: Record<string, unknown> = {}) {
  console.debug(`[voice-plan] ${message}`, {
    at: new Date().toISOString(),
    ...context,
  });
}

export default function VoiceStartPage() {
  const router = useRouter();

  const [candidateName, setCandidateName] = useState("");
  const [jobRole, setJobRole] = useState("");
  const [experienceLevel, setExperienceLevel] = useState<ExperienceLevel>("mid");
  const [jdFile, setJdFile] = useState<File | null>(null);
  const [resumeFile, setResumeFile] = useState<File | null>(null);
  const [numQuestions, setNumQuestions] = useState(5);

  const [phase, setPhase] = useState<PagePhase>("form");
  const [preview, setPreview] = useState<PlanPreviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const buildFormData = (): FormData => {
    if (!jdFile) {
      throw new Error("Job description file is required.");
    }

    const form = new FormData();
    form.append("jd", jdFile);
    if (resumeFile) {
      form.append("resume", resumeFile);
    }
    form.append("job_role", jobRole.trim() || "Role from JD");
    form.append("experience_level", experienceLevel);
    form.append("num_questions", String(numQuestions));
    return form;
  };

  const handleGenerate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!jdFile) {
      setError("Upload a job description to continue.");
      return;
    }

    setPhase("generating");
    setError(null);
    const startedAt = performance.now();
    logPlanDebug("generate-clicked", {
      jobRole: jobRole.trim() || "Role from JD",
      experienceLevel,
      numQuestions,
      jdFileName: jdFile.name,
      jdFileSize: jdFile.size,
      resumeFileName: resumeFile?.name ?? null,
      resumeFileSize: resumeFile?.size ?? null,
    });

    try {
      const result = await previewPlan(buildFormData());
      logPlanDebug("generate-succeeded", {
        elapsedMs: Math.round(performance.now() - startedAt),
        draftId: result.draft_id,
        questionCount: result.questions.length,
        usableCount: result.usable_count,
        needsConfirmation: result.needs_confirmation,
      });
      setPreview(result);
      setPhase("preview");
    } catch (err) {
      logPlanDebug("generate-failed", {
        elapsedMs: Math.round(performance.now() - startedAt),
        error:
          err instanceof Error
            ? { name: err.name, message: err.message }
            : { value: String(err) },
      });
      if (err instanceof ApiClientError) {
        setError(err.detail ?? err.message);
      } else {
        setError("Failed to generate interview plan. Is the backend running?");
      }
      setPhase("form");
    }
  };

  const handleRegenerate = async () => {
    setPhase("generating");
    setError(null);
    const startedAt = performance.now();
    logPlanDebug("regenerate-clicked", {
      jobRole: jobRole.trim() || "Role from JD",
      experienceLevel,
      numQuestions,
      jdFileName: jdFile?.name ?? null,
      resumeFileName: resumeFile?.name ?? null,
      previousDraftId: preview?.draft_id ?? null,
    });

    try {
      const result = await previewPlan(buildFormData());
      logPlanDebug("regenerate-succeeded", {
        elapsedMs: Math.round(performance.now() - startedAt),
        draftId: result.draft_id,
        questionCount: result.questions.length,
        usableCount: result.usable_count,
        needsConfirmation: result.needs_confirmation,
      });
      setPreview(result);
      setPhase("preview");
    } catch (err) {
      logPlanDebug("regenerate-failed", {
        elapsedMs: Math.round(performance.now() - startedAt),
        error:
          err instanceof Error
            ? { name: err.name, message: err.message }
            : { value: String(err) },
      });
      if (err instanceof ApiClientError) {
        setError(err.detail ?? err.message);
      } else {
        setError("Failed to regenerate plan.");
      }
      setPhase("preview");
    }
  };

  const handleStart = async () => {
    if (!preview || preview.questions.length === 0) {
      return;
    }

    setPhase("starting");
    setError(null);

    try {
      const res = await startFromDraft({
        draft_id: preview.draft_id,
        candidate_name: candidateName.trim() || "Candidate",
      });
      sessionStorage.setItem(`voice_session_${res.session_id}`, JSON.stringify(res));
      router.push(`/interview/voice/${res.session_id}`);
    } catch (err) {
      if (err instanceof ApiClientError) {
        setError(err.detail ?? err.message);
      } else {
        setError("Failed to start voice session.");
      }
      setPhase("preview");
    }
  };

  const handleBackToForm = () => {
    setPhase("form");
    setPreview(null);
    setError(null);
  };

  if (phase === "form" || phase === "generating") {
    return (
      <div className="max-w-xl mx-auto">
        <div className="flex items-center gap-3 mb-6">
          <span className="text-3xl">🎙</span>
          <div>
            <h1 className="text-2xl font-bold text-slate-900">Voice Interview</h1>
            <p className="text-slate-500 text-sm">
              Upload a job description to generate a personalized interview
            </p>
          </div>
        </div>

        <div className="bg-violet-50 border border-violet-200 rounded-xl px-4 py-3 mb-6 text-sm text-violet-700">
          Your browser will ask for microphone permission when the interview starts.
        </div>

        <form onSubmit={handleGenerate} className="space-y-6">
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Candidate Name
            </label>
            <input
              type="text"
              value={candidateName}
              onChange={(event) => setCandidateName(event.target.value)}
              placeholder="e.g. Alex Chen"
              className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-violet-500"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Job Role
            </label>
            <input
              type="text"
              value={jobRole}
              onChange={(event) => setJobRole(event.target.value)}
              placeholder="e.g. Mechanical Design Engineer (derived from JD if left blank)"
              className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-violet-500"
            />
            <p className="text-xs text-slate-400 mt-1">
              Optional hint. The planner derives the role from the JD automatically.
            </p>
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
              Job Description <span className="text-rose-500">*</span>
            </label>
            <input
              type="file"
              accept=".pdf,.docx,.md,.txt"
              onChange={(event) => setJdFile(event.target.files?.[0] ?? null)}
              className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-violet-500 file:mr-4 file:rounded-md file:border-0 file:bg-violet-50 file:px-3 file:py-1.5 file:text-violet-700"
            />
            <p className="text-xs text-slate-400 mt-1">
              PDF, DOCX, MD, or TXT. The JD drives about 80% of interview questions.
            </p>
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Resume <span className="text-slate-400 font-normal">(optional)</span>
            </label>
            <input
              type="file"
              accept=".pdf,.docx"
              onChange={(event) => setResumeFile(event.target.files?.[0] ?? null)}
              className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-violet-500 file:mr-4 file:rounded-md file:border-0 file:bg-violet-50 file:px-3 file:py-1.5 file:text-violet-700"
            />
            <p className="text-xs text-slate-400 mt-1">
              Personalizes about 20% of questions to the candidate&apos;s experience.
            </p>
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Technical questions: {numQuestions}
            </label>
            <input
              type="range"
              min={5}
              max={8}
              step={1}
              value={numQuestions}
              onChange={(event) => setNumQuestions(Number(event.target.value))}
              className="w-full accent-violet-600"
            />
            <div className="flex justify-between text-xs text-slate-400 mt-1">
              <span>5 (quick)</span>
              <span>8 (thorough)</span>
            </div>
            <p className="text-xs text-slate-400 mt-1">
              Plus a behavioral and project deep-dive question on top.
            </p>
          </div>

          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={phase === "generating" || !jdFile}
            className="w-full bg-violet-600 hover:bg-violet-700 disabled:bg-violet-400 text-white font-semibold py-3 rounded-xl text-base transition-colors"
          >
            {phase === "generating" ? "Generating plan..." : "Generate Interview Plan"}
          </button>
        </form>
      </div>
    );
  }

  const hasQuestions = Boolean(preview && preview.questions.length > 0);

  return (
    <div className="max-w-2xl mx-auto">
      <div className="flex items-center gap-3 mb-6">
        <span className="text-3xl">🎙</span>
        <div>
          <h1 className="text-2xl font-bold text-slate-900">
            {preview?.role_title ?? "Interview Plan"}
          </h1>
          <p className="text-slate-500 text-sm">
            Review the generated plan, then start the interview
          </p>
        </div>
      </div>

      {preview?.needs_confirmation && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 mb-4 text-sm text-amber-700">
          This JD supports only <strong>{preview.usable_count}</strong> grounded
          questions (you requested {preview.requested}). The interview will proceed
          with {preview.usable_count} technical questions.
        </div>
      )}

      <div className="space-y-3 mb-6">
        {hasQuestions ? (
          preview?.questions.map((question: PlanPreviewQuestion, index: number) => (
            <div
              key={`${question.competency}-${index}`}
              className="border border-slate-200 rounded-lg px-4 py-3 bg-white"
            >
              <div className="flex items-center gap-2 mb-1">
                <span className="text-xs font-medium text-slate-400">
                  Q{index + 1}
                </span>
                <span
                  className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                    DIFFICULTY_COLORS[question.difficulty] ?? "bg-slate-100 text-slate-600"
                  }`}
                >
                  {question.difficulty}
                </span>
                <span className="text-xs text-slate-400">{question.competency}</span>
                <span className="text-xs text-slate-300">({question.source})</span>
              </div>
              <p className="text-sm text-slate-800">{question.question_text}</p>
            </div>
          ))
        ) : (
          <div className="border border-dashed border-slate-200 rounded-lg px-4 py-6 text-sm text-slate-500">
            No grounded technical questions were generated. Go back and try a different
            job description.
          </div>
        )}

        <div className="border border-dashed border-slate-200 rounded-lg px-4 py-3 text-sm text-slate-400">
          + 1 behavioral question + 1 project deep-dive (added automatically)
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm mb-4">
          {error}
        </div>
      )}

      <div className="flex gap-3">
        <button
          type="button"
          onClick={handleBackToForm}
          disabled={phase === "starting"}
          className="px-5 py-3 rounded-xl border border-slate-300 text-slate-700 hover:bg-slate-50 transition-colors text-sm font-medium disabled:opacity-50"
        >
          Back
        </button>
        <button
          type="button"
          onClick={handleRegenerate}
          disabled={phase === "starting"}
          className="px-5 py-3 rounded-xl border border-violet-300 text-violet-700 hover:bg-violet-50 transition-colors text-sm font-medium disabled:opacity-50"
        >
          Regenerate
        </button>
        <button
          type="button"
          onClick={handleStart}
          disabled={phase === "starting" || !hasQuestions}
          className="flex-1 bg-violet-600 hover:bg-violet-700 disabled:bg-violet-400 text-white font-semibold py-3 rounded-xl text-base transition-colors"
        >
          {phase === "starting"
            ? "Starting interview..."
            : `Start Interview (${(preview?.usable_count ?? 0) + 2} questions)`}
        </button>
      </div>
    </div>
  );
}
