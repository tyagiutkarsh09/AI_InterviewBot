# Grill Notes — Voice JD/Resume Interview Redesign

Working doc for the grill-me session. Records decisions as we resolve them so the
thread survives summarization. Source request: fix the JD-voice interview output —
deterministic intro, admin-selectable question count, resume questions, difficulty
ramp, proper closing.

---

## 2026-06-21 — Checkpoint 1

### Decisions resolved
1. **Counting model** — The admin's number = **technical questions only** (5–10,
   default 5). Behavioral (disagreement), project deep-dive, and resume questions
   are **additive**, outside that count. Breaks current `plan_math` where
   `RESERVED_SLOTS = total − 2`.
2. **Resume questions source** — There is NO resume anywhere in the voice flow
   today (the file field is the JD). To add resume questions we add a resume
   upload + a resume-analysis step (reuse `jd_extract` for PDF/DOCX → text; the
   resume→questions LLM step is net-new). Resume is **optional** — slots omitted
   when absent.
3. **Opening sequence** — Root cause of "question in the introduction" found:
   `voice_session.py:74` glues `intro_text + first_q_text` into ONE bot turn
   tagged `type:"question"`. Fix = split into 3 turns: (1) deterministic intro
   STATEMENT, no question; (2) deterministic merged ease-in = "whenever you're
   ready, let's start with…" + an EASY first question; (3) Q2 onward. No real
   gated "are you ready?" pause (avoids an extra round-trip).

4. **Document model = Resume primary + JD optional (decision C).** Resume is the
   primary upload (drives personalized questions, optional). JD becomes an
   OPTIONAL second upload that tops up role-specific technical questions. The
   question bank (role dropdown + level) is the always-on technical base. The JD
   is NOT deleted — it's demoted to optional filler. Confirmed user's instinct:
   core technical questions already come from the dropdown via the bank
   (`question_bank.py:39-56`); JD only added extra on top.

### Known constraint (fail-loud, not hidden)
- **Bank is tiny: 15 questions total; only 5 eligible for `junior`** (3 junior +
  2 "all"; mid/senior/staff filtered out for a junior candidate). So
  junior + no JD + count>5 → InsufficientQuestionsError or repeats.
  AGREED handling: when no JD uploaded, **cap selectable count to bank capacity
  for that level** (junior→5); full 5–10 unlocked once a JD is attached.
  Expanding `questions.json` is noted as separate future work.
- mid level has 11 eligible → 10 is fine there.

### Open branches
- Count math — rewrite `plan_math` for the additive model + the no-JD cap.
- Resume analysis — new LLM step mirroring `analyze_jd`; what fields/questions.
- Admin count selector UI + the no-JD cap behavior.

### Next questions
- Resume analysis output shape (last big unknown).

---

## 2026-06-21 — Checkpoint 2

### Decisions resolved (continued)
5. **Difficulty ramp** — strictly the **first two technical slots are the two
   easiest eligible** questions (prefer `easy`, then `junior`-level); the rest
   keep current ordering. NOT a gradual whole-interview ramp. The Q3 ease-in turn
   uses slot 1. Resume questions are **medium**, placed after the easy openers.
6. **Closing** — Root cause of "ends immediately": the tested **WRAP_UP outro is
   wired into the TEXT flow only** (`interview.py:161-209`); the voice
   orchestrator returns `COMPLETION_MESSAGE` + triggers eval instantly
   (`voice_llm_orchestrator.py:106,183,210`). FIX = **port the full WRAP_UP outro
   into the voice orchestrator**, 3 beats: (1) deterministic wind-down + "any
   questions for me about the role/team?"; (2) candidate Q&A via existing
   `outro.answer_candidate_question`, cap `MAX_OUTRO_QUESTIONS=3`, JD-context only,
   recruiter-fallback; (3) deterministic next-steps + warm sign-off → THEN eval.

### Canonical candidate-experienced sequence (assembled from decisions 1–6)
1. Intro STATEMENT (deterministic, no question)
2. Ease-in turn = "whenever you're ready…" + EASY Q1
3. EASY Q2
4. Remaining technical (bank core + JD questions if JD uploaded), medium+
5. Resume questions (medium) interleaved in the middle
6. Behavioral (disagreement) — fixed
7. Project deep-dive (proud project) — fixed
8. WRAP_UP outro: wind-down → candidate Q&A (≤3) → next-steps sign-off
9. Evaluation

