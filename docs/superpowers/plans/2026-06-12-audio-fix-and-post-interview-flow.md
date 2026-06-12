# Audio Fix & Post-Interview Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix silent AI audio playback and add a post-interview evaluation + report pipeline for voice interviews.

**Architecture:** The audio fix is a two-line `AudioContext.resume()` addition. The post-interview flow reuses the existing `Evaluation` type and `generate_final_evaluation` LLM call (already wired in `voice_llm_orchestrator.py`), extends the report endpoint to serve voice sessions from Redis, adds a richer voice-specific evaluation prompt, stores reports to PostgreSQL for permanence, and extends the existing report UI with a transcript timeline and export.

**Tech Stack:** FastAPI, Redis, PostgreSQL (asyncpg), Anthropic Claude, Next.js 14 (App Router), TypeScript, Pydantic

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `frontend/src/lib/voice-capture.ts` | Modify | AudioContext.resume() fix |
| `backend/migrations/001_interview_reports.sql` | Create | PostgreSQL table DDL |
| `backend/src/models/interview_report.py` | Create | Pydantic model + asyncpg read/write |
| `backend/src/prompts/voice_evaluation_prompt.txt` | Create | Evaluation system prompt for voice interviews |
| `backend/src/services/interview/voice_evaluation.py` | Create | Metrics computation + LLM evaluation + PG write |
| `backend/src/services/interview/voice_llm_orchestrator.py` | Modify | Call voice_evaluation instead of inline eval |
| `backend/src/routes/interview.py` | Modify | Extend report endpoint for voice sessions |
| `backend/src/routes/voice_ws.py` | Modify | Send interview_complete event |
| `frontend/src/lib/voice-capture.ts` | Modify (part 2) | Handle interview_complete event |
| `frontend/src/components/TranscriptTimeline.tsx` | Create | Speaker-labeled transcript with timestamps |
| `frontend/src/components/ReportCard.tsx` | Modify | Add transcript timeline + export buttons |
| `frontend/src/services/api.ts` | Modify | Add voice report fetch |

---

## Task 1: Fix Silent AI Audio

**Files:**
- Modify: `frontend/src/lib/voice-capture.ts:138-151` (\_initAudio)
- Modify: `frontend/src/lib/voice-capture.ts:89-96` (\_scheduleMp3)

- [ ] **Step 1: Add `audioCtx.resume()` in `_initAudio()`**

In `frontend/src/lib/voice-capture.ts`, after line 150 (`this.audioCtx = new AudioContext({ sampleRate: SAMPLE_RATE });`), add the resume call:

```typescript
// line 150 currently:
this.audioCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
// ADD after:
await this.audioCtx.resume();
```

The full `_initAudio` method lines 138-151 should become:

```typescript
private async _initAudio(): Promise<void> {
    this.mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: SAMPLE_RATE,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });

    this.audioCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
    await this.audioCtx.resume();
    await this.audioCtx.audioWorklet.addModule('/worklets/resampler.worklet.js');
```

- [ ] **Step 2: Add safety-net resume in `_scheduleMp3()`**

In `frontend/src/lib/voice-capture.ts`, at the start of `_scheduleMp3`, before the `decodeAudioData` call, add:

```typescript
private async _scheduleMp3(mp3: ArrayBuffer, gen: number): Promise<void> {
    if (!this.audioCtx || gen !== this.audioGen) return;
    if (this.audioCtx.state === 'suspended') await this.audioCtx.resume();
    let decoded: AudioBuffer;
```

- [ ] **Step 3: Verify the fix compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors related to voice-capture.ts

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/voice-capture.ts
git commit -m "fix: resume AudioContext to unblock TTS playback"
```

---

## Task 2: Create PostgreSQL Migration

**Files:**
- Create: `backend/migrations/001_interview_reports.sql`

- [ ] **Step 1: Create migration file**

```sql
-- backend/migrations/001_interview_reports.sql
-- Interview report persistence for voice + text sessions

CREATE TABLE IF NOT EXISTS interview_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id VARCHAR(64) NOT NULL UNIQUE,
    candidate_name VARCHAR(255) NOT NULL DEFAULT 'Candidate',
    job_role VARCHAR(255) NOT NULL DEFAULT '',
    experience_level VARCHAR(32) NOT NULL DEFAULT 'mid',
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    duration_seconds INTEGER,
    transcript JSONB NOT NULL DEFAULT '[]'::jsonb,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    analysis JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_interview_reports_session_id ON interview_reports(session_id);
