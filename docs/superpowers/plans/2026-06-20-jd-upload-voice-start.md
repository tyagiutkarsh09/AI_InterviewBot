# JD Upload on Voice Start — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a mandatory JD file upload (PDF/DOCX) to the admin-only voice interview start page so each voice interview asks 2 JD-derived questions parsed from the uploaded job description.

**Architecture:** New additive multipart endpoint `POST /api/v1/voice/session/start-from-jd` (admin-key enforced) extracts text from the uploaded file, runs the existing `analyze_jd` → `build_plan` pipeline (`core_ratio=0.5` → 2 JD questions), and feeds the frozen plan into `create_voice_session`. The existing JSON voice-start endpoint and the whole WS turn pipeline are untouched — JD question text is already spoken verbatim by `voice_llm_orchestrator`.

**Tech Stack:** FastAPI (Python), pypdf + python-docx for extraction, Next.js 14 (App Router) frontend, pytest.

**Spec:** `docs/superpowers/specs/2026-06-20-jd-upload-voice-start-design.md`

**Note on running tests:** Per the repo's known pytest-hang workaround, run **targeted test files** (e.g. `python -m pytest tests/test_jd_extract.py -v`), not the full suite. The new test files touch no aiosqlite, so they exit cleanly. All backend commands run from `backend/`.

---

## File Structure

- **Create** `backend/src/lib/jd_extract.py` — text extraction from PDF/DOCX bytes. One job: bytes → text, fail loud.
- **Create** `backend/tests/test_jd_extract.py` — extraction unit tests.
- **Modify** `backend/requirements.txt` — add `pypdf`, `python-docx`.
- **Modify** `backend/src/routes/voice_api.py` — add the `start-from-jd` endpoint.
- **Create** `backend/tests/test_voice_start_from_jd.py` — endpoint + auth tests.
- **Modify** `frontend/src/services/voice-api.ts` — add `startVoiceSessionFromJd`.
- **Modify** `frontend/src/app/interview/voice/start/page.tsx` — AdminGuard + JD file field, drop Key Skills, FormData submit.

---

## Task 1: Add extraction dependencies

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Add the two dependencies**

Add these two lines to `backend/requirements.txt` (after `python-multipart==0.0.9`):

```
pypdf==4.2.0
python-docx==1.1.2
```

- [ ] **Step 2: Install**

Run: `cd backend && pip install pypdf==4.2.0 python-docx==1.1.2`
Expected: "Successfully installed pypdf-4.2.0 python-docx-1.1.2" (or "already satisfied").

- [ ] **Step 3: Verify import works**

Run: `cd backend && python -c "import pypdf, docx; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add backend/requirements.txt
git commit -m "build(deps): add pypdf + python-docx for JD file extraction"
```

---

## Task 2: JD text extraction module

**Files:**
- Create: `backend/src/lib/jd_extract.py`
- Test: `backend/tests/test_jd_extract.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_jd_extract.py`:

```python
"""JD file extraction.

WHY: extraction must FAIL LOUD (raise) on unsupported files and on empty/garbage
text, so an admin never silently starts a JD-less interview (repo rule 9).
"""
import io

import pytest

from src.lib.jd_extract import extract_jd_text, JDExtractError


def _make_docx_bytes(text: str) -> bytes:
    from docx import Document

    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_docx_happy_path_returns_text():
    data = _make_docx_bytes("Senior Python Engineer\nBuild FastAPI services.")
    out = extract_jd_text("jd.docx", data)
    assert "Senior Python Engineer" in out
    assert "FastAPI" in out


def test_unsupported_extension_raises():
    with pytest.raises(JDExtractError):
        extract_jd_text("jd.txt", b"plain text")


def test_no_extension_raises():
    with pytest.raises(JDExtractError):
        extract_jd_text("jd", b"whatever")


def test_empty_docx_raises():
    data = _make_docx_bytes("   \n  \n")
    with pytest.raises(JDExtractError):
        extract_jd_text("jd.docx", data)


def test_pdf_extraction_wiring(monkeypatch):
    # Verify our PDF branch joins page text and strips, without a binary fixture.
    class _Page:
        def __init__(self, t):
            self._t = t

        def extract_text(self):
            return self._t

    class _Reader:
        def __init__(self, *_a, **_k):
            self.pages = [_Page("Backend Engineer "), _Page("Kafka, Postgres")]

    monkeypatch.setattr("src.lib.jd_extract.PdfReader", _Reader)
    out = extract_jd_text("jd.pdf", b"%PDF-fake")
    assert "Backend Engineer" in out
    assert "Kafka, Postgres" in out


def test_empty_pdf_raises(monkeypatch):
    class _Page:
        def extract_text(self):
            return ""

    class _Reader:
        def __init__(self, *_a, **_k):
            self.pages = [_Page()]

    monkeypatch.setattr("src.lib.jd_extract.PdfReader", _Reader)
    with pytest.raises(JDExtractError):
        extract_jd_text("jd.pdf", b"%PDF-fake")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_jd_extract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.lib.jd_extract'`

