# Voice Fixes — Implementation Plan (Items 1 & 2)

Derived from `GRILL_NOTES_2.md` (2026-06-28 FINAL DESIGN TREE). Item 3 (EOT model)
is **deferred** — it is spike-gated by design and needs an external ONNX artifact +
real transcripts that aren't available here. Barge-in is **untouched**.

Branch: `feat/jd-hyper-personalized-questions` (already carries related uncommitted
voice work — the `tts_turn_complete` playback handshake and the `_looks_wait_request`
regex fast-path. This plan **builds on** those, does not clobber them.)

Test baseline: `tests/test_turn_taking.py` + `tests/test_voice_turn_processor.py` =
30 passed. Run targeted: `./.venv/Scripts/python.exe -m pytest tests/<file> -q`
(full suite hangs post-run on orphaned aiosqlite threads — run targeted files).

---

## Task 1 — Item 1a: Silence ladder + accept-thinking grace

**Files:** `backend/src/services/interview/voice_turn_processor.py`,
`backend/src/routes/voice_ws.py`, `backend/tests/test_voice_turn_processor.py`,
`backend/tests/test_turn_taking.py`.

**Changes:**
1. Ladder constants: `SILENCE_PROMPT_SECS` 8→**12**, `SILENCE_CHECKIN_SECS` 18→**30**,
   `SILENCE_STRIKE_SECS` 30→**60**. Update the module docstring (lines 10-15) to match.
2. Reword `SILENCE_PROMPT_2` to **drop** "or are you running into any issues?" — keep the
   "Are you still there?" opener + a reassurance ("No rush — take the time you need.").
3. Add `SILENCE_GRACE_SECS = 30`. Add an optional `grace: bool = False` (or
   `first_delay` param) to `_start_silence_monitor` / `_silence_monitor` so the first
   nudge can be delayed by `SILENCE_GRACE_SECS` instead of `SILENCE_PROMPT_SECS`.
4. `open_candidate_turn_after_playback`: if Redis flag `silence_grace_pending` is set,
   start the monitor in grace mode and **clear** the flag; else normal.
5. Fix `_handle_wait_request` (voice_ws.py): after streaming the wait-ack it currently
   leaves **no** silence monitor running (dead air after a thinking request is never
   handled). Restart the monitor in grace mode and set `silence_grace_pending` so a
   reconnect/playback path also honors it.

**Acceptance criteria / tests (Rule 7 — encode WHY):**
- Ladder advances at 12/30/60 (monkeypatched, assert order of nudges + strike).
- New `SILENCE_PROMPT_2` wording does NOT contain "running into" / "issues".
- After accept-thinking grace, the FIRST nudge is delayed (~grace), not ~12s — assert the
  monitor does not nudge before the grace window elapses.
- `_handle_wait_request` leaves a live silence monitor running (regression: dead-air after
  a thinking request must still eventually nudge/advance).
- Existing 30 tests stay green (update the one asserting old PROMPT_2 wording).

---

## Task 2 — Item 1b: Scoring — stop the strict double-pass

**Files:** `backend/src/services/interview/voice_evaluation.py`,
`backend/src/prompts/voice_evaluation_prompt.txt`, new
`backend/tests/test_voice_evaluation_prompt.py`.

**Root bug:** `voice_evaluation_prompt.txt:36` says "Per-question scores from the running
scores provided" but `_build_prompt` never passes `running_scores` — so the final eval
cold-re-scores from the transcript, stricter than the live pass (double-pass strictness).

**Changes:**
1. `_build_prompt`: inject `running_scores` (formatted JSON of the live per-topic scores)
   into the template via a new `{running_scores}` placeholder.
2. Prompt: add the live scores block and instruct the evaluator to treat the provided
   per-question/topic scores as **authoritative** — populate `per_question` / `topic_scores`
   from them, **do not re-derive** per-question numbers. The 4 cross-cutting dimensions
   (communication, technical depth, confidence, relevance) + narrative remain the LLM's job.
3. Add one rubric line: "This was a spoken interview — award partial credit for partial
   answers and calibrate to the candidate's stated experience level."