CREATE INDEX IF NOT EXISTS idx_interview_reports_created_at ON interview_reports(created_at DESC);
```

- [ ] **Step 2: Verify migration syntax**

Run: `cd backend && docker exec -i $(docker ps -q -f ancestor=postgres:16-alpine) psql -U postgres -d interview_db -f - < migrations/001_interview_reports.sql`

If docker isn't running, verify syntax with: `cat migrations/001_interview_reports.sql` and confirm it's valid PostgreSQL.

- [ ] **Step 3: Commit**

```bash
git add backend/migrations/001_interview_reports.sql
git commit -m "feat: add interview_reports PostgreSQL migration"
```

---

## Task 3: Interview Report Pydantic Model + DB Access

**Files:**
- Create: `backend/src/models/interview_report.py`

- [ ] **Step 1: Create the model and DB access module**

```python
# backend/src/models/interview_report.py
"""
Pydantic models and asyncpg helpers for interview_reports table.

read/write only — no ORM. Direct SQL via asyncpg.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class InterviewMetrics(BaseModel):
    total_questions: int = 0
    questions_answered: int = 0
    avg_answer_duration_s: float = 0.0
    total_candidate_words: int = 0
    total_bot_words: int = 0
    follow_ups_used: int = 0
    barge_ins: int = 0
    silence_strikes: int = 0


class CategoryScore(BaseModel):
    score: float = Field(ge=0, le=10)
    explanation: str = ""
    evidence: str = ""


class HighlightedAnswer(BaseModel):
    question: str = ""
    summary: str = ""
    why: str = ""


class InterviewAnalysis(BaseModel):
    summary: str = ""
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    communication_clarity: CategoryScore = Field(default_factory=CategoryScore)
    technical_depth: CategoryScore = Field(default_factory=CategoryScore)
    confidence_consistency: CategoryScore = Field(default_factory=CategoryScore)
    relevance: CategoryScore = Field(default_factory=CategoryScore)
    overall_score: float = 0.0
    best_answer: HighlightedAnswer = Field(default_factory=HighlightedAnswer)
    weakest_answer: HighlightedAnswer = Field(default_factory=HighlightedAnswer)
    red_flags: list[str] = Field(default_factory=list)
    hiring_recommendation: str = "maybe"
    per_question: list[dict] = Field(default_factory=list)
    topic_scores: dict[str, float] = Field(default_factory=dict)


class InterviewReport(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    candidate_name: str = "Candidate"
    job_role: str = ""
    experience_level: str = "mid"
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_seconds: Optional[int] = None
    transcript: list[dict] = Field(default_factory=list)
    metrics: InterviewMetrics = Field(default_factory=InterviewMetrics)
    analysis: InterviewAnalysis = Field(default_factory=InterviewAnalysis)
    created_at: Optional[str] = None


# ---- asyncpg helpers ----

_pool = None


async def _get_pool():
    global _pool
    if _pool is None:
        import asyncpg
        from src.lib.settings import get_settings
        settings = get_settings()
        try:
            _pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=5)
        except Exception as exc:
            logger.error("Failed to create PG pool: %s", exc)
            return None
    return _pool


async def save_report(report: InterviewReport) -> bool:
    pool = await _get_pool()
    if pool is None:
        logger.error("No PG pool — report not saved for session %s", report.session_id)
        return False

    now = datetime.now(timezone.utc).isoformat()
    try:
        await pool.execute(
            """
            INSERT INTO interview_reports
                (id, session_id, candidate_name, job_role, experience_level,
                 started_at, ended_at, duration_seconds,
                 transcript, metrics, analysis, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (session_id) DO UPDATE SET
                transcript = EXCLUDED.transcript,
                metrics = EXCLUDED.metrics,
                analysis = EXCLUDED.analysis,
                ended_at = EXCLUDED.ended_at,
                duration_seconds = EXCLUDED.duration_seconds
            """,
            uuid.UUID(report.id),
            report.session_id,
            report.candidate_name,
            report.job_role,
            report.experience_level,
            datetime.fromisoformat(report.started_at) if report.started_at else None,
            datetime.fromisoformat(report.ended_at) if report.ended_at else None,
            report.duration_seconds,
            json.dumps(report.transcript),
            report.metrics.model_dump_json(),
            report.analysis.model_dump_json(),
            now,
        )
        logger.info("Report saved to PG for session %s", report.session_id)
        return True
    except Exception as exc:
        logger.error("Failed to save report session=%s: %s", report.session_id, exc)
        return False


async def get_report_by_session(session_id: str) -> Optional[InterviewReport]:
    pool = await _get_pool()
    if pool is None:
        return None

    try:
        row = await pool.fetchrow(
            "SELECT * FROM interview_reports WHERE session_id = $1",
            session_id,
        )
        if row is None:
            return None

        return InterviewReport(
            id=str(row["id"]),
            session_id=row["session_id"],
            candidate_name=row["candidate_name"],
            job_role=row["job_role"],
            experience_level=row["experience_level"],
            started_at=row["started_at"].isoformat() if row["started_at"] else None,
            ended_at=row["ended_at"].isoformat() if row["ended_at"] else None,
            duration_seconds=row["duration_seconds"],
            transcript=json.loads(row["transcript"]) if isinstance(row["transcript"], str) else row["transcript"],
            metrics=InterviewMetrics.model_validate_json(
                row["metrics"] if isinstance(row["metrics"], str) else json.dumps(row["metrics"])
            ),
            analysis=InterviewAnalysis.model_validate_json(
                row["analysis"] if isinstance(row["analysis"], str) else json.dumps(row["analysis"])
            ),
            created_at=row["created_at"].isoformat() if row["created_at"] else None,
        )
    except Exception as exc:
        logger.error("Failed to read report session=%s: %s", session_id, exc)
        return None
```

- [ ] **Step 2: Add asyncpg to requirements.txt**

Append `asyncpg>=0.29.0` to `backend/requirements.txt` if not already present.

- [ ] **Step 3: Verify import works**

Run: `cd backend && python -c "from src.models.interview_report import InterviewReport, InterviewMetrics, InterviewAnalysis; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/src/models/interview_report.py backend/requirements.txt
git commit -m "feat: add InterviewReport model with asyncpg persistence"
```

---

## Task 4: Voice Evaluation Prompt

**Files:**
- Create: `backend/src/prompts/voice_evaluation_prompt.txt`

- [ ] **Step 1: Create the evaluation prompt**

```text
You are an expert interview evaluator. You have just observed a complete voice interview. Analyze the transcript and produce a structured evaluation.

The interview was for the role of {job_role} at {experience_level} level.
Candidate: {candidate_name}

## Transcript
{transcript}

## Metrics
- Total questions: {total_questions}
- Questions answered: {questions_answered}
- Average answer duration: {avg_answer_duration_s}s
- Total candidate words: {total_candidate_words}
- Follow-ups used: {follow_ups_used}
- Barge-ins: {barge_ins}
- Silence strikes: {silence_strikes}

## Instructions

Evaluate the candidate across these dimensions. For each, give a score (0-10), a brief explanation, and a quote from the transcript as evidence.

1. Communication Clarity — How clearly and coherently did the candidate express ideas?
2. Technical Depth — How well did the candidate demonstrate technical knowledge relevant to the role?
3. Confidence & Consistency — Was the candidate confident? Were answers consistent and non-contradictory?
4. Relevance — Did the candidate answer the actual questions asked, or go off-topic?

Also provide:
- An overall summary (2-3 sentences)
- A list of strengths (3-5 bullet points)
- A list of weaknesses or gaps (2-4 bullet points)
- Any red flags (e.g., contradictions, evasiveness, misrepresentation)
- The best answer (which question, brief summary, why it stood out)
- The weakest answer (which question, brief summary, why it was weak)
- A hiring recommendation: one of strong_yes, yes, maybe, no, strong_no
- An overall score (0-10, weighted average of category scores)
- Per-question scores from the running scores provided

Respond in this exact JSON format (no markdown, no extra text):
{
  "summary": "...",
  "strengths": ["...", "..."],
  "weaknesses": ["...", "..."],
  "communication_clarity": {"score": N, "explanation": "...", "evidence": "..."},
  "technical_depth": {"score": N, "explanation": "...", "evidence": "..."},
  "confidence_consistency": {"score": N, "explanation": "...", "evidence": "..."},
  "relevance": {"score": N, "explanation": "...", "evidence": "..."},
  "overall_score": N.N,
  "best_answer": {"question": "...", "summary": "...", "why": "..."},
  "weakest_answer": {"question": "...", "summary": "...", "why": "..."},
  "red_flags": ["...", "..."],
  "hiring_recommendation": "...",
  "per_question": [{"question_id": "...", "question_text": "...", "topic": "...", "answer_text": "...", "score": N, "score_reasoning": "..."}],
  "topic_scores": {"topic_name": N.N}
}
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/prompts/voice_evaluation_prompt.txt
git commit -m "feat: add voice evaluation system prompt"
```

---

## Task 5: Voice Evaluation Pipeline

**Files:**
- Create: `backend/src/services/interview/voice_evaluation.py`

- [ ] **Step 1: Create the evaluation pipeline module**

```python
# backend/src/services/interview/voice_evaluation.py
"""
Voice interview evaluation pipeline.

1. Load transcript + session metadata from Redis
2. Compute deterministic metrics
3. Call Claude with evaluation prompt
4. Parse response into InterviewAnalysis
5. Save to PostgreSQL
6. Return InterviewReport
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.lib.anthropic_client import get_async_anthropic_client, get_model_for_task
from src.models.interview_report import (
    InterviewAnalysis,
    InterviewMetrics,
    InterviewReport,
    save_report,
)
from src.services.audio.voice_session import get_voice_session, set_voice_field

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "voice_evaluation_prompt.txt"


def _compute_metrics(voice_data: dict[str, Any]) -> InterviewMetrics:
    transcript: list[dict] = json.loads(voice_data.get("transcript", "[]"))
    questions: list[dict] = json.loads(voice_data.get("questions", "[]"))

    candidate_turns = [t for t in transcript if t.get("speaker") == "candidate"]
    bot_turns = [t for t in transcript if t.get("speaker") == "bot"]

    total_candidate_words = sum(len(t.get("text", "").split()) for t in candidate_turns)
    total_bot_words = sum(len(t.get("text", "").split()) for t in bot_turns)

    questions_answered = len(candidate_turns)
    avg_duration = 0.0  # no per-turn timing in current transcript format

    return InterviewMetrics(
        total_questions=len(questions),
        questions_answered=questions_answered,
        avg_answer_duration_s=avg_duration,
        total_candidate_words=total_candidate_words,
        total_bot_words=total_bot_words,
        follow_ups_used=int(voice_data.get("follow_up_count", 0)),
        barge_ins=int(voice_data.get("barge_in_count", 0)),
        silence_strikes=int(voice_data.get("silence_strikes", 0)),
    )


def _format_transcript(voice_data: dict[str, Any]) -> str:
    transcript: list[dict] = json.loads(voice_data.get("transcript", "[]"))
    lines = []
    for t in transcript:
        speaker = "Interviewer" if t.get("speaker") == "bot" else "Candidate"
        lines.append(f"[{speaker}]: {t.get('text', '')}")
    return "\n".join(lines)


def _build_prompt(voice_data: dict[str, Any], metrics: InterviewMetrics) -> str:
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    return template.format(
        job_role=voice_data.get("job_role", ""),
        experience_level=voice_data.get("experience_level", "mid"),
        candidate_name=voice_data.get("candidate_name", "Candidate"),
        transcript=_format_transcript(voice_data),
        total_questions=metrics.total_questions,
        questions_answered=metrics.questions_answered,
        avg_answer_duration_s=metrics.avg_answer_duration_s,
        total_candidate_words=metrics.total_candidate_words,
        follow_ups_used=metrics.follow_ups_used,
        barge_ins=metrics.barge_ins,
        silence_strikes=metrics.silence_strikes,
    )


async def run_voice_evaluation(session_id: str) -> InterviewReport:
    """
    Full evaluation pipeline. Returns InterviewReport.
    Raises on LLM failure (caller should handle).
    """
    voice_data = get_voice_session(session_id)
    if voice_data is None:
        raise ValueError(f"Voice session {session_id} not found")

    set_voice_field(session_id, "state", "EVALUATING")

    metrics = _compute_metrics(voice_data)
    prompt_text = _build_prompt(voice_data, metrics)

    client = get_async_anthropic_client()
    response = await client.messages.create(
        model=get_model_for_task("evaluation"),
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt_text}],
    )
    raw_json = response.content[0].text

    # Strip markdown fences if present
    cleaned = raw_json.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.index("\n")
        last_fence = cleaned.rfind("```")
        cleaned = cleaned[first_nl + 1:last_fence].strip()

    analysis_data = json.loads(cleaned)
    analysis = InterviewAnalysis.model_validate(analysis_data)

    # Build report
    transcript_raw: list[dict] = json.loads(voice_data.get("transcript", "[]"))
    now = datetime.now(timezone.utc).isoformat()

    report = InterviewReport(
        session_id=session_id,
        candidate_name=voice_data.get("candidate_name", "Candidate"),
        job_role=voice_data.get("job_role", ""),
        experience_level=voice_data.get("experience_level", "mid"),
        started_at=voice_data.get("started_at"),
        ended_at=now,
        duration_seconds=None,  # no start timestamp tracked yet
        transcript=transcript_raw,
        metrics=metrics,
        analysis=analysis,
    )

    # Persist to PG (best-effort)
    saved = await save_report(report)
    if not saved:
        logger.warning("Report not persisted to PG for session %s — Redis still has it", session_id)

    # Also store evaluation in Redis for the report endpoint fallback
    set_voice_field(session_id, "state", "COMPLETE")
    set_voice_field(session_id, "evaluation_report", report.model_dump_json())

    logger.info("Voice evaluation complete session=%s score=%.1f", session_id, analysis.overall_score)
    return report