- [ ] **Step 3: Write the module**

Create `backend/src/lib/jd_extract.py`:

```python
"""Extract plain text from an uploaded JD file (PDF or DOCX).

Fails loud (raises JDExtractError) on unsupported extension, unreadable bytes, or
empty/whitespace-only extracted text — never returns "". A silently empty JD would
produce a JD-less interview, which defeats the feature.
"""
import io
import logging
import os

from pypdf import PdfReader

logger = logging.getLogger(__name__)


class JDExtractError(RuntimeError):
    """Raised when a JD file cannot be turned into usable text."""


def _extract_pdf(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _extract_docx(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs)


def extract_jd_text(filename: str, data: bytes) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    try:
        if ext == ".pdf":
            text = _extract_pdf(data)
        elif ext == ".docx":
            text = _extract_docx(data)
        else:
            raise JDExtractError(f"Unsupported JD file type: {ext or '(none)'}")
    except JDExtractError:
        raise
    except Exception as exc:
        logger.error("JD extraction failed for %s: %s", filename, exc)
        raise JDExtractError(f"Could not read JD file: {exc}") from exc

    if not text.strip():
        raise JDExtractError("JD file produced no extractable text")
    return text.strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_jd_extract.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/src/lib/jd_extract.py backend/tests/test_jd_extract.py
git commit -m "feat(voice): JD file text extraction (pdf/docx, fail-loud)"
```

---

## Task 3: Backend endpoint `start-from-jd`

**Files:**
- Modify: `backend/src/routes/voice_api.py`
- Test: `backend/tests/test_voice_start_from_jd.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_voice_start_from_jd.py`:

```python
"""POST /voice/session/start-from-jd.

WHY: the endpoint must (a) reject non-admins BEFORE any LLM call, (b) build the voice
session's questions from the JD plan (so JD questions actually get asked), and
(c) fail loud at each stage instead of starting a half-built interview.
"""
import io
from unittest.mock import patch

import pytest
from fastapi import HTTPException, UploadFile

from src.lib.jd_extract import JDExtractError
from src.services.llm.jd_analysis import JDAnalysisError
from src.services.interview.plan_builder import InsufficientQuestionsError
from src.services.interview.special_questions import build_jd_question
from src.types.config import InterviewPlan, JDSummary
from src.types.interview import ExperienceLevel


def _upload(name="jd.pdf", data=b"%PDF-bytes") -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(data))


class _Req:
    """Minimal stand-in for starlette Request (only .url is used)."""

    class _Url:
        scheme = "http"
        netloc = "testserver"

    url = _Url()


@pytest.mark.asyncio
async def test_wrong_admin_key_rejected_before_llm():
    from src.routes.admin import require_admin

    with pytest.raises(HTTPException) as exc:
        await require_admin(x_admin_key="not-the-key")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_happy_path_session_questions_come_from_jd_plan():
    from src.routes.voice_api import start_voice_session_from_jd

    summary = JDSummary(skills=["python"], responsibilities=["apis"], seniority_signals=["mid"])
    ideas = [{"question_text": "Explain async IO", "topic": "async"},
             {"question_text": "Design a rate limiter", "topic": "systems"}]
    plan = InterviewPlan(questions=[
        build_jd_question("Explain async IO", "async", 0),
        build_jd_question("Design a rate limiter", "systems", 1),
    ])

    with (
        patch("src.routes.voice_api.extract_jd_text", return_value="JD TEXT"),
        patch("src.routes.voice_api.analyze_jd", return_value=(summary, ideas)),
        patch("src.routes.voice_api.build_plan", return_value=plan),
    ):
        resp = await start_voice_session_from_jd(
            request=_Req(),
            file=_upload(),
            candidate_name="Alex",
            job_role="Backend Engineer",
            experience_level=ExperienceLevel.MID,
        )

    # Session was created with the JD plan's questions, in order.
    from src.services.audio.voice_session import get_voice_session
    import json

    sess = get_voice_session(resp.session_id)
    assert sess is not None
    stored = json.loads(sess["questions"])
    assert [q["question_text"] for q in stored] == ["Explain async IO", "Design a rate limiter"]
    assert resp.ws_url.endswith(f"/ws/interview/voice/{resp.session_id}?token={resp.token}")


@pytest.mark.asyncio
async def test_unreadable_file_returns_422():
    from src.routes.voice_api import start_voice_session_from_jd

    with patch("src.routes.voice_api.extract_jd_text", side_effect=JDExtractError("bad")):
        with pytest.raises(HTTPException) as exc:
            await start_voice_session_from_jd(
                request=_Req(), file=_upload(), candidate_name="Alex",
                job_role="Backend Engineer", experience_level=ExperienceLevel.MID,
            )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_jd_analysis_failure_returns_502():
    from src.routes.voice_api import start_voice_session_from_jd

    with (
        patch("src.routes.voice_api.extract_jd_text", return_value="JD TEXT"),
        patch("src.routes.voice_api.analyze_jd", side_effect=JDAnalysisError("boom")),
    ):
        with pytest.raises(HTTPException) as exc:
            await start_voice_session_from_jd(
                request=_Req(), file=_upload(), candidate_name="Alex",
                job_role="Backend Engineer", experience_level=ExperienceLevel.MID,
            )
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_insufficient_questions_returns_422():
    from src.routes.voice_api import start_voice_session_from_jd

    summary = JDSummary(skills=["python"])
    with (
        patch("src.routes.voice_api.extract_jd_text", return_value="JD TEXT"),
        patch("src.routes.voice_api.analyze_jd", return_value=(summary, [{"question_text": "Q", "topic": "t"}])),
        patch("src.routes.voice_api.build_plan", side_effect=InsufficientQuestionsError("not enough")),
    ):
        with pytest.raises(HTTPException) as exc:
            await start_voice_session_from_jd(
                request=_Req(), file=_upload(), candidate_name="Alex",
                job_role="Backend Engineer", experience_level=ExperienceLevel.MID,
            )
    assert exc.value.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_voice_start_from_jd.py -v`
Expected: FAIL — `ImportError: cannot import name 'start_voice_session_from_jd'`
(`test_wrong_admin_key_rejected_before_llm` should already PASS — it only uses `require_admin`.)

- [ ] **Step 3: Add imports to `voice_api.py`**

In `backend/src/routes/voice_api.py`, add these imports after the existing imports (the
`from src.services...` block near the top):

```python
from fastapi import Depends, File, Form, UploadFile

from src.lib.jd_extract import extract_jd_text, JDExtractError
from src.routes.admin import require_admin
from src.services.interview.plan_builder import build_plan, InsufficientQuestionsError
from src.services.llm.jd_analysis import analyze_jd, JDAnalysisError
```

Note: `APIRouter, HTTPException, Request, status` are already imported from fastapi — extend that line rather than duplicating; the snippet above only adds the new names.

- [ ] **Step 4: Add the endpoint**

Append to `backend/src/routes/voice_api.py` (after the existing `start_voice_session`
function, before the GET state endpoint is fine too):

