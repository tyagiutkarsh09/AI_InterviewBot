# JD-Driven Interview Configuration — Design

**Date:** 2026-06-19
**Status:** Approved (design); pending implementation plan
**Scope:** Text interview pipeline only. Voice pipeline untouched.

## Goal

Let recruiter/admin users create a reusable **interview configuration** anchored on a
mandatory job description. Candidate interviews are generated from that config as a
**frozen, deterministic plan**: roughly 80% of scored technical questions come from the
stable role-based question bank, ~20% are JD-specific, plus a fixed behavioral question
(disagreement with a colleague) and a project deep-dive. Add a resume-personalized warmup
and a smooth closing (outro) so the interview no longer ends abruptly.

## Locked decisions

- **Config storage:** SQLite (`backend/data/interviews.db`), a new `interview_configs`
  table, reusing the existing `aiosqlite` pattern from `models/interview_report.py`.
  Postgres is **not** touched.
- **80/20 scope:** the behavioral and project deep-dive questions count **inside**
  `total_questions`. Two slots are reserved for them; the remaining `total_questions − 2`
  are split 80/20 core/JD.
- **Determinism:** the entire plan (JD analysis result, chosen core-question IDs, generated
  JD questions, behavioral + project questions, order) is **built once and frozen into the
  config row** at creation time. Same config → identical scored plan for every candidate.
- **Resume personalization:** uses the existing external parser's `UserDetails` output, but
  only a **whitelisted** subset (`skills`, `current_company`). Drives one unscored warmup
  line via a template. No LLM call, no PII.
- **Smooth ending:** add one **forward-only** state, `WRAP_UP`, with a constrained
  candidate Q&A answered only from JD/config context.
- **Backward compatibility:** the legacy `POST /interview/start` stays unchanged; the
  config-based flow is additive.

### Context corrections discovered during exploration

- The repo's "PostgreSQL" persistence is actually **SQLite** — `interview.py` imports
  `save_report as save_report_to_pg`; `models/interview_report.py` writes to
  `interviews.db` via `aiosqlite`. Nothing writes to Postgres. CLAUDE.md's "PG provisioned,
  not written" framing is stale, but the "don't write Postgres" rule is honored.
- The state machine already includes a `WARMUP` state not listed in CLAUDE.md:
  `IDLE → STARTED → {WARMUP|QUESTIONING}; WARMUP → QUESTIONING; QUESTIONING → {self|EVALUATING} → COMPLETE`.
- Question routing is **already deterministic** — `turn_manager` asks straight from
  `session.questions[idx]`; the LLM only evaluates and follows up. Feeding the frozen plan's
  question list satisfies "ask from plan, not ad-hoc routing" with near-zero change there.

## 1. Storage

New file `backend/src/models/interview_config.py`, mirroring `interview_report.py`
(direct SQL, no ORM, module-level connection, `_init_tables`).

```
interview_configs
  id                  TEXT PRIMARY KEY
  title               TEXT NOT NULL
  role                TEXT NOT NULL
  experience_level    TEXT NOT NULL
  job_description     TEXT NOT NULL          -- required, non-empty
  total_questions     INTEGER NOT NULL
  core_question_ratio REAL NOT NULL DEFAULT 0.8
  jd_summary          TEXT NOT NULL DEFAULT '{}'   -- JSON: {skills, responsibilities, seniority_signals}
  interview_plan      TEXT NOT NULL DEFAULT '{}'   -- JSON: frozen ordered plan
  created_at          TEXT
```

**Fail-loud difference from the reports layer:** reports degrade silently to `None` if the
DB is unavailable. Config **create** must fail loud — if the write returns `False`, the
endpoint returns 500. A missing/half-saved config must never silently produce a broken
interview. Config **reads** at candidate-start that miss → 404 with a clear message.

## 2. Data models

New `backend/src/types/config.py`:

- **`JDSummary`** — `skills: list[str]`, `responsibilities: list[str]`,
  `seniority_signals: list[str]`.
- **`InterviewPlan`** — `questions: list[Question]` (ordered scored questions) plus markers
  for warmup/outro presence.
