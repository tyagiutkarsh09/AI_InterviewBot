# JD-Hyper-Personalized Questions — Frontend Implementation Plan (Phase 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the voice start page to the new two-step backend flow — `POST /voice/plan/preview` (generate + cache a JD-driven question plan) then `POST /voice/session/start-from-draft` (start the session) — replacing the deleted `startVoiceSessionFromJd` / `/voice/session/start-from-jd` call that currently 404s on this branch.

**Architecture:** The page becomes a two-phase UI: (1) a form with mandatory JD upload, optional resume, free-text role, experience level selector (all 4 levels enabled), and a 5–8 question slider, which calls the preview endpoint; (2) a read-only plan preview showing the generated questions (competency, difficulty, question text) with a Regenerate button and a shortfall warning when the JD couldn't fill the requested count — then a "Start Interview" button that calls `start-from-draft` and redirects to the interview room. No new pages or routes; the existing `page.tsx` is rewritten in place. No inline question editing (desyncs question text from rubric key points → silent mis-scoring).

**Tech Stack:** Next.js 14 (App Router, client component), TypeScript, Tailwind CSS, existing `voice-api.ts` service layer.

**Testing:** This frontend has no test runner (no jest/vitest in `package.json`). Verification is via `tsc --noEmit` (type-check) + manual testing in the browser with the dev server. Each task includes a type-check step; Task 4 is the manual integration test.

---

## Scope

**IN:** New types for the preview/start-from-draft API contract; rewrite `voice-api.ts` to call the two new endpoints (delete the old `startVoiceSessionFromJd`); rewrite `page.tsx` for the two-step form → preview → start flow; enable the N slider (5–8); enable all experience levels; make JD mandatory + resume optional; plan preview with Regenerate; shortfall warning; error handling.

**OUT:** Inline question editing (deferred — see spec §H); any change to the text/admin-config flow; changes to the interview room page (`[sessionId]/page.tsx`) or `VoiceInterviewRoom` component; adding a frontend test runner.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `frontend/src/types/voice-interview.ts` | Modify | Add `PlanPreviewQuestion` + `PlanPreviewResponse` types |
| `frontend/src/services/voice-api.ts` | Modify | Delete `startVoiceSessionFromJd`; add `previewPlan` + `startFromDraft` |
| `frontend/src/app/interview/voice/start/page.tsx` | Rewrite | Two-step form → preview → start flow |

---

## Task 1: Add preview/start-from-draft types  `[inline]`

**Files:**
- Modify: `frontend/src/types/voice-interview.ts:15-27`

- [ ] **Step 1: Add the new types**

Append these types to `frontend/src/types/voice-interview.ts`, after `VoiceSessionStartResponse`:

```typescript
export interface PlanPreviewQuestion {
  competency: string;
  source: string;           // "jd" | "resume"
  question_text: string;
  difficulty: string;        // "easy" | "medium" | "hard"
  rubric_keypoints: string[];
  time_budget_sec: number;
}

export interface PlanPreviewResponse {
  draft_id: string;
  role_title: string;
  questions: PlanPreviewQuestion[];
  requested: number;
  usable_count: number;
  needs_confirmation: boolean;
}

export interface StartFromDraftRequest {
  draft_id: string;
  candidate_name: string;
}
```

These mirror the backend's `PlanPreviewResponse` (voice_api.py:137-143) and `StartFromDraftRequest` (voice_api.py:146-148) exactly. `PlanPreviewQuestion` matches the `PlannedQuestion.model_dump()` dicts in the `questions` array.

