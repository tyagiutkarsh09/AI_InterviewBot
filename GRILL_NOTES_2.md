## 2026-06-28 — NEW SESSION: Semantic turn-taking + free-form cross-questioning

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