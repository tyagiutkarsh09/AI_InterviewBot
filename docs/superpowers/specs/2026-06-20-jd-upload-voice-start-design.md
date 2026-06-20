# JD Upload on the Voice Interview Start Page

**Date:** 2026-06-20
**Branch:** `feat/jd-driven-interview-config`
**Status:** Design — awaiting user review

## Problem

The voice interview start page (`/interview/voice/start`) selects questions purely by
role × experience × skill keywords. There is no way for an interviewer to anchor a voice
interview to a specific job description. A JD-parsing pipeline already exists in the repo
(`analyze_jd` → `build_plan` → frozen `InterviewPlan`) but is wired only into the admin
config flow and the **text** interview — never into voice.

## Goal

Add a **mandatory JD file upload** to the voice start page. The page becomes
**admin-only**. On submit, the uploaded JD is parsed and used to generate the interview's
questions, so the voice interview asks JD-derived technical/general questions (about the
role's responsibilities, the candidate's relevant experience, etc.).

This is the "raw JD on voice start" approach — **not** the admin-config indirection.
The existing admin config feature is left as-is.

## Decisions (confirmed with user)

- **Parsing:** reuse the existing `analyze_jd` (LLM-based). Do **not** add a second parser.
- **Input type:** file **upload**, `.pdf` / `.docx` only (paste textarea rejected).
- **Access:** **admin-only** — both client-side (`AdminGuard`) and **backend-enforced**
  via the existing `X-Admin-Key` admin-key convention.
- **Dependencies:** add `pypdf` (PDF text) + `python-docx` (DOCX text).
- **Key Skills field:** **removed** from the voice form — the JD now supplies skills via
  `analyze_jd`; keeping it would be redundant and confusing.
- **Question-count knobs:** `total_questions=6`, `core_question_ratio=0.8` — fixed defaults
  matching the admin config form; **not** exposed on the voice UI.

## Scope constraint

This touches the in-progress voice pipeline (`voice_api.py`), which CLAUDE.md flags as
do-not-touch-without-asking. The user has explicitly authorized this change.
The change is **additive** — a new endpoint — leaving the existing JSON
`/voice/session/start` untouched to minimize risk to the fragile pipeline.

## Components

### 1. JD text extraction — new module `backend/src/lib/jd_extract.py`

```
extract_jd_text(filename: str, data: bytes) -> str
```

- Dispatch by lowercased extension: `.pdf` → pypdf, `.docx` → python-docx.
- Raise `JDExtractError` (new) on: unsupported extension, unreadable file, or extracted
  text that is empty / whitespace-only after stripping. **Fail loud — never return `""`.**
  (Repo rule 9: silent-empty is a known failure mode here.)
- One clear purpose; no LLM; unit-testable with small fixture bytes.

### 2. Backend endpoint — `POST /api/v1/voice/session/start-from-jd` (in `voice_api.py`)

- **Auth:** `dependencies=[Depends(require_admin)]`, importing `require_admin` from
  `src.routes.admin` (reuse, no duplicate auth logic).
- **Request:** `multipart/form-data`
  - `file: UploadFile` (required) — the JD
  - `candidate_name: str = Form("Candidate")`
  - `job_role: str = Form(...)`
  - `experience_level: ExperienceLevel = Form(ExperienceLevel.MID)`
- **Flow** (mirrors `admin.py:create_config`):
  1. Read bytes → `extract_jd_text(file.filename, data)`
  2. `analyze_jd(jd_text)` → `(jd_summary, jd_ideas)`
  3. `build_plan(role, experience_level, jd_summary, jd_ideas, total_questions=6, core_ratio=0.8)`
  4. `generate_introduction(candidate_name, job_role, len(plan.questions))`
  5. `create_voice_session(... questions_json=json.dumps([q.model_dump() for q in plan.questions]) ...)`
  6. Issue token + ws_url exactly as the existing start endpoint does.
- **Response:** identical `VoiceSessionStartResponse` (session_id, token, state, ws_url).
- **Error mapping (fail loud, distinct codes):**
  - `JDExtractError` → 422 ("Could not read the job description file.")
  - `JDAnalysisError` → 502 ("Could not analyze the job description. Try again.")
  - `InsufficientQuestionsError` → 422 (message from exception)
  - Empty/missing file → 422

### 3. Dependencies — `backend/requirements.txt`

- `pypdf==4.2.0`
- `python-docx==1.1.2`

(`python-multipart` is already present, so FastAPI file uploads work.)

### 4. Frontend — `frontend/src/app/interview/voice/start/page.tsx`

- Wrap the page body in `<AdminGuard>`.
- Add a **mandatory** file input: `accept=".pdf,.docx"`. Store the `File` in state;
  block submit with an inline error if none selected.
- **Remove** the Key Skills field and its state/usage.
- Keep Name / Job Role / Experience Level.
- On submit: build `FormData` (file + candidate_name + job_role + experience_level) and
  call the new service function. Rest of the flow (sessionStorage key
  `voice_session_{id}` → `router.push(/interview/voice/[sessionId])`) is unchanged.

### 5. Frontend service — `frontend/src/services/voice-api.ts`

```
startVoiceSessionFromJd(form: FormData): Promise<VoiceSessionStartResponse>
```

- `fetch` to `/api/v1/voice/session/start-from-jd`, method POST, body = `form`.
- Header: `X-Admin-Key: NEXT_PUBLIC_ADMIN_API_KEY`.
- **Do NOT set `Content-Type`** — the browser sets the multipart boundary automatically.
- Reuse the existing error-unwrapping shape (throw `ApiClientError` with `detail`).

## Data flow

```
Admin → voice/start (AdminGuard) → FormData{file, name, role, level}
  → POST /voice/session/start-from-jd  [X-Admin-Key enforced]
    → extract_jd_text → analyze_jd → build_plan(6, 0.8)
    → create_voice_session(questions = frozen plan)
    → {session_id, token, ws_url}
  → sessionStorage → /interview/voice/[sessionId] → existing WS pipeline
```

The frozen plan's questions (JD-derived + core bank + behavioral + project deep-dive)
become the voice session's questions, so the existing voice turn pipeline asks them
unchanged. No change to the WS pipeline, turn manager, or LLM orchestrator.

## Out of scope

- Voice wrap-up Q&A (`jd_summary` / `interview_config_id` on the voice session) — the text
  path has it; voice does not, and JD questions don't require it.
- The config-based `?configId=` path for voice (explicitly dropped in favor of inline JD).
- Exposing `total_questions` / `core_ratio` on the voice UI.
- Navigation links into `/admin/configs` (separate concern from this feature).

## Testing (intent-encoding, per repo rule 7)

- `extract_jd_text`: returns text for a valid PDF and DOCX fixture; **raises** on
  unsupported extension and on an empty/whitespace-only extraction. (Asserts the
  fail-loud contract, not just the happy path.)
- Endpoint: with a valid file + admin key, returns 201 and a session whose question count
  equals the built plan; the questions include JD-derived items (topic from `jd_ideas`).
- Endpoint auth: missing/wrong `X-Admin-Key` → 401 **before** any LLM call (proves the
  guard actually gates, not just decorates).
- Endpoint: a JD that yields too few questions surfaces 422 (not a half-built session).

## Open risks

- `analyze_jd` is an LLM call (haiku) and can be slow / fail; the endpoint blocks on it.
  Acceptable — the admin config path already accepts this latency. Errors fail loud (502).
- pypdf extraction quality on scanned/image PDFs is poor (no OCR). `extract_jd_text` will
  treat an empty extraction as a hard error, so the admin gets a clear "couldn't read"
  message rather than a silently empty interview.