- [ ] **Step 2: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors (new types are additive, nothing consumes them yet).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/voice-interview.ts
git commit -m "feat(types): add PlanPreviewResponse + StartFromDraftRequest voice types"
```

---

## Task 2: Rewrite `voice-api.ts` for the two-step flow  `[inline]`

**Files:**
- Modify: `frontend/src/services/voice-api.ts:39-60`

- [ ] **Step 1: Replace `startVoiceSessionFromJd` with `previewPlan` and `startFromDraft`**

Replace the `startVoiceSessionFromJd` function (lines 39-60) with two new functions. The full file becomes:

```typescript
import type {
  VoiceSessionStartRequest,
  VoiceSessionStartResponse,
  PlanPreviewResponse,
  StartFromDraftRequest,
} from "@/types/voice-interview";
import { ApiClientError } from "@/services/api";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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
  const res = await fetch(`${API_BASE}/api/v1/voice/plan/preview`, {
    method: "POST",
    headers: { "X-Admin-Key": ADMIN_KEY },
    body: form,
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
  return res.json() as Promise<PlanPreviewResponse>;
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
```

Key points for the implementer:
- `previewPlan` uses raw `fetch` (not the `request` helper) because the body is `FormData` — the browser must set the `Content-Type: multipart/form-data` boundary automatically. Setting `Content-Type: application/json` (which the `request` helper does) would break multipart uploads. This is the same pattern the old `startVoiceSessionFromJd` used.
- `startFromDraft` uses the `request` helper because its body is JSON. It adds the `X-Admin-Key` header.
- `startVoiceSession` (the old JSON-body start, used by some other flow) is kept unchanged.
- The deleted `startVoiceSessionFromJd` is the ONLY function removed.

- [ ] **Step 2: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: one error — `page.tsx` still imports `startVoiceSessionFromJd` which no longer exists. This is expected; Task 3 fixes it.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/services/voice-api.ts
git commit -m "feat(voice-api): replace startVoiceSessionFromJd with previewPlan + startFromDraft"
```

---

## Task 3: Rewrite the voice start page for two-step flow  `[inline]`

The core task. Rewrites `page.tsx` from a single-step form (fill → start) to a two-phase UI (fill → preview → start). This is a full rewrite of the file.

**Files:**
- Rewrite: `frontend/src/app/interview/voice/start/page.tsx`

- [ ] **Step 1: Write the new page**

Replace the entire contents of `frontend/src/app/interview/voice/start/page.tsx` with:

```tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { previewPlan, startFromDraft } from "@/services/voice-api";
import { ApiClientError } from "@/services/api";
import type { ExperienceLevel } from "@/types/interview";
import type { PlanPreviewResponse, PlanPreviewQuestion } from "@/types/voice-interview";

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

export default function VoiceStartPage() {
  const router = useRouter();

  // Form state
  const [candidateName, setCandidateName] = useState("");
  const [jobRole, setJobRole] = useState("");
  const [experienceLevel, setExperienceLevel] = useState<ExperienceLevel>("mid");
  const [jdFile, setJdFile] = useState<File | null>(null);
  const [resumeFile, setResumeFile] = useState<File | null>(null);
  const [numQuestions, setNumQuestions] = useState(5);

  // Flow state
  const [phase, setPhase] = useState<PagePhase>("form");
  const [preview, setPreview] = useState<PlanPreviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const buildFormData = (): FormData => {
    const form = new FormData();
    form.append("jd", jdFile!);
    if (resumeFile) form.append("resume", resumeFile);
    form.append("job_role", jobRole.trim() || "Role from JD");
    form.append("experience_level", experienceLevel);
    form.append("num_questions", String(numQuestions));
    return form;
  };

  const handleGenerate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!jdFile) {
      setError("Upload a job description to continue.");
      return;
    }
    setPhase("generating");
    setError(null);

    try {
      const result = await previewPlan(buildFormData());
      setPreview(result);
      setPhase("preview");
    } catch (err) {
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
    try {
      const result = await previewPlan(buildFormData());
      setPreview(result);
      setPhase("preview");
    } catch (err) {
      if (err instanceof ApiClientError) {
        setError(err.detail ?? err.message);
      } else {
        setError("Failed to regenerate plan.");
      }
      setPhase("preview");
    }
  };

  const handleStart = async () => {
    if (!preview) return;
    setPhase("starting");
    setError(null);

    try {
      const res = await startFromDraft({
        draft_id: preview.draft_id,
        candidate_name: candidateName.trim() || "Candidate",
      });
      sessionStorage.setItem(
        `voice_session_${res.session_id}`,
        JSON.stringify(res)
      );
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

  // ── Form phase ──────────────────────────────────────────────
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
          {/* Candidate name */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Candidate Name
            </label>
            <input
              type="text"
              value={candidateName}
              onChange={(e) => setCandidateName(e.target.value)}
              placeholder="e.g. Alex Chen"
              className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-violet-500"
            />
          </div>

          {/* Job role (free text hint) */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Job Role
            </label>
            <input
              type="text"
              value={jobRole}
              onChange={(e) => setJobRole(e.target.value)}
              placeholder="e.g. Mechanical Design Engineer (derived from JD if left blank)"
              className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-violet-500"
            />
            <p className="text-xs text-slate-400 mt-1">
              Optional hint. The planner derives the role from the JD automatically.
            </p>
          </div>

          {/* Experience level */}
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

          {/* JD upload (required) */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Job Description <span className="text-rose-500">*</span>
            </label>
            <input
              type="file"
              accept=".pdf,.docx,.md,.txt"
              onChange={(e) => setJdFile(e.target.files?.[0] ?? null)}
              className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-violet-500 file:mr-4 file:rounded-md file:border-0 file:bg-violet-50 file:px-3 file:py-1.5 file:text-violet-700"
            />
            <p className="text-xs text-slate-400 mt-1">
              PDF, DOCX, MD, or TXT. The JD drives ~80% of interview questions.
            </p>
          </div>

          {/* Resume upload (optional) */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Resume <span className="text-slate-400 font-normal">(optional)</span>
            </label>
            <input
              type="file"
              accept=".pdf,.docx"
              onChange={(e) => setResumeFile(e.target.files?.[0] ?? null)}
              className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-violet-500 file:mr-4 file:rounded-md file:border-0 file:bg-violet-50 file:px-3 file:py-1.5 file:text-violet-700"
            />
            <p className="text-xs text-slate-400 mt-1">
              Personalizes ~20% of questions to the candidate&apos;s experience.
            </p>
          </div>

          {/* Question count slider (enabled, 5-8) */}
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
              onChange={(e) => setNumQuestions(Number(e.target.value))}
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

  // ── Preview / starting phase ────────────────────────────────
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

      {/* Shortfall warning */}
      {preview?.needs_confirmation && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 mb-4 text-sm text-amber-700">
          This JD supports only <strong>{preview.usable_count}</strong> grounded
          questions (you requested {preview.requested}). The interview will proceed
          with {preview.usable_count} technical questions.
        </div>
      )}

      {/* Question list */}
      <div className="space-y-3 mb-6">
        {preview?.questions.map((q: PlanPreviewQuestion, i: number) => (
          <div
            key={i}
            className="border border-slate-200 rounded-lg px-4 py-3 bg-white"
          >
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xs font-medium text-slate-400">
                Q{i + 1}
              </span>
              <span
                className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                  DIFFICULTY_COLORS[q.difficulty] ?? "bg-slate-100 text-slate-600"
                }`}
              >
                {q.difficulty}
              </span>
              <span className="text-xs text-slate-400">
                {q.competency}
              </span>
              <span className="text-xs text-slate-300">
                ({q.source})
              </span>
            </div>
            <p className="text-sm text-slate-800">{q.question_text}</p>
          </div>
        ))}

        {/* Fixed questions note */}
        <div className="border border-dashed border-slate-200 rounded-lg px-4 py-3 text-sm text-slate-400">
          + 1 behavioral question + 1 project deep-dive (added automatically)
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm mb-4">
          {error}
        </div>
      )}

      {/* Action buttons */}
      <div className="flex gap-3">
        <button
          onClick={handleBackToForm}
          disabled={phase === "starting"}
          className="px-5 py-3 rounded-xl border border-slate-300 text-slate-700 hover:bg-slate-50 transition-colors text-sm font-medium disabled:opacity-50"
        >
          Back
        </button>
        <button
          onClick={handleRegenerate}
          disabled={phase === "starting"}
          className="px-5 py-3 rounded-xl border border-violet-300 text-violet-700 hover:bg-violet-50 transition-colors text-sm font-medium disabled:opacity-50"
        >
          Regenerate
        </button>
        <button
          onClick={handleStart}
          disabled={phase === "starting"}
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
```

Key design decisions for the implementer:
- **`PagePhase` type** controls which UI renders. `"form"` and `"generating"` share the form view (generating just disables the button). `"preview"` and `"starting"` share the preview view.
- **`buildFormData()`** is extracted because both `handleGenerate` and `handleRegenerate` need it. Regenerate = call preview again with the same form data (gets a new `draft_id`).
- **`handleBackToForm()`** lets the admin go back and change inputs (different JD, different N) before regenerating.
- **Shortfall warning** is shown inline above the question list when `needs_confirmation` is true. No separate modal — the admin can see the questions and decide. The "Start Interview" button is always enabled (the backend already enforced the floor; if we're here, usable_count >= 5).
- **Question count in the Start button** shows `usable_count + 2` (the 2 additive: behavioral + project) so the admin knows the real total.
- **File accepts** include `.md` and `.txt` because the gold-standard test JDs are `.md` files (`extract_jd_text` handles them via `jd_extract.py`).
- **All 4 experience levels enabled** — the `comingSoon` flags and "In production" badges are removed. The planner handles all levels.
- **Role is a free-text input** with a hint that the planner derives it from the JD. The backend's `job_role` Form field is sent as-is; the planner uses the JD's own title preferentially (`draft.role_title`). If left blank, sends `"Role from JD"`.
- **`sessionStorage` key** is `voice_session_${session_id}` — matches the existing consumer in `[sessionId]/page.tsx:19`.

- [ ] **Step 2: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS — no type errors. The old `startVoiceSessionFromJd` import is gone; new imports (`previewPlan`, `startFromDraft`, `PlanPreviewResponse`, `PlanPreviewQuestion`) resolve to Task 1 + 2 additions.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/interview/voice/start/page.tsx
git commit -m "feat(voice-start): two-step JD-driven plan preview + start-from-draft flow"
```