- **`InterviewConfig`** — the table fields as Pydantic, plus `jd_summary: JDSummary` and
  `interview_plan: InterviewPlan`.

Plan questions **reuse the existing `Question` model** so `turn_manager` and the evaluator
are unchanged. Each plan question is tagged with its source —
`bank_core | jd_generated | behavioral | project_deepdive` — via the existing `tags` field.

`SessionState` additive fields (no removals):
- `interview_config_id: Optional[str]`
- `jd_summary: Optional[dict]`
- `resume_details: Optional[dict]` — whitelisted subset only (`skills`, `current_company`)

The session's `questions` list is populated **from the frozen plan** instead of
`get_question_set`. `current_question_idx` and total (`len(questions)`) are unchanged.

## 3. Config creation + frozen plan

`POST /admin/configs` (reuses `x-admin-key` header auth). Build and freeze the whole plan
at creation:

1. **JD analysis (LLM, one call):** job description → `JDSummary` + a pool of JD-specific
   question ideas. Parsed with its own fallback. If this call fails, **creation fails loud**
   (502); nothing is persisted.
2. **Split (deterministic):**
   - `technical = total_questions − 2` (2 slots reserved for behavioral + project)
   - `jd_count = max(1, technical − round(technical × core_ratio))` — JD is floored at 1 so
     a JD-driven config always asks at least one JD-specific question.
   - `core_count = technical − jd_count`
   - Worked (`ratio=0.8`): `total=6` → technical 4 → jd 1, core 3, + behavioral + project =
     6 scored. `total=5` → technical 3 → jd 1, core 2. `total=8` → technical 6 → jd 1,
     core 5. `total=4` → technical 2 → jd 1, core 1 (floor applies). `total=10` →
     technical 8 → jd 2, core 6. Small pools won't be exactly 80% due to rounding + the JD
     floor; tests assert the **formula and the floors** (`core ≥ 1`, `jd ≥ 1`), not a
     literal 0.8.
3. **Core questions:** call `question_bank.get_question_set` once with the config's `role`
   and `experience_level` and `jd_summary.skills`, take `core_count`, **store the chosen
   question IDs**. The one-time RNG result is frozen, so the plan is deterministic per
   config.
4. **JD questions:** `jd_count` drawn from step 1's ideas; each gets a generic competency
   rubric so the existing evaluator works.
5. **Behavioral + project deep-dive:** fixed deterministic templates (not LLM) — a
   disagreement-with-a-colleague behavioral question and a "walk me through a recent
   project" deep-dive, each with a behavioral rubric.
6. **Order (fixed, deterministic):** `[core…] → [jd…] → behavioral → project_deepdive`.

**Validation — reject at creation (422), nothing persisted:** empty JD;
`total_questions < 4` (need ≥ 2 technical slots so both `core ≥ 1` and `jd ≥ 1` hold after
the 2 reserved slots); `core_ratio` not in the open interval `(0, 1)`; or the bank cannot
supply `core_count` questions.

## 4. Candidate start (config-based) + resume warmup

New `POST /interview/start-from-config`, body
`{ interview_config_id, candidate_name, resume_details? }`. Legacy `/interview/start` is
untouched.

- Load config (404 if missing). Populate session `questions` from the frozen plan; set
  `job_role`/`experience_level`/`required_skills` from the config and `jd_summary`.
- **Resume seam:** accept the external parser's `UserDetails`, store **only** `skills` and
  `current_company`. A single bounded function `personalize_warmup(details) -> str` builds
  one warmup line from those via a template (no LLM). Example: *"I see you've been at Acme
  working with Kubernetes — what have you most enjoyed building there?"* If no resume
  details are provided, fall back to the existing generic warmup.
- **Privacy guarantee:** `phone`, `email`, `linkedin_url`, `current_location`,
  `country_code` are never read into or stored on the session. Enforced by the whitelist and
  a test.

### Resume parser reference (external repo `qtst_v3_parser1`)