**Acceptance criteria / tests:**
- `_build_prompt(...)` output contains the live `running_scores` values (regression: the
  prompt must never again reference data it wasn't given).
- Prompt contains the partial-credit / experience-calibration instruction.
- Prompt instructs "do not re-derive" per-question scores (consume the live ones).
- `.format()` still succeeds (no stray unescaped braces).

---

## Task 3 — Item 2: B′ free-form cross-questioning (LLM drives, code clamps)

**Files:** NEW `backend/src/prompts/voice_system_prompt.txt`,
`backend/src/services/llm/prompt_builder.py` (new `build_voice_system_prompt()` +
`build_voice_answer_evaluation_prompt()` — voice-only, text path untouched),
`backend/src/services/interview/voice_llm_orchestrator.py`,
new `backend/tests/test_voice_llm_orchestrator.py`.

**CRITICAL — text mode isolation:** `system_prompt.txt` and `build_answer_evaluation_prompt`
are SHARED with text mode (`llm_service.py` + `turn_manager.py`, which routes on
`action == "follow_up"` else finalizes). Teaching the 6 actions in the shared prompts would
regress text mode. So add VOICE-SPECIFIC prompt variants and switch only the two call sites in
`voice_llm_orchestrator.py`. Do NOT modify `system_prompt.txt` or the default
`build_answer_evaluation_prompt` text-mode instruction. `response_parser` needs NO change
(it returns the action string as-is; the orchestrator normalizes).

**6 actions** (replace the current `ask_question|follow_up|acknowledge|transition|wrap_up`):
`answer_clarification`, `follow_up`, `accept_thinking`, `redirect`, `acknowledge_advance`,
`wrap_up`. A question now spans MULTIPLE turns. Legacy `acknowledge`/`transition` map to
`acknowledge_advance`; unknown/fallback → `acknowledge_advance` (safe forward progress).

**Orchestrator routing (`run_llm_turn`):**
- **Score fires ONCE, at `acknowledge_advance`** (today it persists on every turn). Move the
  score-persist block into the advance branch only; conversational turns never score.
- `acknowledge_advance`: persist score for `current_q.topic` (confidence-gated), advance idx
  +1, reset `follow_up_count` + `non_advancing_turns`. `_acknowledgment_only` lead-in + next
  question (or wrap-up at the end).
- `follow_up`: keep model's question (`validate_single_question`), no score, increment
  `follow_up_count` + `non_advancing_turns`.
- `answer_clarification`: speak model's reply **verbatim** (do NOT strip its question — this is
  the bot answering the candidate), same question, no advance, no score, +`non_advancing_turns`.
- `accept_thinking`: brief ack, no advance, no score, set `silence_grace_pending`,
  +`non_advancing_turns`.
- `redirect`: steer back to current question, no advance, no score, +`non_advancing_turns`.
- `wrap_up`: enter wrap-up.

**5 clamps (the crown-jewel tests — Rule 7: reject invalid transitions):**
1. **Coverage** — never advance past N planned questions (advance into wrap-up at the end).
2. **No unscored advance** — at `acknowledge_advance`, if `running_scores` has no entry for
   `current_q.topic`, persist this turn's parsed score if present; if still none, record a
   flag + log loudly (Rule 9 — never silently skip).
3. **Follow-up cap** — `follow_up` past `max_follow_ups_for(current_q)` is forced to
   `acknowledge_advance`.
4. **No-skip** — exactly +1 index per finalization.
5. **Loop guard** — `non_advancing_turns >= 3` forces `acknowledge_advance`.

**Prompts (voice-only):** `voice_system_prompt.txt` — same persona/XML/scoring/confidence as
`system_prompt.txt` but the action list is the 6 B′ actions + one line each on when to use them
(keep "exactly ONE question per turn" and emit `<score_update>` only on `acknowledge_advance`).
`build_voice_answer_evaluation_prompt` turn_instruction — describe the 6 actions and that the
authoritative score is emitted at `acknowledge_advance`.

**Also fold in the Task-1 flag-leak fix:** clear `silence_grace_pending` at the top of
`run_llm_turn` (a real candidate utterance arrived → prior grace consumed); `accept_thinking`
re-sets it. This stops a wait-request's grace flag from leaking into the next question's monitor.

**Acceptance criteria / tests:**
- `acknowledge_advance` scores once + advances; `answer_clarification` / `accept_thinking` /
  `redirect` / `follow_up` neither score nor advance.
- `answer_clarification` preserves the model's question (regression for the "strips questions"
  bug — the core cross-questioning defect).
- Clamp 3: `follow_up` beyond cap force-advances.
- Clamp 5: 3 consecutive non-advancing turns force-advance.
- Clamp 2: advancing with no score recorded leaves a flag (never silently unscored).
- Clamp 1: never advances past the last question (enters wrap-up).
- `accept_thinking` sets `silence_grace_pending` and does not advance/score.

---

## Deferred — Item 3: EOT semantic turn-taking

Spike-gated by design (`GRILL_NOTES_2.md` Q5/Q6): requires the LiveKit ONNX turn-detector +
a validation run over real interview transcripts BEFORE wiring. Not implementable to 95%
confidence without those artifacts. Left exactly as designed for a follow-up session.

## Out of scope / untouched
Barge-in path; realtime/speech-to-speech; text/admin-config flow; the frontend
`tts_turn_complete` handshake (already correct in the working tree).