### Next questions
- Resume analysis output shape (extract what; how many Qs; PII guard; skills merge).

---

## 2026-06-21 — FINAL DESIGN (stands alone as the spec)

Goal: fix the admin-triggered voice interview (`/voice/session/start-from-jd`) so
it opens deterministically, is resume-personalized, lets the admin choose how many
technical questions, ramps difficulty for juniors, and closes properly instead of
cutting to evaluation. All work lives in the voice pipeline (explicitly authorized
by the user despite the CLAUDE.md "don't touch" default).

### 1. Documents & inputs (form + endpoint)
- **Resume upload** — PRIMARY, OPTIONAL. Drives personalized questions + skills.
- **JD upload** — OPTIONAL filler. Adds role-specific technical questions + skills.
- **Role dropdown + experience level** — always-on; drive bank question selection.
- **Question-count selector** — NEW. Min 5, max 10, default 5. Counts TECHNICAL
  questions only.
- Endpoint route name kept (`/voice/session/start-from-jd`); form RELABELED
  (resume first, JD second, + count selector). Reuse `jd_extract` (PDF/DOCX→text)
  for the resume too.

### 2. Counting model (additive)
- Admin's number N (5–10) = **technical questions only**.
- Behavioral (disagreement), project deep-dive, and the 2 resume questions are
  **ADDITIVE** — they sit OUTSIDE N.
- Rewrite `plan_math`: drop `RESERVED_SLOTS = total − 2`. New total session =
  N technical + 2 resume (if resume) + behavioral + project.
- When JD uploaded: split N between bank-core and JD questions via
  **`core_ratio = 0.7`** (~70% bank / 30% JD).
- When NO JD: all N from the bank.

### 3. Bank capacity guard (fail-loud)
- Bank = 15 questions total; **junior candidate has only 5 eligible** (3 junior +
  2 "all"). So junior + no JD caps technical at 5.
- Behavior: when no JD is uploaded, **cap the selectable count to the level's bank
  capacity** (junior→5); unlock full 5–10 once a JD is attached.
- Expanding `questions.json` = separate future work, NOT in this change.

### 4. Resume analysis (NEW LLM step, mirrors `analyze_jd`)
- Extract `skills[]` (merged with JD skills — union — to feed the bank) +
  `experiences[]` (recent roles/projects the candidate can speak to).
- Generate **exactly 2** resume questions (fixed), difficulty **medium**.
- PII guard: mirror the JD prompt prohibition (never family/age/gender/nationality/
  religion/etc.); reference work/projects ONLY (same posture as warmup whitelist).
- Runs at session creation, frozen into the plan. Omitted entirely if no resume.

### 5. Opening sequence (fixes intro+Q1 glue at `voice_session.py:74`)
Split the single glued opening turn into THREE turns:
1. Intro STATEMENT — deterministic, NO question. Also fix `generate_introduction`
   text (remove the false "after a quick warm-up" promise).
2. Ease-in turn — deterministic "whenever you're ready, let's start with…" +
   EASY Q1 (merged "are you ready", no separate gated pause).
3. EASY Q2, then the rest.

### 6. Difficulty ramp
- Strictly the **first two technical slots = the two easiest eligible** questions
  (prefer `easy`, then `junior`-level). Rest keep current ordering. NOT a gradual
  whole-interview ramp.

### 7. Closing (port WRAP_UP outro into voice)
- The tested WRAP_UP outro exists in the TEXT flow only (`interview.py:161-209`);
  voice just returns `COMPLETION_MESSAGE` + evaluates instantly. Port all 3 beats
  into `voice_llm_orchestrator`:
  1. Deterministic wind-down + "any questions for me about the role/team?"
  2. Candidate Q&A via `outro.answer_candidate_question`, cap
     `MAX_OUTRO_QUESTIONS=3`, answered from job context, recruiter-fallback.
  3. Deterministic next-steps + warm sign-off → THEN trigger evaluation.