`UserDetails` Pydantic output: `name`, `email`, `linkedin_url`, `phone`, `country_code`,
`skills: list[str]`, `current_company`, `current_location`,
`last_company_experience_in_months`, `total_work_experience_in_months`. The parser has **no
hobbies/interests field**, so warmup personalization uses professional context
(`current_company` + a `skill`), not hobbies.

## 5. Interview run + smooth ending

The questioning loop is unchanged: `turn_manager` asks from `session.questions[idx]`; the
LLM evaluates and follows up (max 2 follow-ups per question, existing behavior).

Add one **forward-only** state, `WRAP_UP`:

```
QUESTIONING → {QUESTIONING, WRAP_UP}
WRAP_UP     → {WRAP_UP, EVALUATING}
EVALUATING  → COMPLETE
```

No backward transitions are added. When the last scored question is answered, transition to
`WRAP_UP`: the bot gives a closing line and asks "Any questions for me about the role?"
Candidate questions are answered by the LLM **constrained to JD/config/company context
only**; anything outside it → "That's something the recruiter can clarify." `WRAP_UP` turns
are **unscored**. Routing stays deterministic: a `MAX_OUTRO_QUESTIONS` cap (default 3) plus
an explicit "finish" control advances to `EVALUATING`. The LLM only drafts answers from
provided context — it never decides routing. Detecting "no thanks" in free text is
deliberately avoided (that would be fuzzy LLM routing).

## 6. LLM vs deterministic boundary

- **LLM:** JD analysis/extraction; per-answer evaluation + follow-up (existing); final
  evaluation/summary (existing); outro answers drafted from JD context.
- **Deterministic code:** plan building, 80/20 math, core-question freezing, question order,
  all state transitions, counts, warmup personalization. Behavioral/project questions are
  fixed templates.

## 7. Frontend

- **Admin:** new `/admin/configs/new` form — title, role, experience_level, required
  `job_description` textarea, `total_questions`, `core_question_ratio` (default 0.8) →
  `POST /admin/configs` with `x-admin-key`. Plus a minimal `/admin/configs` list. New
  `createConfig` / `listConfigs` in `frontend/src/services/api.ts`.
- **Candidate:** start flow accepts a `configId` (URL param/selection) and calls
  `start-from-config`, passing the `UserDetails` the existing upload flow produces. Upload
  and parsing are not rebuilt.

## 8. Error handling / fail-loud

- JD required → 422 (Pydantic `min_length` on `job_description`).
- Invalid config (total < 3, ratio out of range, insufficient bank questions) → 422, not
  persisted.
- JD-analysis LLM failure → 502, not persisted.
- Config not found at start → 404.
- Config write failure → 500 (loud).
- Malformed XML fallback in `response_parser.py` untouched.
- `score_update` propagation path untouched.

## 9. Tests

1. JD required — empty JD rejected (422).
2. Deterministic plan — reloading a config yields identical questions and order.
3. 80/20 split — formula counts correct for representative totals; `core + jd + 2 == total`;
   `core ≥ 1` and `jd ≥ 1` (floors hold).
4. Invalid config rejected — total < 4, ratio out of range, insufficient questions.
5. Malformed XML fallback preserved.
6. `score_update` still reaches the final report.
7. Invalid state transitions rejected, including no backward transition from `WRAP_UP`.
8. Warmup text never contains PII fields (`phone`, `email`, `linkedin_url`,
   `current_location`, `country_code`).
9. Outro answers stay within JD context; unknown → recruiter-clarify; cap advances to
   `EVALUATING`.
10. Missing resume details → graceful default warmup.

## 10. Explicitly not touched

Voice pipeline (`voice_ws_router`, `voice_api_router`, `websocket.ts`); Postgres;
`response_parser.py` XML logic; legacy `POST /interview/start`; existing scoring/evaluation
path.

## Open follow-ups (non-blocking)

- Exact transport of `UserDetails` from the upload flow into `start-from-config` (inline
  object vs reference) to be settled when wiring the parser; the `personalize_warmup` seam
  isolates this.
- Generic competency rubric text for JD/behavioral/project questions to be finalized during
  implementation.