---

## Task 4: Manual integration test  `[inline]`

No automated frontend tests exist. Verify the flow end-to-end in the browser.

**Files:** none (verification only).

**Pre-req:** backend running (`npm run dev:backend`) with a valid `ANTHROPIC_API_KEY` in `backend/.env`, Redis up (`docker-compose up -d`), and one of the gold-standard JD files available (e.g. a `.md` JD in `C:\Users\Acer\Downloads\`).

- [ ] **Step 1: Start the dev servers**

Run: `npm run dev`

This starts the backend on `:8000` and frontend on `:3000`.

- [ ] **Step 2: Open the voice start page**

Navigate to `http://localhost:3000/interview/voice/start` in the browser. Verify:
- The form shows: Candidate Name, Job Role (free text), Experience Level (all 4 enabled, no "coming soon"), JD upload (required, marked with red asterisk), Resume (optional), and the question slider (5–8, enabled, accent-violet).
- The submit button says "Generate Interview Plan" and is disabled until a JD file is selected.
- No error states visible.

- [ ] **Step 3: Test the happy path**

1. Enter a candidate name.
2. Leave Job Role blank (the planner derives it from the JD).
3. Select "Senior" experience level.
4. Upload a gold-standard JD `.md` file.
5. Set the slider to 6.
6. Click "Generate Interview Plan".
7. Wait for the plan preview to appear (button should say "Generating plan..." while loading).
8. Verify the preview shows:
   - The role title from the JD (not "Role from JD") in the heading.
   - 6 question cards, each with a difficulty badge (easy/medium/hard in emerald/amber/rose), a competency label, a source tag (jd/resume), and the question text.
   - A dashed-border note about "+1 behavioral + 1 project deep-dive".
   - Back, Regenerate, and "Start Interview (8 questions)" buttons at the bottom.
9. Click "Regenerate" — verify new questions appear (different `draft_id`; questions may differ).
10. Click "Start Interview" — verify redirect to `/interview/voice/{sessionId}`.
11. Verify the interview room loads (the VoiceInterviewRoom component renders, asks for mic permission).

- [ ] **Step 4: Test the shortfall path**

1. Go back to `/interview/voice/start`.
2. Upload a very short/thin JD (e.g. create a file with just "Software Engineer needed. Must know Python." — two sentences).
3. Set slider to 8.
4. Click "Generate Interview Plan".
5. If the planner generates fewer than 8 but >= 5 questions: verify an amber warning appears saying "This JD supports only N grounded questions (you requested 8)".
6. Verify the Start button still works.
7. If the planner generates fewer than 5: verify a red error appears with the "too thin" message from the backend (422 response).

- [ ] **Step 5: Test error handling**

1. Try submitting without a JD file — verify the "Upload a job description" error appears inline (client-side validation, no network request).
2. Stop the backend, try generating — verify the "Is the backend running?" error appears.
3. Upload a non-document file (e.g. a `.png`) — verify the backend returns a 422 and the error message "Could not read the job description file" displays.

- [ ] **Step 6: Test the "Back" button**

1. Generate a plan successfully.
2. Click "Back" — verify the form reappears with the previously entered values (candidate name, experience level, slider position preserved in state).
3. Change the JD file and slider.
4. Click "Generate Interview Plan" — verify a new plan generates for the new inputs.

---

## Self-Review

**1. Spec coverage:** Checked against the FINAL DESIGN (GRILL_NOTES.md §2026-06-26) and the backend plan's "OUT (Phase 2)" section:

| Requirement | Task |
|---|---|
| Mandatory JD upload (was resume-only) | Task 3 — `jdFile` required, client-side validation |
| Keep resume optional | Task 3 — `resumeFile` optional, no asterisk |
| Enable N slider 5–8 | Task 3 — `min={5} max={8}`, enabled |
| Call `POST /voice/plan/preview` | Task 2 — `previewPlan(form)` |
| Render read-only plan preview | Task 3 — question cards in preview phase |
| Regenerate button | Task 3 — `handleRegenerate`, calls preview again |
| Blocking "too thin / only N possible" confirm | Task 3 — amber warning when `needs_confirmation`, 422 error for below-floor |
| Call `POST /voice/session/start-from-draft` | Task 2 — `startFromDraft(body)` |
| No inline question editing in v1 | Task 3 — preview is read-only, no edit controls |
| Role dropdown removed | Task 3 — replaced with free-text input |
| All experience levels enabled | Task 3 — no `comingSoon` flag |

**2. Placeholder scan:** No TBD/TODO/placeholder patterns found. All code is complete.

**3. Type consistency:** `PlanPreviewResponse` fields (`draft_id`, `role_title`, `questions`, `requested`, `usable_count`, `needs_confirmation`) match the backend's Pydantic model at `voice_api.py:137-143`. `PlanPreviewQuestion` fields match `PlannedQuestion.model_dump()` output. `StartFromDraftRequest` matches `voice_api.py:146-148`. `previewPlan`/`startFromDraft` function names used identically in `voice-api.ts` and `page.tsx`. `sessionStorage` key format `voice_session_${session_id}` matches the consumer at `[sessionId]/page.tsx:19`.