```python
VOICE_TOTAL_QUESTIONS = 6
VOICE_CORE_RATIO = 0.5  # -> 2 JD questions + 2 core bank + behavioral + project


@router.post(
    "/session/start-from-jd",
    response_model=VoiceSessionStartResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def start_voice_session_from_jd(
    request: Request,
    file: UploadFile = File(...),
    candidate_name: str = Form("Candidate"),
    job_role: str = Form(...),
    experience_level: ExperienceLevel = Form(ExperienceLevel.MID),
) -> VoiceSessionStartResponse:
    data = await file.read()
    try:
        jd_text = extract_jd_text(file.filename or "", data)
    except JDExtractError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not read the job description file.",
        )

    try:
        jd_summary, jd_ideas = analyze_jd(jd_text)
    except JDAnalysisError as exc:
        logger.error("Voice JD analysis failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not analyze the job description. Try again.",
        )

    try:
        plan = build_plan(
            role=job_role,
            experience_level=experience_level,
            jd_summary=jd_summary,
            jd_question_ideas=jd_ideas,
            total_questions=VOICE_TOTAL_QUESTIONS,
            core_ratio=VOICE_CORE_RATIO,
        )
    except InsufficientQuestionsError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )

    session_id = str(uuid.uuid4())
    intro_text = generate_introduction(candidate_name, job_role, len(plan.questions))
    create_voice_session(
        session_id=session_id,
        candidate_name=candidate_name,
        job_role=job_role,
        experience_level=experience_level.value,
        required_skills=jd_summary.skills,
        questions_json=_json.dumps([q.model_dump() for q in plan.questions]),
        intro_text=intro_text,
    )
    logger.info(
        "Voice JD session created session=%s role=%s questions=%d",
        session_id, job_role, len(plan.questions),
    )

    token = _issue_token(session_id)
    ws_base = os.getenv("VOICE_WS_BASE")
    if not ws_base:
        scheme = "wss" if request.url.scheme == "https" else "ws"
        ws_base = f"{scheme}://{request.url.netloc}"

    return VoiceSessionStartResponse(
        session_id=session_id,
        token=token,
        state="INITIALIZING",
        ws_url=f"{ws_base}/ws/interview/voice/{session_id}?token={token}",
    )
```

Note: `_json` is already imported locally inside the existing `start_voice_session`
(`import json as _json`). Move that import to the module top (`import json as _json` near
the other stdlib imports) so both endpoints can use it. Remove the now-redundant local
`import json as _json` line inside `start_voice_session`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_voice_start_from_jd.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Sanity-check nothing else broke**

Run: `cd backend && python -m pytest tests/test_voice_regressions.py tests/test_plan_builder.py -v`
Expected: PASS (same as before this change).

- [ ] **Step 7: Commit**

```bash
git add backend/src/routes/voice_api.py backend/tests/test_voice_start_from_jd.py
git commit -m "feat(voice): admin-only start-from-jd endpoint feeding JD plan into session"
```

---

## Task 4: Frontend service function

**Files:**
- Modify: `frontend/src/services/voice-api.ts`

- [ ] **Step 1: Add the multipart service function**

In `frontend/src/services/voice-api.ts`, after the existing `startVoiceSession`
function, add:

```typescript
const ADMIN_KEY = process.env.NEXT_PUBLIC_ADMIN_API_KEY ?? "change-me-admin-key";

export async function startVoiceSessionFromJd(
  form: FormData
): Promise<VoiceSessionStartResponse> {
  const res = await fetch(`${API_BASE}/api/v1/voice/session/start-from-jd`, {
    method: "POST",
    // X-Admin-Key only — do NOT set Content-Type; the browser adds the
    // multipart boundary automatically when the body is FormData.
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
  return res.json() as Promise<VoiceSessionStartResponse>;
}
```

`API_BASE`, `ApiClientError`, and `VoiceSessionStartResponse` are already imported/defined
at the top of this file — reuse them, do not redeclare.

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/services/voice-api.ts
git commit -m "feat(voice): startVoiceSessionFromJd multipart service (admin key)"
```

---

## Task 5: Frontend voice start page (admin-only + mandatory JD)

**Files:**
- Modify: `frontend/src/app/interview/voice/start/page.tsx`

- [ ] **Step 1: Update imports and add AdminGuard**

In `frontend/src/app/interview/voice/start/page.tsx`:

Replace the import block at the top:

```typescript
import { useState } from "react";
import { useRouter } from "next/navigation";
import { startVoiceSession } from "@/services/voice-api";
import { ApiClientError } from "@/services/api";
import type { ExperienceLevel } from "@/types/interview";
```

with:

```typescript
import { useState } from "react";
import { useRouter } from "next/navigation";
import AdminGuard from "@/components/AdminGuard";
import { startVoiceSessionFromJd } from "@/services/voice-api";
import { ApiClientError } from "@/services/api";
import type { ExperienceLevel } from "@/types/interview";
```

- [ ] **Step 2: Swap skills state for a JD file state**

Remove this line:

```typescript
  const [skillsInput, setSkillsInput] = useState("");