### Canonical candidate-experienced sequence
intro statement → ease-in + EASY Q1 → EASY Q2 → remaining technical (bank +
JD-if-present) → 2 resume questions (medium) → behavioral (disagreement) →
project deep-dive → WRAP_UP outro (wind-down → candidate Q&A ≤3 → sign-off) →
evaluation.

### Files in scope (anticipated)
- `routes/voice_api.py` — accept resume + count; build plan; (no JD required).
- `services/llm/resume_analysis.py` (NEW) + `prompts/resume_analysis_prompt.txt`
  (NEW).
- `services/interview/plan_math.py` — additive rewrite + no-JD cap.
- `services/interview/plan_builder.py` — resume slots, difficulty ordering,
  optional JD.
- `services/interview/special_questions.py` — resume question builder.
- `services/audio/voice_session.py:74` — split intro/ease-in/Q1 seeding.
- `services/interview/warmup.py` — intro text fix; ease-in builder.
- `services/interview/voice_llm_orchestrator.py` — WRAP_UP outro port.
- `services/interview/voice_turn_processor.py` — outro turn handling.
- `frontend/.../interview/voice/start/page.tsx` + `services/voice-api.ts` — resume
  field, count selector, relabel.
- Tests across all of the above (TDD; tests encode WHY per CLAUDE.md rule 7).

### Open items deferred (NOT in this change)
- Expanding `questions.json` for deeper junior/mid coverage.
- Any change to the text/config flow (this is voice-only).

---

## 2026-06-25 — NEW SESSION: Hyper-personalized JD-driven questioning

New request: make the **JD the basis of 70–80% of questioning**, with **JD priority >
resume > role dropdown**. Add a JD upload field to the voice start page. Example JDs
provided are all **mechanical / medical-device** roles (Stryker etc.).