```

- [ ] **Step 2: Verify import works**

Run: `cd backend && python -c "from src.services.interview.voice_evaluation import run_voice_evaluation; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/src/services/interview/voice_evaluation.py
git commit -m "feat: add voice evaluation pipeline with metrics + LLM analysis"
```

---

## Task 6: Wire Evaluation Into Voice LLM Orchestrator

**Files:**
- Modify: `backend/src/services/interview/voice_llm_orchestrator.py:185-242`

- [ ] **Step 1: Replace `_trigger_final_evaluation` with the new pipeline**

Replace the entire `_trigger_final_evaluation` function (lines 185-242) with:

```python
async def _trigger_final_evaluation(
    session_id: str, voice_data: dict[str, Any]
) -> None:
    """
    Hand off to the voice evaluation pipeline.
    Computes metrics, runs LLM evaluation, persists to PG + Redis.
    """
    logger.info("Triggering voice evaluation for session %s", session_id)
    try:
        from src.services.interview.voice_evaluation import run_voice_evaluation
        await run_voice_evaluation(session_id)
    except Exception as exc:
        logger.error(
            "Voice evaluation failed session=%s: %s", session_id, exc
        )
        set_voice_field(session_id, "state", "COMPLETE")
```

- [ ] **Step 2: Verify no import errors**

Run: `cd backend && python -c "from src.services.interview.voice_llm_orchestrator import run_llm_turn; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/src/services/interview/voice_llm_orchestrator.py
git commit -m "feat: wire voice evaluation pipeline into LLM orchestrator"
```

---

## Task 7: Send `interview_complete` Event Over WebSocket

**Files:**
- Modify: `backend/src/services/interview/voice_turn_processor.py:64-98`

- [ ] **Step 1: Add interview_complete event after evaluation**

In `voice_turn_processor.py`, modify the `stream_response` method. After the TTS completes and before setting state to `WAITING_FOR_CANDIDATE`, check if the session is COMPLETE and send the redirect event.

Replace lines 96-98 (the block after `finally`) with:

```python
        # Check if evaluation completed (state set to COMPLETE by evaluation pipeline)
        from src.services.audio.voice_session import get_voice_session
        session_data = get_voice_session(self.session_id)
        if session_data and session_data.get("state") == "COMPLETE":
            await _send_json(self.ws, {
                "event": "interview_complete",
                "report_url": f"/report/{self.session_id}",
            })
            return

        set_voice_field(self.session_id, "state", "WAITING_FOR_CANDIDATE")
        await _send_json(self.ws, {"event": "turn", "speaker": "candidate"})
        self._start_silence_monitor()
