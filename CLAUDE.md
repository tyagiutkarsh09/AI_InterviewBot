# CLAUDE.md

Rules and context for Claude Code in this repository.
Bias: caution over speed on non-trivial work. Read before writing. Surface uncertainty.

---

## Rules

### 1 — Think Before Coding
State assumptions explicitly. Ask rather than guess.
Push back when a simpler approach exists. Stop when confused.

### 2 — Simplicity First
Minimum code that solves the problem. Nothing speculative.
No abstractions for single-use code.

### 3 — Surgical Changes
Touch only what you must. Don't improve adjacent code.
Match existing style. Don't refactor what isn't broken.
The voice pipeline (`voice_ws_router`, `voice_api_router`, `websocket.ts`) is wired but incomplete — don't modify unless explicitly asked.

### 4 — Read Before You Write
Before adding code, read exports, immediate callers, shared utilities.
If unsure why existing code is structured a certain way, ask.
Specifically: read `backend/src/prompts/system_prompt.txt` before changing any LLM behavior. Read `response_parser.py` before touching XML response handling.

### 5 — Use the Model for Judgment Calls Only
LLM for: classification, drafting, summarization, extraction, evaluation.
NOT for: routing, retries, status-code handling, deterministic transforms.
If code can answer, code answers. This applies to Claude Code's work AND to the interview bot's own LLM calls.

### 6 — Surface Conflicts, Don't Average Them
If two patterns contradict, pick one (more recent / more tested).
Explain why. Flag the other for cleanup. Don't blend.

### 7 — Tests Verify Intent, Not Just Behavior
Tests must encode WHY behavior matters, not just WHAT it does.
A test that can't fail when business logic changes is wrong.
For this codebase: test that malformed XML triggers the fallback path. Test that `score_update` values actually propagate to final scores. Test that the state machine rejects invalid transitions — not just that valid ones advance.

### 8 — Checkpoint After Every Significant Step
Summarize what was done, what's verified, what's left.
Don't continue from a state you can't describe back.
If you lose track, stop and restate.

### 9 — Fail Loud
"Completed" is wrong if anything was skipped silently.
"Tests pass" is wrong if any were skipped.
Default to surfacing uncertainty, not hiding it.
Redis writes can silently fail — verify after mutation. Question bank loading can silently return empty — always check length.

### 10 — Match Conventions, Even If You Disagree
Conformance > taste inside the codebase.
If you think a convention is harmful, surface it. Don't fork it silently.

---

## Architecture

### Overview
Text-first interview bot with a planned voice pipeline. FastAPI backend, Next.js 14 frontend (App Router). Session state lives exclusively in Redis (4-hour TTL). PostgreSQL is provisioned but not actively written to — do not start writing to it without explicit discussion.

### State Ownership
- Single source of truth: Redis at `session:{session_id}`.
- Every mutation re-serializes the full `SessionState` Pydantic model immediately.
- No in-memory caching of session state across requests.

### State Machine (forward-only)
`IDLE → STARTED → QUESTIONING → EVALUATING → COMPLETE`
Defined in `backend/src/services/interview/state_machine.py`.
`QUESTIONING` self-loops once per question. Transitions to `EVALUATING` when `current_question_idx + 1 >= total_questions`.
Do NOT add backward transitions.

### Request Flow (text mode)
```
POST /api/v1/interview/start   → create session, select questions, return first question
POST /api/v1/interview/answer  → evaluate via LLM, advance state, repeat
GET  /api/v1/interview/report/{session_id} → full evaluation + transcript
```

### LLM Integration
- Routing (`backend/src/lib/anthropic_client.py`):
  - `interview` / `evaluation` → `claude-sonnet-4-6`
  - `follow_up` / `compression` → `claude-haiku-4-5-20251001`
- System prompt: `backend/src/prompts/system_prompt.txt` (file, not hardcoded)
- Responses: structured XML, parsed deterministically in `response_parser.py`
- `spoken_text` = candidate-facing. `internal_notes` + `score_update` = internal only. Don't leak across boundary.
- Malformed XML fallback: `action="acknowledge"`, raw text as `spoken_text`. This is intentional — don't remove it.

### Question Bank
- Source: `backend/data/questions.json` (loaded once, cached globally)
- Selection: scored by role keywords × skill tags × experience level, then weighted-random from top pool
- Changes affect variety across ALL sessions. Test with multiple sample draws, not one.

### Voice Pipeline (in progress — do not touch without asking)
- Backend: `voice_ws_router`, `voice_api_router` registered in `main.py`
- Frontend: `frontend/src/lib/websocket.ts`
- Design reference: `ai-interview-bot-arch.md` (authoritative)
- Pipeline: Deepgram STT → Claude LLM → ElevenLabs TTS

### Frontend
- Routes: `/interview/start` → `/interview/[sessionId]` → `/report/[sessionId]`
- `sessionStorage` key `interview_session_{sessionId}` carries `StartInterviewResponse` into interview page. Don't change this key or serialization format without updating both producer and consumer.
- API calls: `frontend/src/services/api.ts`, base URL from `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`)

---

## Commands

```bash
# Infrastructure
docker-compose up -d          # Redis + PostgreSQL

# Dependencies
npm run setup                 # everything
cd backend && pip install -r requirements.txt   # backend only
cd frontend && npm install                      # frontend only

# Development
npm run dev              # backend (8000) + frontend (3000)
npm run dev:backend      # backend only
npm run dev:frontend     # frontend only
```

### Environment
- `backend/.env` ← `backend/.env.example` — set `ANTHROPIC_API_KEY` (required for text mode)
- `frontend/.env.local` ← `frontend/.env.local.example`
- `DEEPGRAM_API_KEY` + `ELEVENLABS_API_KEY` needed only for voice pipeline