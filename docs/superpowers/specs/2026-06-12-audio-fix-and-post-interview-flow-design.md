# Audio Fix & Post-Interview Flow Design

**Date**: 2026-06-12
**Status**: Approved

---

## Part 1: Fix Silent AI Audio

### Root Cause

The `AudioContext` created in `frontend/src/lib/voice-capture.ts:150` starts in the `"suspended"` state on most browsers. The browser requires an explicit `audioCtx.resume()` call tied to a user gesture. The current code never calls `resume()`, so `AudioBufferSourceNode.start()` schedules silently — no audio reaches the speakers.

### Fix

Two changes in `voice-capture.ts`:

1. **`_initAudio()`**: Call `await this.audioCtx.resume()` immediately after construction.
2. **`_scheduleMp3()`**: Safety net — `if (this.audioCtx.state === 'suspended') await this.audioCtx.resume()` before `decodeAudioData`.

No architectural changes. The rest of the pipeline (TTS streaming, binary WS frames, chunk buffering, sentence-complete decode, gapless scheduling) is correct.

---

## Part 2: Post-Interview Flow

### Approach

Synchronous pipeline. When the last question is answered:

1. Backend sets state to `EVALUATING`
2. Collects full transcript from Redis
3. Computes deterministic metrics (duration, timing, word counts)
4. Sends transcript + metrics to Claude (claude-sonnet) for qualitative analysis
5. Writes completed report to PostgreSQL `interview_reports`
6. Sends `{"event": "interview_complete", "report_url": "/report/{session_id}"}` over WS
7. Frontend redirects to report page with loading skeleton

Expected latency: ~10-15s for LLM analysis. Acceptable post-interview.

### Data Model

#### PostgreSQL: `interview_reports`

| Column | Type | Notes |
|--------|------|-------|
| id | UUID (PK) | |
| session_id | VARCHAR (unique, indexed) | |
| candidate_name | VARCHAR | |
| job_role | VARCHAR | |
| experience_level | VARCHAR | |
| started_at | TIMESTAMP | |
| ended_at | TIMESTAMP | |
| duration_seconds | INTEGER | |
| transcript | JSONB | `[{speaker, text, timestamp_ms}]` |
| metrics | JSONB | Deterministic metrics |
| analysis | JSONB | LLM-generated analysis |
| created_at | TIMESTAMP | |

#### Metrics JSONB

```json
{
  "total_questions": 5,
  "questions_answered": 5,
  "avg_answer_duration_s": 45.2,
  "total_candidate_words": 1200,
  "total_bot_words": 800,
  "follow_ups_used": 3,
  "barge_ins": 0,
  "silence_strikes": 0
}
```

#### Analysis JSONB

```json
{
  "summary": "string",
  "strengths": ["string"],
  "weaknesses": ["string"],
  "communication_clarity": {"score": 8, "explanation": "...", "evidence": "..."},
  "technical_depth": {"score": 7, "explanation": "...", "evidence": "..."},
  "confidence_consistency": {"score": 6, "explanation": "...", "evidence": "..."},
  "relevance": {"score": 7, "explanation": "...", "evidence": "..."},
  "overall_score": 7.2,
  "best_answer": {"question": "...", "summary": "...", "why": "..."},
  "weakest_answer": {"question": "...", "summary": "...", "why": "..."},
  "red_flags": ["string"],
  "hiring_recommendation": "strong_yes | yes | maybe | no | strong_no"
}
```

### Pipeline Steps

1. `voice_turn_processor` detects no more questions (current_question_idx + 1 >= total)
2. Calls `trigger_voice_evaluation(session_id)` (new function)
3. `trigger_voice_evaluation`:
   a. Sets state to `EVALUATING`, sends `{"event": "evaluating"}` over WS
   b. Loads full transcript + session metadata from Redis
   c. Computes deterministic metrics from transcript data
   d. Calls Claude (claude-sonnet) with evaluation system prompt + transcript + metrics
   e. Parses structured analysis response
   f. Writes report to PostgreSQL `interview_reports`
   g. Sets state to `COMPLETE`
   h. Sends `{"event": "interview_complete", "report_url": "/report/{session_id}"}` over WS
4. Frontend receives `interview_complete` event, redirects to report page
5. Report page calls `GET /api/v1/interview/report/{session_id}` (extend existing endpoint)

### Report UI (extend `/report/[sessionId]`)

Sections:

1. **Header** — Candidate name, role, date, duration, overall score badge
2. **Transcript timeline** — Speaker-labeled turns with timestamps, copy button
3. **Scores dashboard** — Category scores as horizontal bars, expand for evidence
4. **Analysis cards** — Strengths, weaknesses, red flags as collapsible sections
5. **Best/weakest answer** — Highlighted transcript excerpts with context
6. **Recommendation** — Verdict badge with reasoning paragraph
7. **Export bar** — Download JSON, copy transcript, PDF (stretch)

Loading state: Skeleton UI with "Generating analysis..." while LLM runs.

### Files to Create/Modify

**New files:**
- `backend/src/services/interview/voice_evaluation.py` — evaluation pipeline
- `backend/src/prompts/voice_evaluation_prompt.txt` — evaluation system prompt
- `backend/src/models/interview_report.py` — Pydantic model + DB schema
- `backend/migrations/001_interview_reports.sql` — PostgreSQL table creation

**Modified files:**
- `frontend/src/lib/voice-capture.ts` — AudioContext resume fix
- `backend/src/services/interview/voice_turn_processor.py` — trigger evaluation on last question
- `backend/src/routes/voice_ws.py` — handle evaluation event forwarding
- `frontend/src/app/report/[sessionId]/page.tsx` — extend with voice report sections
- `frontend/src/services/api.ts` — add report fetch for voice interviews
- `backend/src/routes/interview.py` — extend report endpoint for voice data