```

- [ ] **Step 2: Verify no import errors**

Run: `cd backend && python -c "from src.services.interview.voice_turn_processor import process_voice_turn; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/src/services/interview/voice_turn_processor.py
git commit -m "feat: send interview_complete event after voice evaluation"
```

---

## Task 8: Extend Report Endpoint for Voice Sessions

**Files:**
- Modify: `backend/src/routes/interview.py:98-139`

- [ ] **Step 1: Add voice session fallback to report endpoint**

Replace the `get_report` function (lines 98-139) with a version that checks both text sessions and voice sessions:

```python
@router.get("/report/{session_id}", response_model=GetReportResponse)
async def get_report(session_id: str) -> GetReportResponse:
    # Try text-mode session first (existing behavior)
    session = session_manager.get_session(session_id)
    if session is not None:
        if session.state != InterviewState.COMPLETE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Interview is not complete yet. Current state: {session.state.value}",
            )

        eval_ = session.evaluation
        if eval_ is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Evaluation missing.")

        started = session.started_at
        ended = session.ended_at
        duration = None
        if started and ended:
            from datetime import datetime
            duration = int(
                (datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds()
            )

        return GetReportResponse(
            session_id=session_id,
            candidate_name=session.candidate_name,
            job_role=session.job_role,
            experience_level=session.experience_level.value,
            overall_score=eval_.overall_score,
            recommendation=eval_.recommendation,
            strengths=eval_.strengths,
            weaknesses=eval_.weaknesses,
            summary=eval_.summary,
            per_question=[qr.model_dump() for qr in eval_.per_question],
            topic_scores=eval_.topic_scores,
            transcript=[t.model_dump() for t in session.transcript],
            started_at=started,
            ended_at=ended,
            duration_seconds=duration,
        )

    # Try voice session (Redis evaluation_report field or PG)
    from src.services.audio.voice_session import get_voice_session
    voice_data = get_voice_session(session_id)

    if voice_data is not None:
        state = voice_data.get("state", "")
        if state == "EVALUATING":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Interview is being evaluated.",
            )
        if state != "COMPLETE":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Interview is not complete yet. Current state: {state}",
            )

        report_json = voice_data.get("evaluation_report")
        if report_json:
            import json as _json
            from src.models.interview_report import InterviewReport
            report = InterviewReport.model_validate_json(report_json)
            return GetReportResponse(
                session_id=session_id,
                candidate_name=report.candidate_name,
                job_role=report.job_role,
                experience_level=report.experience_level,
                overall_score=report.analysis.overall_score,
                recommendation=report.analysis.hiring_recommendation,
                strengths=report.analysis.strengths,
                weaknesses=report.analysis.weaknesses,
                summary=report.analysis.summary,
                per_question=report.analysis.per_question,
                topic_scores=report.analysis.topic_scores,
                transcript=report.transcript,
                started_at=report.started_at,
                ended_at=report.ended_at,
                duration_seconds=report.duration_seconds,
            )

    # Try PG as last resort
    from src.models.interview_report import get_report_by_session
    pg_report = await get_report_by_session(session_id)
    if pg_report is not None:
        return GetReportResponse(
            session_id=session_id,
            candidate_name=pg_report.candidate_name,
            job_role=pg_report.job_role,
            experience_level=pg_report.experience_level,
            overall_score=pg_report.analysis.overall_score,
            recommendation=pg_report.analysis.hiring_recommendation,
            strengths=pg_report.analysis.strengths,
            weaknesses=pg_report.analysis.weaknesses,
            summary=pg_report.analysis.summary,
            per_question=pg_report.analysis.per_question,
            topic_scores=pg_report.analysis.topic_scores,
            transcript=pg_report.transcript,
            started_at=pg_report.started_at,
            ended_at=pg_report.ended_at,
            duration_seconds=pg_report.duration_seconds,
        )

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
```

- [ ] **Step 2: Verify no import errors**

Run: `cd backend && python -c "from src.routes.interview import router; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/src/routes/interview.py
git commit -m "feat: extend report endpoint to serve voice session reports"
```

---

## Task 9: Handle `interview_complete` in Frontend

**Files:**
- Modify: `frontend/src/lib/voice-capture.ts:232-254`

- [ ] **Step 1: Add `interview_complete` handler**

In the `_handleServerControl` method, add a case before the `else` block (before line 251):

```typescript
    } else if (event === 'interview_complete') {
      this._setState('idle');
      this.onControlMessage(data);
    } else if (event === 'evaluating') {
      this.onControlMessage(data);
    } else {
```

The full updated `_handleServerControl` should be:

```typescript
  private _handleServerControl(data: Record<string, unknown>): void {
    const event = data.event as string;

    if (event === 'transcript') {
      this.onTranscript(
        data.text as string,
        data.is_final as boolean
      );
    } else if (event === 'turn') {
      const speaker = data.speaker as string;
      this._setState(speaker === 'bot' ? 'bot_speaking' : 'idle');
    } else if (event === 'barge_in') {
      this.stopBotAudio();
      this._setState('speaking');
    } else if (event === 'ping') {
      this._sendControl({ event: 'pong' });
    } else if (event === 'tts_sentence_complete') {
      this._onSentenceComplete();
    } else if (event === 'interview_complete') {
      this._setState('idle');
      this.onControlMessage(data);
    } else if (event === 'evaluating') {
      this.onControlMessage(data);
    } else {
      this.onControlMessage(data);
    }
  }
```

- [ ] **Step 2: Verify compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/voice-capture.ts
git commit -m "feat: handle interview_complete and evaluating events in voice capture"
```

---

## Task 10: Transcript Timeline Component

**Files:**
- Create: `frontend/src/components/TranscriptTimeline.tsx`

- [ ] **Step 1: Create the component**

```tsx
// frontend/src/components/TranscriptTimeline.tsx
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
```

- [ ] **Step 2: Verify compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/TranscriptTimeline.tsx
git commit -m "feat: add TranscriptTimeline component with copy and download"
```

---

## Task 11: Add Transcript Timeline to ReportCard

**Files:**
- Modify: `frontend/src/components/ReportCard.tsx`

- [ ] **Step 1: Import and add TranscriptTimeline**

At the top of `ReportCard.tsx`, add the import after the existing imports:

```typescript
import TranscriptTimeline from "./TranscriptTimeline";
```

Then, at the end of the `<div className="space-y-6">` container (before the closing `</div>` on the last line), add the TranscriptTimeline after the Question Breakdown section:

```tsx
      {report.transcript.length > 0 && (
        <TranscriptTimeline transcript={report.transcript} />
      )}
```

- [ ] **Step 2: Verify compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ReportCard.tsx
git commit -m "feat: show transcript timeline in interview report"
```

---

## Task 12: Handle Voice Interview Redirect in VoiceInterviewRoom

**Files:**
- Modify: `frontend/src/components/VoiceInterviewRoom.tsx` (or the voice interview page component)

This task ensures the frontend redirects to the report page when it receives `interview_complete`.

- [ ] **Step 1: Find the voice interview component**

Check `frontend/src/components/VoiceInterviewRoom.tsx` or `frontend/src/app/interview/voice/[sessionId]/page.tsx` — whichever sets up the `VoiceCapture.onControlMessage` callback.

- [ ] **Step 2: Add redirect handler**

In the `onControlMessage` callback, add handling for the `interview_complete` event:

```typescript
vc.onControlMessage = (data) => {
  if (data.event === 'interview_complete') {
    const reportUrl = data.report_url as string;
    router.push(reportUrl);
    return;
  }
  if (data.event === 'evaluating') {
    // Show "evaluating" UI state
    setInterviewState('evaluating');
    return;
  }
  // ... existing handling
};
```

This requires `useRouter` from `next/navigation` to be available in the component. If not already imported, add it.

- [ ] **Step 3: Verify compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/VoiceInterviewRoom.tsx  # or the page file
git commit -m "feat: redirect to report page on interview_complete"
```

---

## Task 13: Integration Smoke Test

- [ ] **Step 1: Start infrastructure**

Run: `docker-compose up -d`
Verify Redis and PostgreSQL are running.

- [ ] **Step 2: Run migration**

Run: `docker exec -i $(docker ps -q -f ancestor=postgres:16-alpine) psql -U postgres -d interview_db < backend/migrations/001_interview_reports.sql`
Expected: `CREATE TABLE` / `CREATE INDEX` output

- [ ] **Step 3: Start backend**

Run: `npm run dev:backend`
Verify no startup errors in logs.

- [ ] **Step 4: Start frontend**

Run: `npm run dev:frontend`
Verify no compilation errors.

- [ ] **Step 5: Manual audio test**

1. Open `http://localhost:3000/interview/start`
2. Start a voice interview
3. Speak a test answer
4. Verify you can **hear** the AI response (the AudioContext fix)
5. Check browser console for any audio decode errors

- [ ] **Step 6: Manual evaluation test**

1. Complete a full voice interview (answer all questions)
2. Verify the "evaluating" state appears briefly
3. Verify automatic redirect to `/report/{sessionId}`
4. Verify the report loads with transcript, scores, and analysis
5. Verify the transcript timeline shows speaker labels
6. Verify copy and download buttons work

- [ ] **Step 7: Commit any fixes needed**

If any adjustments were needed during testing, commit them as a single fix commit.