```

and add in its place:

```typescript
  const [jdFile, setJdFile] = useState<File | null>(null);
```

- [ ] **Step 3: Rewrite `handleSubmit` to send FormData**

Replace the entire existing `handleSubmit` function with:

```typescript
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!effectiveRole) {
      setError("Please specify a job role.");
      return;
    }
    if (!jdFile) {
      setError("Please upload a job description (PDF or DOCX).");
      return;
    }
    setLoading(true);
    setError(null);

    const form = new FormData();
    form.append("file", jdFile);
    form.append("candidate_name", candidateName.trim() || "Candidate");
    form.append("job_role", effectiveRole);
    form.append("experience_level", experienceLevel);

    try {
      const res = await startVoiceSessionFromJd(form);
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
```

- [ ] **Step 4: Replace the Key Skills field with the JD upload field**

Find the Key Skills block (the `<div>` containing the "Key Skills" label and its
`skillsInput` input) and replace the whole `<div>...</div>` with:

```tsx
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">
            Job Description <span className="text-red-500">*</span>
          </label>
          <input
            type="file"
            accept=".pdf,.docx"
            onChange={(e) => setJdFile(e.target.files?.[0] ?? null)}
            className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-violet-500 file:mr-4 file:rounded-md file:border-0 file:bg-violet-50 file:px-3 file:py-1.5 file:text-violet-700"
            required
          />
          <p className="text-xs text-slate-400 mt-1">
            PDF or DOCX. The interview&apos;s questions are generated from this.
          </p>
        </div>
```

- [ ] **Step 5: Wrap the page in AdminGuard**

Change the outermost returned element from:

```tsx
  return (
    <div className="max-w-xl mx-auto">
```

to:

```tsx
  return (
    <AdminGuard>
    <div className="max-w-xl mx-auto">
```

and change the matching closing of that root `<div>` at the end of the return from:

```tsx
    </div>
  );
}
```

to:

```tsx
    </div>
    </AdminGuard>
  );
}
```

- [ ] **Step 6: Typecheck + build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: no type errors; build succeeds. (Fix any "unused variable" errors by removing
leftover references to `skillsInput`/`setSkillsInput` if any remain.)

- [ ] **Step 7: Commit**

```bash
git add frontend/src/app/interview/voice/start/page.tsx
git commit -m "feat(voice): admin-only voice start with mandatory JD upload"
```

---

## Task 6: End-to-end manual verification

**Files:** none (manual)

- [ ] **Step 1: Start infra + servers**

Run: `docker-compose up -d` then `npm run dev` (backend 8000 + frontend 3000).
Ensure `backend/.env` has a real `ANTHROPIC_API_KEY` (analyze_jd is a live LLM call) and
that `admin_api_key` matches `NEXT_PUBLIC_ADMIN_API_KEY` in `frontend/.env.local`.

- [ ] **Step 2: Confirm admin gating**

As a non-admin (or signed out), visit `http://localhost:3000/interview/voice/start`.
Expected: AdminGuard redirects (to `/login` or `/`). As an admin, the page renders with
the JD upload field and no Key Skills field.

- [ ] **Step 3: Confirm JD questions are asked**

As admin: enter a name, pick a role/level, upload a real JD PDF or DOCX, click "Begin
Voice Interview". Expected: the interview starts; over the session the bot asks the
2 JD-derived questions (recognizably about the uploaded JD's content) plus the core,
behavioral, and project questions.

- [ ] **Step 4: Confirm fail-loud paths**

Try submitting with no file → inline "Please upload a job description" (no request sent).
Upload a scanned/image-only PDF (no text layer) → expect the start to fail with
"Could not read the job description file." (422), not a silently empty interview.

- [ ] **Step 5: Final commit (if any tweaks were needed)**

```bash
git add -A
git commit -m "chore(voice): verification fixups for JD upload start"
```

---

## Notes / known constraints

- `analyze_jd` is a live haiku call; expect a short delay on submit. Errors surface as 502.
- `core_ratio=0.5` at `total=6` yields exactly 2 JD questions (`technical=4 → 2 JD + 2 core`).
  If the question bank can't supply the 2 core questions for the role/level, `build_plan`
  raises `InsufficientQuestionsError` → 422 (no half-built session).
- The full pytest suite hangs on orphaned aiosqlite threads after passing (known baseline);
  run targeted test files as shown above.