### Verified findings (pre-grill, 95%+ confidence)
- **This directly REVERSES the 06-21 "FINAL DESIGN" (decision #4).** That design made
  Resume PRIMARY, JD OPTIONAL filler, and the **bank 70% (`VOICE_CORE_RATIO=0.7`)**.
  New request wants JD PRIMARY ~70–80%, resume secondary, bank minimized. Inversion.
- **Backend already accepts a JD upload.** `voice_api.py:145` takes `jd: Optional[UploadFile]`
  and runs `analyze_jd` → `jd_ideas`. **Frontend just doesn't send it** — the voice
  start form (`voice/start/page.tsx`) only appends `resume`. So "add JD field" is
  largely a frontend wiring task, not net-new backend.
- **The question bank is the real problem.** 15 questions, **100% software/CS**
  (algorithms, React, K8s, SQL, system_design). The Role dropdown lists only software
  roles. For a mechanical/medical-device JD, every bank ("core") question is irrelevant
  → with `core_ratio=0.7`, ~70% of the interview would be off-domain software questions.
  This is almost certainly what drove the request ("can't have industry-level questions").
- **The only domain-relevant question source for arbitrary JDs today is `analyze_jd`**
  (LLM generation grounded in the JD text). The bank cannot scale to arbitrary industries.

### Root decision to resolve first
Keep the static bank in the loop for JD-driven interviews, or move to JD(+resume)-grounded
generation as the primary content source (bank → fallback only / no-JD software case)?
Everything else (the 70–80% number, the role dropdown's role, expanding the bank) hangs
off this.

### Open branches (to grill)
- Why the reversal vs 06-21 (resume-primary)? Deliberate? Driven by mechanical-JD testing?
- Is "JD dropdown" = the Role dropdown? (No other dropdown exists.)
- Generation quality: how to get *industry-level* depth from `analyze_jd` on a JD.
- Bank's future: fallback only, expand it, or retire it for voice.

### Checkpoint 2026-06-25 #2 — decisions resolved
1. **Reversal is deliberate** — client requirement centers on uploaded JDs. Today the
   Role *dropdown* stands in for "the job" + resume = candidate. Want a real JD upload
   to carry the unique job requirements. JD > resume > (old dropdown).
2. **JD upload becomes MANDATORY + primary** (70–80% of questioning). No JD → no voice
   interview. (Kills the no-JD/bank-capacity fallback path entirely.)
3. **Role dropdown removed/disabled** once JD is the input. Role *title* still derived
   from the JD for intro line + report header (assumed; user to veto if not wanted).
4. **Static question bank is being REMOVED** for the voice flow. `get_question_set`,
   `eligible_question_count`, the bank-core slots, and `compute_voice_split`'s core/JD
   split all go away. Technical questions = 100% JD-generated (+ resume personalization).

### Evaluation-path facts verified (drive the generation contract)
- `build_answer_evaluation_prompt` (prompt_builder.py:40-44) feeds question_text + topic +
  **difficulty + rubric** to Haiku for 0–10 scoring.
- JD questions today (`special_questions.build_jd_question`) are hardcoded
  `difficulty="medium"` + **generic domain-blind rubric** + no follow-ups. → With the
  bank gone, 100% of scoring rides a domain-blind rubric = weak on specialized topics.
  → Must enrich `analyze_jd` to emit per-question difficulty + a real key-points rubric.

### User's detailed expectations (2026-06-25) — the target behavior
- **Per-skill weighted budget + difficulty mix.** Ex: FE dev, 3 yrs, React/Tailwind/Next +
  AWS/Azure → 3 React (2 moderate, 1 hard) + 1 Azure + 1 Next. Primary skill gets more
  questions; difficulty calibrated to years of experience. (Read as an *example to infer*,
  not hardcoded numbers — TO CONFIRM.)
- **Domain-aware question type.** Azure≈deployment → "walk me through how you deployed a
  project." Generate question_type per question.
- **Per-question time budget** (~2.5 min). Soft target (hard cut-off rejected for voice) —
  TO CONFIRM hard vs soft.
- **Strict JD grounding** — questions AND follow-ups never deviate from JD competencies.
- **Mix direct/recall + applied** ("do you know anything about motors") — not all deep.
- **Experience-calibrated dynamic follow-ups** (1-yr vs 7-yr) + probe when answer thin
  (e.g. Azure deploy), capped, in-competency.
- **Bot persona** = senior recruiter, 20 yrs experience. Add to system prompt.

### Checkpoint 2026-06-25 #3 — decisions resolved
5. **Allocation auto-decided by the LLM** from JD (primary) + resume. 3/1/1 + Azure
   long-answer are *examples to infer*, never hardcoded. Difficulty driven by JD +
   experience, JD primary. No manual admin override in v1.
6. **N = 5 default, 5–10 supported** (slider can stay disabled now; backend honors range).
7. **Single COMBINED planning call** — JD + resume fed together into ONE LLM step that
   emits the personalized plan. Replaces the separate `analyze_jd` + `analyze_resume`
   merge. Reason: "what to ask given both" is a cross-referencing judgment (JD demands X,
   resume shows/lacks X → personalize + calibrate). Two isolated calls can't do it.
8. **Blend within N:** ~70–80% JD-competency questions + ~20–30% resume-grounded probes
   that stay JD-relevant. JD stays primary.
9. **Behavioral kept as-is; project deep-dive kept but made JD/resume-grounded.** Both
   remain additive (outside N).

### Checkpoint 2026-06-25 #4 — decisions resolved
10. **Blend = 80/20** (JD/resume), not 70/30. N=5 → 4 JD + 1 resume; N=10 → 8 + 2.
11. **Per-question `rubric_keypoints` = YES.** Planner emits 3–5 expected key points per
    question; evaluator scores the answer against them. Biggest lever on real scoring.
12. **Question-object schema** (planner output, per question):
    `{competency, source(jd|resume), question_text, question_type(recall|applied|
    scenario|troubleshooting), difficulty(easy|med|hard), rubric_keypoints[], time_budget_sec}`.
13. **Time budget = SOFT + spoken-only-on-deep-questions.** No hard timer/interrupt
    (hostile in voice + fights turn/silence handling). Shapes framing + when-to-advance.
14. **Follow-ups:** max **1 per question, 2 for `hard`**, CODE-capped via `follow_up_count`.
    LLM judges *whether* to probe within budget. Prompt gets `experience_level` + the
    MISSING rubric_keypoints + JD seniority → targeted, level-calibrated probes.
15. **Grounding guardrail (runtime):** follow-ups must stay within the current question's
    JD competency — never introduce a non-JD topic. Enforces "doesn't deviate from JD"
    on the improvised turns (main hallucination risk).
16. **CONFLICT RESOLVED — teaching vs "never give hints" (system_prompt.txt:6).**
    New policy: NO hints while candidate is still attempting (don't inflate the score);
    once they concede/"I don't know" or the probe is exhausted → drop assessment posture,
    warm "no worries" + BRIEF teach, then advance. Teaching turn ≠ follow-up turn.
17. **Scoring integrity:** score reflects the CANDIDATE's answer only; "I don't know" →
    low competency score, THEN teach. Bot's explanation never leaks into `score_update`.
    Evaluator scores before composing teaching text. (Rule 7.)
18. **Teaching accuracy guardrail:** when teaching, frame the explanation around that
    question's pre-generated `rubric_keypoints` (2–3 sentences), not free-style — bounds
    hallucination on specialized domains (GD&T, ISO 13485) where a wrong "lesson" is worse
    than none.
19. **Teaching is SELECTIVE + seniority-calibrated**, not every miss (it's a job interview,
    not a tutorial). Junior → explain more when stuck; senior/senior-sales-lead → usually
    acknowledge & move on (lecturing a senior is itself condescending); ALWAYS warm, never
    rude. LLM judgment + prompt guidance, NO hard code cap in v1 (revisit if it over-teaches).

---

## 2026-06-26 — FINAL DESIGN TREE (stands alone as the spec)

**Feature:** Hyper-personalized, JD-driven voice interview. The uploaded JD is the
mandatory, primary source of questioning (~80%); resume personalizes; the static
question bank is removed. Supersedes the 2026-06-21 "resume-primary, bank-70%" design
for the voice flow (deliberate — client requirement centers on uploaded JDs).
Scope: **voice flow only.** The text/admin-config flow (`build_plan` + bank) is
UNCHANGED and out of scope.

### A. Inputs & access
- **JD upload = MANDATORY + primary.** No JD → no voice interview. (Backend already
  accepts an optional `jd` UploadFile at `voice_api.py:145`; make it required + wire the
  frontend to send it.)
- **Resume = optional**, personalizes.
- **Role dropdown removed/disabled.** Role *title* derived from the JD (intro + report).
- Admin-only (existing `require_admin` / `X-Admin-Key`), unchanged.

### B. Question generation — ONE combined planner (replaces analyze_jd + analyze_resume)
- **Single LLM call** takes JD + resume together → emits the full personalized plan.
  Cross-references JD demands vs resume evidence (personalize / calibrate / flag gaps).
- **LLM auto-decides allocation** (which competencies, count, difficulty, type) grounded
  in JD (primary) + resume. NO hardcoded ratios (3/1/1 was only an example). Difficulty
  driven by JD seniority × `experience_level`. **Code enforces totals + easy-first order**
  (Rule 5 split: LLM judges, code counts).
- **Blend within N = 80/20** (JD / resume-grounded-but-JD-relevant). N=5 → 4+1; N=8 → 6+2.
- **N = 5 default, 5–8 selectable** (slider enabled).
- **Per-question object:** `{competency, source(jd|resume), question_text,
  question_type(recall|applied|scenario|troubleshooting), difficulty(easy|med|hard),
  rubric_keypoints[3–5], time_budget_sec}`.
- **Strict JD grounding** — never invent off-JD topics.
- **Additive (outside N):** behavioral kept as-is; project deep-dive kept but made
  JD/resume-grounded. (Carry the existing PII guard into the combined planner.)

### C. Evaluation
- Score each answer against its **per-question `rubric_keypoints`** (replaces the generic
  domain-blind rubric) — the main lever for industry-level scoring.
- **Scoring integrity:** score = candidate's answer ONLY; computed before any teaching
  text; teaching never leaks into `score_update`. (Rule 7 / Rule 9.)

### D. Follow-ups & teaching
- **Cap: 1 per question, 2 for `hard`** — CODE-capped via `follow_up_count`; LLM judges
  whether to probe within budget.
- **Calibration:** follow-up prompt gets `experience_level` + the MISSING key points +
  JD seniority → targeted, level-appropriate probes.
- **Grounding guardrail:** follow-ups stay within the current question's JD competency.
- **Teaching policy (resolves conflict w/ system_prompt.txt:6 "never give hints"):**
  NO hints while the candidate is still attempting; on concession/exhaustion → drop
  assessment posture, warm "no worries" + BRIEF teach → advance. Teaching ≠ follow-up.
- **Teaching is selective + seniority-calibrated:** junior → explain more when stuck;
  senior / senior-sales-lead → usually acknowledge & move on (no lecture); ALWAYS warm,
  never rude. LLM judgment, no hard cap v1.
- **Teaching accuracy:** frame the explanation around that question's pre-generated
  `rubric_keypoints` (2–3 sentences), not free-style — bounds hallucination on GD&T /
  ISO 13485 etc.

### E. Time budget
- **Soft, spoken only on deep questions.** No hard timer/interrupt (hostile in voice;
  fights turn/silence handling). Shapes question framing + when-to-advance.

### F. Persona
- Add to **`system_prompt.txt`** (one file → covers BOTH voice `voice_llm_orchestrator.py:218`
  and text `llm_service.py:33`). Warm senior recruiter, 20 yrs, never condescend/rude,
  calibrate to experience, stay strictly within the JD. Replaces the old "never give
  away answers/hints" line (now the teaching policy).

### G. Thin-JD floor (warn-and-shrink, never hard-reject)
- Generate only **grounded** questions; cap the count to distinct competencies found;
  resume tops up if present.
- If requested N can't be met → **blocking warning** ("This JD supports only 5 strong
  questions — Proceed with 5 / Upload a richer JD"), floor = **5**.
- Only hard failure = degenerate JD (unreadable file / zero competencies) → existing
  `JDAnalysisError`.
- **Acceptance bar (eval target):** the 4 shared JDs are the gold standard → must
  reliably yield **6–8 grounded questions**.

### H. Admin setup UX
- **Blocking confirm** when requested N is unachievable.
- **Read-only plan preview** before the interview (question + competency + difficulty).
- **"Regenerate" button** (fresh full plan). **NO inline editing in v1** — editing question
  text desyncs it from its `rubric_keypoints` → silent mis-scoring (Rule 9). Deferred;
  if ever added, an edit must regenerate that question's rubric.

### Anticipated implementation surface
- `routes/voice_api.py` — make `jd` required; drop no-JD/bank-capacity path; call combined
  planner; build plan; blocking-warning + preview/regenerate support.
- NEW combined planner module + prompt (replaces `jd_analysis.py` + `resume_analysis.py`
  usage in voice); enriched output schema. Keep them for the text/admin flow.
- `services/interview/special_questions.py` — stop hardcoding `difficulty="medium"` +
  generic rubric for JD/resume questions; carry generated fields.
- `services/interview/plan_builder.py` (`build_voice_plan`) + `plan_math.py`
  (`compute_voice_split`) — rewrite for 80/20, no bank, cap-to-grounded, floor 5.
- Remove bank from voice: `get_question_set`, `eligible_question_count`, no-JD cap.
- `prompts/system_prompt.txt` — persona + teaching policy + JD grounding + tone.
- `prompt_builder.build_answer_evaluation_prompt` — feed key points; follow-up calibration
  (experience + missing key points); teaching + scoring-integrity instructions.
- `voice_llm_orchestrator` / `voice_turn_processor` — soft time-budget framing; selective
  teaching; warm tone; follow-up cap wiring.
- `frontend/.../interview/voice/start/page.tsx` + `services/voice-api.ts` — mandatory JD
  field, optional resume, enable N slider 5–8, role-from-JD, plan preview + regenerate +
  blocking warning.
- Tests across all (Rule 7 intent-encoding: grounding, scoring integrity, follow-up cap,
  thin-JD floor, teaching-doesn't-leak-into-score).

### Flagged risks / to validate (NOT reopened — for the build phase)
- **Model choice for the planner.** CLAUDE.md routes all tasks to Haiku. Generating
  *industry-level* questions + accurate `rubric_keypoints` in specialized domains (GD&T,
  medical-device QMS) is the product's core quality. Strongly recommend evaluating a more
  capable model **for the planning call specifically** (eval can stay Haiku) against the
  4 gold-standard JDs before committing to Haiku.
- **Interview length.** N=8 → 6 JD + 2 resume + behavioral + project + follow-ups = 10+
  turns of voice. Watch total duration.
- **Schema reliability.** The richer combined-planner JSON must parse robustly and fail
  loud (mirror the existing `JDAnalysisError` pattern).

### Deferred (NOT in v1)
- Inline admin editing of questions; manual per-skill allocation override.
- N > 8. Any change to the text/admin-config flow. Expanding `questions.json`.

---

## 2026-06-28 — NEW SESSION: Semantic turn-taking + free-form cross-questioning

> NOTE: snapshot up to Checkpoint 1 only. Canonical, up-to-date checkpoints for this
> session live in **GRILL_NOTES_2.md** (per user request). Refer there, not here.

New request: the two highest-value voice-experience behaviours, on the EXISTING cascade
(Deepgram STT → Claude Haiku → ElevenLabs TTS). Reference repo (code100x/ai-interviewer)
feels smoother only because it offloads the whole conversation to OpenAI gpt-realtime
(speech-to-speech). We are deliberately NOT going realtime.
1. **Semantic turn-taking** — decide the candidate is done from prosody *and* content,
   not a fixed timer. (Fixes: bot interrupts mid-answer; "are you still there?" too soon.)
2. **Free-form cross-questioning** — bot answers clarifications, reacts, adapts; not a
   rigid scripted FSM. (Today `_acknowledgment_only` strips any question the model asks
   and force-appends the next scripted question.)

### Checkpoint 1 (4 decisions resolved)
1. **Architecture (Q1): stay on the cascade**, engineer both. Not realtime. Keeps Claude,
   JD planning, structured eval, provider independence. Cross-questioning doesn't need
   realtime; turn-taking can be made good with an EOT model.
2. **Cross-questioning control (Q2): B′ — LLM drives the loop, code clamps invariants**
   ("LLM proposes, code disposes"). User REMOVED CLAUDE.md Rule 5 ("code must route") to
   enable this — confirmed via git diff.
3. **Control contract (Q3): shipped as-is.** 6 actions: `answer_clarification`, `follow_up`,
   `accept_thinking`, `redirect`, `acknowledge_advance`, `wrap_up`. A question spans
   multiple turns; **scoring fires once, at `acknowledge_advance`**. 5 clamps: (1) coverage
   (no advance past N planned Qs); (2) no question leaves unscored; (3) follow-up cap
   (existing, keep); (4) no-skip (index +1 per finalization); (5) loop guard (max 3
   consecutive non-advancing turns/question → force advance).
4. **Turn-taking content signal (Q4): (i) dedicated EOT model**, local ONNX in a worker
   (LiveKit-style), like the existing Silero VAD. NOT Haiku-per-pause (latency/cost), NOT
   Deepgram-only (not semantic). Embeddings (incl. OpenAI hosted) rejected: tuned for
   topical similarity, blur the trailing-token cue EOT needs; hosted = network call/pause.

### Open branches (recommended default)
- **EOT model sourcing (Q5 next)** — off-the-shelf open turn-detector vs train a custom
  head. Default: off-the-shelf open model first.
- **EOT placement** — backend on Deepgram interim/`utterance_end`, replacing the fixed
  debounce ladder in `voice_ws.py`. Default: backend, triggered on pause.
- **Silence-ladder fix (complaint #3)** — new turn model changes 8/18/30s nags in
  `voice_turn_processor.py`. Default: push thresholds way out + drop "running into issues".
- **Scoring decoupling + final-eval calibration** — `running_scores` never passed into the
  final eval prompt (`voice_evaluation.py:_build_prompt`). Default: feed it in; soften bands.
- **Sequencing** — Default: build B′ (pure software, no infra) first, then the EOT model.
- **Barge-in interplay** with the new turn model.

### Next questions
- Q5: EOT model sourcing (off-the-shelf vs train; training-data implications).
- Q6: EOT placement / how it replaces the debounce ladder.
- Then sequencing + the silence-ladder & scoring fixes (agreed in principle).
