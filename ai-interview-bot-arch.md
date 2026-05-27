# AI Interview Bot — Production Architecture Specification
**Version 1.0 | Staff Engineer Grade | MVP Target: 14 Days**

---

## TABLE OF CONTENTS
1. [High Level Architecture](#1-high-level-architecture)
2. [Voice Pipeline Design](#2-voice-pipeline-design)
3. [LLM Orchestration](#3-llm-orchestration)
4. [API Requirements & Cost Analysis](#4-api-requirements--cost-analysis)
5. [Database Design](#5-database-design)
6. [Security & Compliance](#6-security--compliance)
7. [Observability](#7-observability)
8. [Deployment Strategy](#8-deployment-strategy)
9. [Development Roadmap](#9-development-roadmap)
10. [Final Stack Recommendation](#10-final-stack-recommendation)

---

## 1. HIGH LEVEL ARCHITECTURE

### 1.1 System Overview

The AI Interview Bot is a real-time, voice-first system that conducts structured technical interviews, evaluates candidates, and persists session data for human review. It is composed of five primary planes:

- **Client Plane** — Browser (Next.js), handles microphone capture, WebRTC/WebSocket management, and UI rendering
- **Edge Plane** — API Gateway + WebSocket server, handles auth, routing, rate-limiting
- **Orchestration Plane** — FastAPI interview engine, session state machine, LLM coordination
- **Media Plane** — STT (Deepgram), TTS (ElevenLabs), audio pipeline
- **Data Plane** — PostgreSQL, Redis, S3, analytics

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              CLIENT (Browser / Next.js)                          │
│  ┌────────────┐  ┌────────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │ Microphone │  │ WebSocket Mgr  │  │  Audio Player│  │ Interview UI / State │ │
│  │ Capture    │  │ (reconnect/    │  │  (TTS stream)│  │ (questions, timer,   │ │
│  │ (PCM 16kHz)│  │  heartbeat)    │  │              │  │  transcript, score)  │ │
│  └─────┬──────┘  └───────┬────────┘  └──────┬───────┘  └──────────────────────┘ │
└────────┼─────────────────┼──────────────────┼─────────────────────────────────────┘
         │ PCM audio chunks │ Control msgs     │ TTS audio
         │ (binary frames)  │ (JSON)           │ (streamed MP3)
         ▼                  ▼                  ▲
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            EDGE / API GATEWAY (AWS ALB)                          │
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │  WebSocket Endpoint /ws/interview/{session_id}   REST /api/v1/*             ││
│  │  • JWT validation on upgrade                     • Rate limiting (Redis)    ││
│  │  • Session routing (by session_id → pod)         • Request logging          ││
│  │  • Heartbeat / ping-pong                         • CORS / auth middleware   ││
│  └──────────────────────────────┬──────────────────────────────────────────────┘│
└─────────────────────────────────┼───────────────────────────────────────────────┘
                                  │
         ┌────────────────────────┼──────────────────────────┐
         │                        │                           │
         ▼                        ▼                           ▼
┌─────────────────┐   ┌───────────────────────┐   ┌────────────────────┐
│  AUTH SERVICE   │   │  INTERVIEW ENGINE      │   │  ADMIN REST API    │
│  (FastAPI)      │   │  (FastAPI + asyncio)   │   │  (FastAPI)         │
│                 │   │                        │   │                    │
│  • JWT issue    │   │  ┌──────────────────┐  │   │  • Session review  │
│  • Refresh      │   │  │ Session FSM       │  │   │  • Score override  │
│  • Revocation   │   │  │ (state machine)   │  │   │  • Question bank   │
│  • OAuth2       │   │  └───────┬──────────┘  │   │  • Analytics       │
└─────────────────┘   │          │              │   └────────────────────┘
                       │  ┌──────▼──────────┐  │
                       │  │  Audio Pipeline  │  │
                       │  │                 │  │
                       │  │  PCM → Deepgram │  │
                       │  │  STT → Partial  │  │
                       │  │  transcripts    │  │
                       │  └──────┬──────────┘  │
                       │         │              │
                       │  ┌──────▼──────────┐  │
                       │  │  LLM Orchestr.  │  │
                       │  │                 │  │
                       │  │  Claude Sonnet  │  │
                       │  │  Prompt engine  │  │
                       │  │  Tool calls     │  │
                       │  └──────┬──────────┘  │
                       │         │              │
                       │  ┌──────▼──────────┐  │
                       │  │  TTS Pipeline   │  │
                       │  │                 │  │
                       │  │  ElevenLabs /   │  │
                       │  │  OpenAI TTS     │  │
                       │  │  Streaming MP3  │  │
                       │  └─────────────────┘  │
                       └───────────┬────────────┘
                                   │
              ┌────────────────────┼──────────────────────┐
              │                    │                        │
              ▼                    ▼                        ▼
┌─────────────────┐   ┌─────────────────────┐  ┌──────────────────────┐
│  POSTGRESQL     │   │  REDIS              │  │  AWS S3              │
│                 │   │                     │  │                      │
│  • users        │   │  • Session state    │  │  • Audio recordings  │
│  • sessions     │   │  • Rate limit ctr   │  │  • Transcripts       │
│  • transcripts  │   │  • WS room mgmt     │  │  • Evaluation PDFs   │
│  • evaluations  │   │  • LLM cache        │  │  • Encrypted at rest │
│  • questions    │   │  • JWT blacklist    │  │                      │
│  • analytics    │   │  • Pub/Sub bus      │  │                      │
└─────────────────┘   └─────────────────────┘  └──────────────────────┘
```

### 1.2 Event Flow — Single Interview Turn

```
CANDIDATE speaks
     │
     ▼ PCM audio (binary WS frame, 20ms chunks)
[BROWSER VAD] ─── silence detected ──► stop sending
     │
     ▼ audio stream
[DEEPGRAM STT] ─── partial transcript events ──► Interview Engine
     │                (is_final=false, latency ~100ms)
     │
     ▼ final transcript
[INTERVIEW ENGINE]
     ├── Update Redis session state
     ├── Check anti-cheat signals
     ├── Append to transcript buffer
     ├── Call LLM with full context
     │       │
     │       ▼ streaming tokens
     │   [CLAUDE API] ─── SSE stream ──► Interview Engine
     │       │              (first token ~300ms)
     │       ▼
     │   [RESPONSE PARSER] ─── detect response type ──► question / follow-up / score
     │
     ▼ text response
[TTS ENGINE]
     ├── ElevenLabs streaming endpoint
     ├── MP3 chunks pushed to client via WS binary frames
     └── First audio chunk ~400ms after LLM response start

CANDIDATE hears response
     │
     ▼ client plays audio
[BROWSER] ─── sends "tts_complete" event ──► Interview Engine
     │
     ▼ next turn begins
```

### 1.3 Service Boundaries

| Service | Responsibility | Language | Scale Unit |
|---|---|---|---|
| Interview Engine | Core orchestration, STT/TTS routing, LLM calls | Python / FastAPI | Per-session pods |
| Auth Service | JWT issue/validate, OAuth, user mgmt | Python / FastAPI | Stateless, horizontal |
| Admin API | Human review, analytics, question bank CRUD | Python / FastAPI | Low traffic |
| Transcript Worker | Async transcript finalization, S3 upload | Python / Celery | Queue-based |
| Scoring Worker | Post-session evaluation, PDF generation | Python / Celery | Queue-based |
| Notification Service | Email/webhook post-interview | Node.js | Event-driven |

### 1.4 Queue / Event Bus Strategy

Use **Redis Pub/Sub + Redis Streams** for MVP (not Kafka — overengineered at this stage).

```
Redis Streams (durable):
  interview-events     → session_start, turn_complete, session_end
  transcript-events    → chunk_received, utterance_final
  scoring-events       → evaluation_requested, evaluation_complete
  audit-events         → auth_attempt, session_action

Redis Pub/Sub (ephemeral, in-process):
  session:{id}:control → barge_in, pause, resume, terminate
  session:{id}:audio   → tts_start, tts_chunk, tts_complete
```

Celery workers consume from Streams. Migrate to SQS/SNS at 1000+ concurrent sessions.

### 1.5 Session Management Strategy

Each interview session is a **state machine** persisted in Redis with a TTL. On reconnect, full state is rehydrated. The state machine lives in the Interview Engine as an async coroutine.

```
Session States:
  INITIALIZING → INTRODUCTION → QUESTIONING → [FOLLOW_UP ↔ QUESTIONING] → CODING_CHALLENGE → WRAP_UP → SCORED → COMPLETE

State payload (Redis Hash, key: session:{id}):
  {
    "state": "QUESTIONING",
    "candidate_id": "uuid",
    "job_role": "senior_backend",
    "current_question_idx": 2,
    "questions": [...],           // ordered question IDs
    "transcript": [...],          // all turns
    "turn_count": 6,
    "silence_strikes": 0,
    "barge_in_count": 0,
    "llm_context_tokens": 3420,
    "audio_recording_path": "s3://...",
    "started_at": "2024-01-15T10:00:00Z",
    "last_activity": "2024-01-15T10:12:34Z"
  }
```

**Session TTL**: 4 hours in Redis. Postgres persists permanently.

### 1.6 Failure Handling

| Failure | Detection | Recovery |
|---|---|---|
| Deepgram drops | WS disconnect event | Auto-reconnect (exponential backoff, max 3x), fallback to Whisper API |
| LLM timeout | asyncio timeout (10s) | Retry once with truncated context, then send scripted "connection issue" response |
| TTS failure | HTTP 5xx from ElevenLabs | Fallback to OpenAI TTS, then Google TTS |
| Client disconnect | WS close frame / ping timeout | Session paused in Redis, client can resume within 10min |
| DB write failure | Exception caught | Write to Redis first (source of truth), async reconcile to Postgres via worker |
| Session pod crash | Kubernetes liveness probe | Session reloaded from Redis on new pod, 15-30s recovery |

---

## 2. VOICE PIPELINE DESIGN

### 2.1 Microphone Input Flow

```
Browser getUserMedia()
  → AudioContext (sample rate: 48kHz)
  → AudioWorkletProcessor (resample to 16kHz PCM, mono)
  → ScriptProcessorNode (collect 20ms chunks = 320 samples)
  → Browser VAD (Silero VAD via ONNX in worker)
  → WebSocket send (binary frame, Int16Array)
```

Critical config:
```javascript
// client/lib/audio-capture.ts
const SAMPLE_RATE = 16000;       // Deepgram requires 16kHz
const CHUNK_MS = 20;             // 20ms chunks = good latency/overhead tradeoff
const CHUNK_SAMPLES = 320;       // 16000 * 0.02
const VAD_THRESHOLD = 0.5;       // Silero VAD confidence threshold
const SILENCE_TIMEOUT_MS = 800;  // 800ms silence = end of utterance signal
```

Do NOT send 44.1kHz or 48kHz audio. Resample client-side. This avoids server-side FFmpeg and saves ~50ms latency.

### 2.2 VAD — Voice Activity Detection

Run **Silero VAD** (ONNX Runtime Web) in a Web Worker — never on the main thread. Do not use the browser's built-in `onnxruntime-web` naively; bundle it into the worker separately.

```javascript
// client/workers/vad.worker.ts
import * as ort from 'onnxruntime-web';

const VAD_STATES = { SILENT: 0, SPEECH: 1, TRAILING: 2 };
let state = VAD_STATES.SILENT;
let silenceFrames = 0;
const SILENCE_FRAMES_THRESHOLD = 40; // 40 * 20ms = 800ms

self.onmessage = async ({ data: { pcm } }) => {
  const prob = await runSileroVAD(pcm);
  
  if (prob > 0.5 && state === VAD_STATES.SILENT) {
    state = VAD_STATES.SPEECH;
    self.postMessage({ event: 'speech_start' });
  }
  
  if (prob < 0.3 && state === VAD_STATES.SPEECH) {
    state = VAD_STATES.TRAILING;
    silenceFrames = 0;
  }
  
  if (state === VAD_STATES.TRAILING) {
    silenceFrames++;
    if (silenceFrames >= SILENCE_FRAMES_THRESHOLD) {
      state = VAD_STATES.SILENT;
      self.postMessage({ event: 'speech_end' }); // triggers STT finalize
    }
  }
  
  if (state !== VAD_STATES.SILENT) {
    self.postMessage({ event: 'audio_chunk', pcm });
  }
};
```

Only send audio to WebSocket when VAD state is SPEECH or TRAILING. This reduces bandwidth by ~60% and eliminates STT noise.

### 2.3 Streaming STT with Deepgram

Connect Deepgram via server-side WebSocket proxy — never expose your API key to the browser. The Interview Engine maintains a persistent Deepgram connection per session.

```python
# services/audio/deepgram_client.py
import asyncio
from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions

class DeepgramSTTStream:
    def __init__(self, session_id: str, on_transcript: Callable):
        self.session_id = session_id
        self.on_transcript = on_transcript
        self.dg_connection = None
        
    async def connect(self):
        client = DeepgramClient(settings.DEEPGRAM_API_KEY)
        self.dg_connection = client.listen.asynclive.v("1")
        
        options = LiveOptions(
            model="nova-2",             # Best accuracy/latency tradeoff
            language="en-US",
            smart_format=True,          # Punctuation, numbers, etc.
            interim_results=True,       # Partials for responsive UI
            endpointing=300,            # ms of silence = end of utterance
            utterance_end_ms=1000,      # Flush after 1s silence
            vad_events=True,            # Deepgram-side VAD as backup
            encoding="linear16",
            sample_rate=16000,
            channels=1,
        )
        
        self.dg_connection.on(LiveTranscriptionEvents.Transcript, self._on_transcript)
        self.dg_connection.on(LiveTranscriptionEvents.Error, self._on_error)
        self.dg_connection.on(LiveTranscriptionEvents.Close, self._on_close)
        
        await self.dg_connection.start(options)
    
    async def send_audio(self, pcm_bytes: bytes):
        if self.dg_connection:
            await self.dg_connection.send(pcm_bytes)
    
    async def _on_transcript(self, result, **kwargs):
        transcript = result.channel.alternatives[0].transcript
        is_final = result.is_final
        speech_final = result.speech_final  # True when utterance is complete
        
        if not transcript:
            return
            
        if speech_final:
            await self.on_transcript(transcript, is_final=True)
        elif is_final and transcript:
            # Partial but committed — update UI
            await self.on_transcript(transcript, is_final=False)
```

**Key Deepgram settings:**
- `nova-2`: 14% better WER than nova-1, faster than whisper-large
- `interim_results=True`: partial transcripts for live UI display
- `endpointing=300`: aggressive endpoint detection to reduce latency
- `smart_format=True`: converts "three hundred fifty" → "350" automatically

### 2.4 Interruption Handling (Barge-in)

When the candidate starts speaking while the bot is still talking, the bot must stop. This is "barge-in" and is critical for natural conversation.

```python
# services/interview/turn_manager.py
class TurnManager:
    def __init__(self, session_id: str, ws_connection):
        self.session_id = session_id
        self.ws = ws_connection
        self.bot_speaking = False
        self.current_tts_task: asyncio.Task | None = None
        
    async def handle_speech_detected(self):
        """Called when VAD detects speech start during bot turn."""
        if self.bot_speaking:
            await self._interrupt_bot()
    
    async def _interrupt_bot(self):
        # 1. Cancel TTS stream immediately
        if self.current_tts_task:
            self.current_tts_task.cancel()
        
        # 2. Signal client to stop playing audio
        await self.ws.send_json({
            "event": "barge_in",
            "action": "stop_tts"
        })
        
        # 3. Update session state
        self.bot_speaking = False
        
        # 4. Track barge-in count (anti-cheat + UX signal)
        await redis.hincrby(f"session:{self.session_id}", "barge_in_count", 1)
        
        # 5. Open microphone for candidate
        await self.ws.send_json({"event": "turn", "speaker": "candidate"})
```

Client-side, on receiving `stop_tts`, immediately pause and discard the audio buffer. Do not drain it.

### 2.5 Partial Transcript Handling

Partial transcripts update the live UI but are NOT sent to the LLM. Only `speech_final=True` transcripts trigger LLM processing.

```
Flow:
  interim transcript → WebSocket → Client (update live display)
  final transcript   → WebSocket → Client (commit to transcript)
  speech_final=True  → trigger_llm_processing()
```

Deduplication: track the last committed transcript. If the new final transcript is a prefix of or identical to the previous, discard it.

### 2.6 Latency Optimization Strategy

Target: **< 1.5 seconds** from candidate stops speaking to bot starts responding.

| Stage | Target | Technique |
|---|---|---|
| VAD end detection | 0–800ms | Tune SILENCE_TIMEOUT. 800ms is good for technical interviews (allow thinking pauses) |
| Deepgram STT final | ~100ms | `endpointing=300`, `nova-2` model |
| LLM first token | ~300ms | Claude Haiku for follow-ups, Sonnet for main questions. Pre-warm with system prompt |
| TTS first chunk | ~200ms | ElevenLabs streaming, request as soon as first ~50 LLM tokens are available |
| Network (WS) | ~30ms | Same-region deployment (us-east-1) |
| **Total** | **~1.4s** | Parallel: TTS request starts during LLM streaming |

**Critical optimization**: Start TTS before LLM is done. Accumulate ~50-80 tokens, then send first sentence to TTS. Stream subsequent sentences. This creates a pipeline where TTS is ~0.5s behind LLM generation.

```python
async def stream_llm_to_tts(self, llm_stream, ws):
    sentence_buffer = ""
    tts_tasks = []
    
    async for chunk in llm_stream:
        sentence_buffer += chunk.text
        
        # Detect sentence boundary
        if any(sentence_buffer.rstrip().endswith(p) for p in ['.', '?', '!']):
            sentence = sentence_buffer.strip()
            sentence_buffer = ""
            
            # Fire-and-forget TTS for this sentence
            task = asyncio.create_task(
                self.tts.stream_sentence(sentence, ws)
            )
            tts_tasks.append(task)
    
    # Handle any remaining text
    if sentence_buffer.strip():
        tts_tasks.append(asyncio.create_task(
            self.tts.stream_sentence(sentence_buffer.strip(), ws)
        ))
    
    # Wait for all TTS tasks (they'll play sequentially via client queue)
    await asyncio.gather(*tts_tasks)
```

### 2.7 TTS Streaming

Use **ElevenLabs Streaming** as primary, **OpenAI TTS** as fallback.

```python
# services/audio/tts_client.py
import httpx

class ElevenLabsTTS:
    VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel — neutral, professional
    
    async def stream_sentence(self, text: str, ws) -> None:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.VOICE_ID}/stream"
        
        payload = {
            "text": text,
            "model_id": "eleven_turbo_v2",   # Lowest latency model
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "speed": 1.1,               # Slightly faster for interview pace
            },
            "output_format": "mp3_44100_64", # 64kbps = good quality, small size
        }
        
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST", url,
                headers={"xi-api-key": settings.ELEVENLABS_API_KEY},
                json=payload,
                timeout=30.0,
            ) as response:
                async for chunk in response.aiter_bytes(chunk_size=4096):
                    await ws.send_bytes(chunk)
        
        # Signal sentence complete so client can sequence sentences
        await ws.send_json({"event": "tts_sentence_complete"})
```

### 2.8 Conversation Turn Management

Strict turn management prevents overlapping audio and confusion.

```
Turn States:
  BOT_SPEAKING → (candidate speaks) → BARGE_IN_DETECTED → CANDIDATE_SPEAKING
  BOT_SPEAKING → (TTS complete) → WAITING_FOR_CANDIDATE
  WAITING_FOR_CANDIDATE → (VAD speech_start) → CANDIDATE_SPEAKING
  CANDIDATE_SPEAKING → (VAD speech_end) → PROCESSING
  PROCESSING → (LLM + TTS ready) → BOT_SPEAKING

Timeouts:
  WAITING_FOR_CANDIDATE: 15s → prompt ("Take your time, I'm listening")
  WAITING_FOR_CANDIDATE: 30s → "Are you still there?"
  WAITING_FOR_CANDIDATE: 45s → Mark silence_strike, move to next question
  PROCESSING: 10s → Timeout, send "I'm having a moment, please repeat"
```

---

## 3. LLM ORCHESTRATION

### 3.1 Interview State Machine

The interview progresses through defined states. The LLM always receives the current state as context and must respond within that state's constraints.

```python
# services/interview/state_machine.py
from enum import Enum
from dataclasses import dataclass

class InterviewState(Enum):
    INTRO = "intro"
    WARM_UP = "warm_up"
    TECHNICAL_Q = "technical_question"
    FOLLOW_UP = "follow_up"
    CODING_CHALLENGE = "coding_challenge"
    BEHAVIORAL = "behavioral"
    WRAP_UP = "wrap_up"
    SCORING = "scoring"

@dataclass
class QuestionContext:
    question_id: str
    question_text: str
    topic: str
    difficulty: str
    follow_up_count: int = 0
    max_follow_ups: int = 3
    score: float | None = None
    rubric: dict = None

@dataclass
class SessionContext:
    session_id: str
    candidate_name: str
    job_role: str
    required_skills: list[str]
    experience_level: str  # junior / mid / senior / staff
    state: InterviewState
    questions: list[QuestionContext]
    current_q_idx: int
    transcript: list[dict]      # [{speaker, text, timestamp}]
    running_scores: dict        # {topic: score}
    flags: list[str]            # anti-cheat, performance flags
    token_count: int            # track LLM context usage
```

### 3.2 Prompt Architecture

Use a **layered prompt system** with strict XML tags for deterministic parsing.

```
SYSTEM PROMPT (static, cached)
├── Role definition
├── Interview framework
├── Scoring rubric
├── Behavioral rules (don't let candidate steer, don't give answers)
└── Output format specification

CONTEXT BLOCK (dynamic, rebuilt each turn)
├── <candidate_info> — name, role, level
├── <interview_progress> — question index, state, time elapsed
├── <current_question> — full question + rubric
├── <conversation_history> — last N turns (token-limited)
├── <running_assessment> — scores so far
└── <turn_instruction> — what to do this turn

USER MESSAGE (candidate's response, current turn only)
```

```python
# services/llm/prompt_builder.py

SYSTEM_PROMPT = """
You are an expert technical interviewer conducting a Round 1 screening interview.

## YOUR ROLE
- You are fair, professional, and encouraging
- You ask ONE question at a time
- You listen carefully and ask targeted follow-ups
- You never give away answers or provide hints
- You keep track of interview progress and stay on schedule
- Total interview duration: 30 minutes

## RESPONSE FORMAT
Always respond with a valid XML block:

<interviewer_response>
  <action>ask_question | follow_up | acknowledge | transition | wrap_up</action>
  <spoken_text>What the candidate will hear (natural speech, no markdown)</spoken_text>
  <internal_notes>Your assessment of the last answer (not spoken)</internal_notes>
  <score_update>
    <topic>{topic_name}</topic>
    <score>{0-10}</score>
    <reasoning>{brief}</reasoning>
  </score_update>
  <next_state>{current_state}</next_state>
  <flags>{any anti-cheat observations, comma separated}</flags>
</interviewer_response>

## SCORING RUBRIC
Score each technical answer 0–10:
- 0-3: Does not understand the concept
- 4-5: Partial understanding, significant gaps
- 6-7: Good understanding, minor gaps
- 8-9: Strong understanding, practical knowledge
- 10: Expert-level, depth and breadth

## INTERVIEW RULES
1. If candidate is silent > 10 seconds, prompt them gently
2. If answer is very short, ask for elaboration once
3. If candidate asks to skip, note it and comply
4. If candidate's answer includes suspicious accuracy (e.g., perfect textbook answers with unusual phrasing), flag in internal_notes
5. Stay in the current topic until the rubric criteria are met or max_follow_ups reached
"""

def build_context_block(ctx: SessionContext) -> str:
    recent_transcript = ctx.transcript[-10:]  # Last 10 turns
    
    return f"""
<candidate_info>
  Name: {ctx.candidate_name}
  Role: {ctx.job_role}
  Level: {ctx.experience_level}
  Required skills: {', '.join(ctx.required_skills)}
</candidate_info>

<interview_progress>
  Current question: {ctx.current_q_idx + 1} of {len(ctx.questions)}
  State: {ctx.state.value}
  Follow-ups used: {ctx.questions[ctx.current_q_idx].follow_up_count}
  Max follow-ups: {ctx.questions[ctx.current_q_idx].max_follow_ups}
</interview_progress>

<current_question>
  Question: {ctx.questions[ctx.current_q_idx].question_text}
  Topic: {ctx.questions[ctx.current_q_idx].topic}
  Difficulty: {ctx.questions[ctx.current_q_idx].difficulty}
  Rubric: {json.dumps(ctx.questions[ctx.current_q_idx].rubric)}
</current_question>

<conversation_history>
{format_transcript(recent_transcript)}
</conversation_history>

<running_assessment>
{json.dumps(ctx.running_scores, indent=2)}
</running_assessment>
"""
```

### 3.3 Memory Strategy

Use a **sliding window + compression** approach. Never send the full 30-minute transcript to the LLM.

```
Token Budget: 8,000 tokens per turn (Claude Sonnet 3.5)
  - System prompt: ~800 tokens
  - Context block: ~1,200 tokens
  - Conversation history: ~3,000 tokens (last 10 turns)
  - Current candidate response: ~500 tokens
  - Buffer for response: ~2,500 tokens

Memory Tiers:
  Tier 1 — Active: Last 10 turns (full verbatim, 3k tokens)
  Tier 2 — Compressed: Earlier turns (1-sentence summaries, ~600 tokens)
  Tier 3 — Structured: Running scores + flags (always included, ~200 tokens)
```

```python
# services/llm/memory_manager.py
class InterviewMemoryManager:
    ACTIVE_TURNS = 10
    
    async def build_history(self, transcript: list[dict]) -> str:
        if len(transcript) <= self.ACTIVE_TURNS:
            return format_transcript(transcript)
        
        # Compress older turns
        older = transcript[:-self.ACTIVE_TURNS]
        recent = transcript[-self.ACTIVE_TURNS:]
        
        # Compress older turns with lightweight LLM call (haiku)
        summary = await self._compress_turns(older)
        
        return f"""
<historical_summary>
{summary}
</historical_summary>

<recent_conversation>
{format_transcript(recent)}
</recent_conversation>
"""
    
    async def _compress_turns(self, turns: list[dict]) -> str:
        # Use Claude Haiku for cheap compression
        response = await anthropic.messages.create(
            model="claude-haiku-20240307",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": f"Summarize these interview turns in 3-5 sentences, focusing on what topics were covered and the candidate's performance level:\n\n{format_transcript(turns)}"
            }]
        )
        return response.content[0].text
```

### 3.4 Structured Scoring System

Parse LLM output deterministically. Never rely on free-text scoring.

```python
# services/llm/response_parser.py
import xml.etree.ElementTree as ET
from dataclasses import dataclass

@dataclass
class InterviewerResponse:
    action: str
    spoken_text: str
    internal_notes: str
    score_update: dict | None
    next_state: str
    flags: list[str]

def parse_llm_response(raw: str) -> InterviewerResponse:
    # Extract XML block (LLM may add preamble)
    start = raw.find("<interviewer_response>")
    end = raw.find("</interviewer_response>") + len("</interviewer_response>")
    
    if start == -1 or end == -1:
        raise ValueError(f"No XML block found in LLM response: {raw[:200]}")
    
    xml_str = raw[start:end]
    root = ET.fromstring(xml_str)
    
    score_elem = root.find("score_update")
    score_update = None
    if score_elem is not None:
        score_update = {
            "topic": score_elem.findtext("topic"),
            "score": float(score_elem.findtext("score", "0")),
            "reasoning": score_elem.findtext("reasoning"),
        }
        # Validate score range
        if not 0 <= score_update["score"] <= 10:
            raise ValueError(f"Invalid score: {score_update['score']}")
    
    flags_text = root.findtext("flags", "")
    flags = [f.strip() for f in flags_text.split(",") if f.strip()]
    
    return InterviewerResponse(
        action=root.findtext("action", "follow_up"),
        spoken_text=root.findtext("spoken_text", ""),
        internal_notes=root.findtext("internal_notes", ""),
        score_update=score_update,
        next_state=root.findtext("next_state", ""),
        flags=flags,
    )
```

**Hallucination mitigation:**
- LLM never generates questions — all questions come from the question bank
- Scores are always validated (0-10 range, must include reasoning)
- `spoken_text` is stripped of markdown before TTS
- All LLM responses logged for audit

### 3.5 Follow-up Question Generation

Follow-ups are **guided** — not open-ended LLM generation. The LLM chooses from a structured follow-up tree.

```python
# services/questions/follow_up_engine.py
FOLLOW_UP_TRIGGERS = {
    "vague_answer": "Can you give me a specific example of when you've used that?",
    "incomplete_answer": "You mentioned {concept}. Can you explain how that works under the hood?",
    "strong_answer": "Great. Now, how would your approach change if {harder_constraint}?",
    "wrong_answer": "Interesting. Let me push on that — what happens in this edge case: {edge_case}?",
    "no_answer": "Take your time. What's the first thing that comes to mind when thinking about {topic}?",
}

async def generate_follow_up(
    ctx: SessionContext,
    answer_quality: str,  # vague | incomplete | strong | wrong | none
    concept_mentioned: str | None,
) -> str:
    q = ctx.questions[ctx.current_q_idx]
    
    # Select template based on answer quality
    template = FOLLOW_UP_TRIGGERS[f"{answer_quality}_answer"]
    
    # Fill template with LLM (short, targeted call)
    response = await anthropic.messages.create(
        model="claude-haiku-20240307",
        max_tokens=150,
        system="Generate a single follow-up question for a technical interview. Be specific and concise. Return only the question, no preamble.",
        messages=[{
            "role": "user",
            "content": f"""
Template: {template}
Topic: {q.topic}
Original question: {q.question_text}
Candidate answer: {ctx.transcript[-1]['text']}
Concept mentioned: {concept_mentioned or 'none'}
Current difficulty: {q.difficulty}
Fill in the template appropriately, adjusting for the specific conversation context.
"""
        }]
    )
    return response.content[0].text.strip()
```

### 3.6 Coding Question Generation & Evaluation

For coding challenges, use a **static question bank + dynamic test case generation**.

```python
# services/questions/coding_evaluator.py

CODING_EVALUATION_RUBRIC = {
    "problem_understanding": {
        "weight": 0.15,
        "criteria": ["Clarifies constraints", "Identifies edge cases", "Restates problem correctly"]
    },
    "approach": {
        "weight": 0.25,
        "criteria": ["Explains approach before coding", "Considers complexity", "Mentions tradeoffs"]
    },
    "implementation": {
        "weight": 0.35,
        "criteria": ["Correct solution", "Clean code", "Handles edge cases", "No major bugs"]
    },
    "optimization": {
        "weight": 0.15,
        "criteria": ["Discusses time complexity", "Discusses space complexity", "Offers improvements"]
    },
    "communication": {
        "weight": 0.10,
        "criteria": ["Explains reasoning", "Asks good questions", "Responds to hints"]
    },
}

async def evaluate_coding_response(
    question: str,
    candidate_explanation: str,  # what they said (voice)
    code_submitted: str | None,   # if using code editor
) -> dict:
    prompt = f"""
Evaluate this coding interview response.

Question: {question}
Candidate explanation: {candidate_explanation}
Code submitted: {code_submitted or "Voice only, no code submitted"}

Score each dimension 0-10:
{json.dumps(CODING_EVALUATION_RUBRIC, indent=2)}

Return JSON only:
{{
  "problem_understanding": {{"score": X, "notes": "..."}},
  "approach": {{"score": X, "notes": "..."}},
  "implementation": {{"score": X, "notes": "..."}},
  "optimization": {{"score": X, "notes": "..."}},
  "communication": {{"score": X, "notes": "..."}},
  "overall": {{"score": X, "recommendation": "strong_yes|yes|no|strong_no"}}
}}
"""
    # Use Sonnet for coding evaluation (better reasoning)
    response = await anthropic.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )
    return json.loads(response.content[0].text)
```

### 3.7 Tool Calling Requirements

The interview engine uses tool calls for structured operations during the interview.

```python
INTERVIEW_TOOLS = [
    {
        "name": "record_score",
        "description": "Record a score for a specific topic",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "score": {"type": "number", "minimum": 0, "maximum": 10},
                "reasoning": {"type": "string"}
            },
            "required": ["topic", "score", "reasoning"]
        }
    },
    {
        "name": "advance_question",
        "description": "Move to the next interview question",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "enum": ["answered", "skipped", "time_limit", "max_followups"]}
            },
            "required": ["reason"]
        }
    },
    {
        "name": "flag_candidate",
        "description": "Flag an observation about the candidate",
        "input_schema": {
            "type": "object",
            "properties": {
                "flag_type": {"type": "string", "enum": ["suspicious_accuracy", "long_pause", "off_topic", "possible_assistance"]},
                "details": {"type": "string"}
            },
            "required": ["flag_type", "details"]
        }
    },
    {
        "name": "request_clarification",
        "description": "Ask the candidate to clarify or elaborate",
        "input_schema": {
            "type": "object",
            "properties": {
                "aspect": {"type": "string"}
            },
            "required": ["aspect"]
        }
    }
]
```

---

## 4. API REQUIREMENTS & COST ANALYSIS

### 4.1 Required APIs

| Service | Purpose | Tier | Required? |
|---|---|---|---|
| Deepgram Nova-2 | Streaming STT | Pay-per-use | Yes (primary) |
| OpenAI Whisper API | STT fallback | Pay-per-use | Yes (fallback) |
| Anthropic Claude Sonnet | Main interview LLM | Pay-per-token | Yes |
| Anthropic Claude Haiku | Follow-ups, compression | Pay-per-token | Yes |
| ElevenLabs Turbo v2 | TTS primary | Pay-per-char | Yes |
| OpenAI TTS | TTS fallback | Pay-per-char | Yes |
| AWS S3 | Audio + transcript storage | Pay-per-GB | Yes |
| AWS CloudFront | CDN for audio delivery | Pay-per-transfer | Yes |
| Postmark / SendGrid | Email notifications | Pay-per-email | Yes |
| Sentry | Error tracking | SaaS | Yes |
| Datadog / Grafana | Observability | SaaS/self-host | Yes |

### 4.2 STT Provider Comparison

| Provider | Model | WER | Latency | Cost/hr | Streaming | Notes |
|---|---|---|---|---|---|---|
| **Deepgram** | Nova-2 | ~5% | ~100ms | $0.059 | Yes | Best for realtime |
| OpenAI | Whisper Large v3 | ~4% | ~1-3s | $0.36 | No (batch) | Better accuracy, high latency |
| Google | Chirp | ~6% | ~150ms | $0.048 | Yes | Slightly lower quality |
| AssemblyAI | Nano | ~8% | ~120ms | $0.037 | Yes | Cheapest, lower quality |
| AWS Transcribe | — | ~7% | ~200ms | $0.024 | Yes | Cheapest, worst quality |

**Recommendation**: Deepgram Nova-2 primary, OpenAI Whisper for post-processing verification.

### 4.3 TTS Provider Comparison

| Provider | Model | Quality | Latency | Cost/1M chars | Streaming | Notes |
|---|---|---|---|---|---|---|
| **ElevenLabs** | Turbo v2 | ★★★★★ | ~200ms | $330 | Yes | Best voice, highest cost |
| OpenAI | TTS-1 | ★★★★ | ~300ms | $15 | Yes (chunked) | Great quality/cost |
| Google | WaveNet | ★★★ | ~250ms | $16 | Yes | Good fallback |
| AWS Polly | Neural | ★★★ | ~200ms | $16 | Yes | Cheapest viable |
| PlayHT | 2.0-turbo | ★★★★ | ~250ms | $30 | Yes | Good alt |

**Recommendation**: ElevenLabs Turbo v2 for primary (quality matters for interview experience), OpenAI TTS-1 as fallback.

### 4.4 LLM Options for Real-time

| Model | First Token | Quality | Cost/1M tokens (in/out) | Context | Notes |
|---|---|---|---|---|---|
| **Claude Sonnet 3.5** | ~300ms | ★★★★★ | $3/$15 | 200k | Best for main interview |
| **Claude Haiku 3** | ~100ms | ★★★ | $0.25/$1.25 | 200k | Fast, cheap follow-ups |
| GPT-4o | ~400ms | ★★★★★ | $5/$15 | 128k | Comparable to Sonnet |
| GPT-4o-mini | ~150ms | ★★★★ | $0.15/$0.60 | 128k | Good for simple tasks |
| Gemini 1.5 Flash | ~200ms | ★★★ | $0.075/$0.30 | 1M | Cheapest, good quality |

**Recommendation**: Claude Sonnet 3.5 for main interview, Claude Haiku 3 for follow-up generation and memory compression.

### 4.5 Cost Per Interview (30-minute session)

```
ASSUMPTIONS:
  - 30 minute interview
  - ~1,500 words of candidate speech = ~11 minutes of audio
  - ~2,000 words of bot speech = ~15 minutes of audio
  - ~15 LLM calls, avg 4,000 input tokens + 400 output tokens each
  - 2 compression calls (Haiku), 500 input + 150 output each
  
DEEPGRAM (STT):
  Audio: 30 min × $0.059/hr = $0.0295
  
ELEVENLABS (TTS):
  Bot speech: 2,000 words × 6 chars/word = 12,000 chars
  Cost: 12,000 / 1,000,000 × $330 = $0.00396
  (OpenAI TTS fallback: 12,000 / 1,000,000 × $15 = $0.00018)
  
CLAUDE SONNET 3.5 (main LLM):
  Input: 15 calls × 4,000 tokens = 60,000 tokens × $3/1M = $0.18
  Output: 15 calls × 400 tokens = 6,000 tokens × $15/1M = $0.09
  Subtotal: $0.27

CLAUDE HAIKU 3 (follow-ups + compression):
  Input: 2 calls × 500 = 1,000 tokens × $0.25/1M = $0.00025
  Output: 2 calls × 150 = 300 tokens × $1.25/1M = $0.000375
  Subtotal: $0.0006 (negligible)

AWS S3 (audio storage):
  30 min MP3 @ 64kbps ≈ 14MB
  Cost: 14MB × $0.023/GB = $0.0003

TOTAL PER INTERVIEW: ~$0.30
```

### 4.6 Scaling Cost Projections

| Volume | Daily API Cost | Monthly API Cost | Infra Cost/mo | Total/mo |
|---|---|---|---|---|
| 100 interviews/day | $30/day | $900 | $400 | $1,300 |
| 500 interviews/day | $150/day | $4,500 | $1,200 | $5,700 |
| 1,000 interviews/day | $300/day | $9,000 | $2,500 | $11,500 |
| 5,000 interviews/day | $1,500/day | $45,000 | $8,000 | $53,000 |

**Infrastructure breakdown at 1,000/day (estimated concurrent: ~50 sessions):**
- EKS cluster (4x t3.xlarge): $600/mo
- RDS Postgres (db.t3.medium, Multi-AZ): $180/mo
- ElastiCache Redis (cache.t3.medium): $100/mo
- S3 (500GB audio): $12/mo
- ALB + CloudFront: $80/mo
- Nat Gateway, misc: $200/mo
- Monitoring (Datadog): $300/mo + $400 apm

---

## 5. DATABASE DESIGN

### 5.1 PostgreSQL Schema

```sql
-- ============================================================
-- USERS
-- ============================================================
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(255) UNIQUE NOT NULL,
    name            VARCHAR(255) NOT NULL,
    role            VARCHAR(50) NOT NULL DEFAULT 'candidate',  -- candidate | recruiter | admin
    org_id          UUID REFERENCES organizations(id),
    password_hash   VARCHAR(255),                              -- NULL if OAuth only
    oauth_provider  VARCHAR(50),                               -- google | github | linkedin
    oauth_sub       VARCHAR(255),
    is_active       BOOLEAN DEFAULT true,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_org ON users(org_id);

-- ============================================================
-- ORGANIZATIONS
-- ============================================================
CREATE TABLE organizations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,
    slug            VARCHAR(100) UNIQUE NOT NULL,
    plan            VARCHAR(50) DEFAULT 'free',  -- free | starter | pro | enterprise
    interview_quota INTEGER DEFAULT 10,           -- monthly limit
    interviews_used INTEGER DEFAULT 0,
    settings        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- INTERVIEW SESSIONS
-- ============================================================
CREATE TABLE interview_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations(id),
    candidate_id    UUID NOT NULL REFERENCES users(id),
    recruiter_id    UUID REFERENCES users(id),
    job_role        VARCHAR(255) NOT NULL,
    experience_level VARCHAR(50) NOT NULL,  -- junior | mid | senior | staff
    required_skills TEXT[] NOT NULL DEFAULT '{}',
    
    -- State
    status          VARCHAR(50) NOT NULL DEFAULT 'scheduled',
    -- scheduled | in_progress | completed | cancelled | failed
    state           VARCHAR(50),            -- FSM state (from Redis)
    
    -- Timing
    scheduled_at    TIMESTAMPTZ,
    started_at      TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    duration_seconds INTEGER,
    
    -- Audio
    audio_s3_key    VARCHAR(500),
    audio_duration_seconds INTEGER,
    
    -- Config
    question_ids    UUID[] DEFAULT '{}',
    interview_config JSONB DEFAULT '{}',    -- custom settings
    
    -- Anti-cheat
    flags           TEXT[] DEFAULT '{}',
    barge_in_count  INTEGER DEFAULT 0,
    silence_count   INTEGER DEFAULT 0,
    tab_switch_count INTEGER DEFAULT 0,
    
    -- Meta
    client_ip       INET,
    user_agent      TEXT,
    session_token   VARCHAR(255),           -- short-lived token for WebSocket auth
    
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_sessions_candidate ON interview_sessions(candidate_id);
CREATE INDEX idx_sessions_org ON interview_sessions(org_id);
CREATE INDEX idx_sessions_status ON interview_sessions(status);
CREATE INDEX idx_sessions_scheduled ON interview_sessions(scheduled_at);

-- ============================================================
-- TRANSCRIPTS
-- ============================================================
CREATE TABLE transcripts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
    
    -- Full transcript (JSONB for flexibility)
    turns           JSONB NOT NULL DEFAULT '[]',
    -- [{turn_idx, speaker (bot|candidate), text, timestamp, audio_start_ms, audio_end_ms, confidence}]
    
    -- Derived text
    full_text       TEXT,                   -- concatenated transcript (for search)
    word_count      INTEGER,
    
    -- Processing state
    is_complete     BOOLEAN DEFAULT false,
    s3_key          VARCHAR(500),           -- JSON transcript on S3
    
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_transcripts_session ON transcripts(session_id);
-- Full text search
CREATE INDEX idx_transcripts_fulltext ON transcripts USING gin(to_tsvector('english', COALESCE(full_text, '')));

-- ============================================================
-- EVALUATIONS
-- ============================================================
CREATE TABLE evaluations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
    evaluator       VARCHAR(50) DEFAULT 'ai',    -- ai | human | hybrid
    
    -- Scores per dimension
    technical_scores JSONB NOT NULL DEFAULT '{}',
    -- {"system_design": {"score": 7.5, "max": 10, "notes": "..."}, ...}
    
    behavioral_scores JSONB DEFAULT '{}',
    
    -- Aggregate
    overall_score   NUMERIC(4,2),            -- 0.00 - 10.00
    recommendation  VARCHAR(50),             -- strong_yes | yes | no | strong_no
    
    -- Detailed feedback
    strengths       TEXT[],
    weaknesses      TEXT[],
    summary         TEXT,
    hiring_notes    TEXT,                    -- for recruiter eyes only
    
    -- Human override
    human_reviewed  BOOLEAN DEFAULT false,
    reviewed_by     UUID REFERENCES users(id),
    reviewed_at     TIMESTAMPTZ,
    override_score  NUMERIC(4,2),
    override_reason TEXT,
    
    -- Report
    report_s3_key   VARCHAR(500),           -- PDF report path
    
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX idx_evaluations_session ON evaluations(session_id);

-- ============================================================
-- QUESTION BANK
-- ============================================================
CREATE TABLE questions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID REFERENCES organizations(id),  -- NULL = global
    
    topic           VARCHAR(100) NOT NULL,        -- e.g., "system_design", "algorithms", "databases"
    difficulty      VARCHAR(20) NOT NULL,         -- easy | medium | hard | expert
    question_type   VARCHAR(50) NOT NULL,         -- conceptual | coding | behavioral | scenario
    experience_level VARCHAR(50) NOT NULL,        -- junior | mid | senior | all
    
    question_text   TEXT NOT NULL,
    follow_up_texts TEXT[] DEFAULT '{}',         -- scripted follow-ups
    answer_hints    TEXT,                         -- for human reviewers
    
    rubric          JSONB NOT NULL DEFAULT '{}',
    -- {"criteria": [{"name": "...", "weight": 0.3, "good": "...", "bad": "..."}]}
    
    -- Metadata
    tags            TEXT[] DEFAULT '{}',
    is_active       BOOLEAN DEFAULT true,
    times_asked     INTEGER DEFAULT 0,
    avg_score       NUMERIC(4,2),
    
    created_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_questions_topic ON questions(topic);
CREATE INDEX idx_questions_difficulty ON questions(difficulty);
CREATE INDEX idx_questions_org ON questions(org_id);
CREATE INDEX idx_questions_active ON questions(is_active) WHERE is_active = true;

-- ============================================================
-- QUESTION SELECTIONS (per session)
-- ============================================================
CREATE TABLE session_questions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES interview_sessions(id),
    question_id     UUID NOT NULL REFERENCES questions(id),
    order_idx       INTEGER NOT NULL,
    
    -- Per-question state
    status          VARCHAR(50) DEFAULT 'pending',  -- pending | asked | answered | skipped
    score           NUMERIC(4,2),
    score_breakdown JSONB DEFAULT '{}',
    follow_up_count INTEGER DEFAULT 0,
    notes           TEXT,                           -- LLM internal notes for this question
    
    asked_at        TIMESTAMPTZ,
    answered_at     TIMESTAMPTZ,
    time_spent_seconds INTEGER,
    
    UNIQUE(session_id, order_idx)
);

-- ============================================================
-- AUDIO METADATA
-- ============================================================
CREATE TABLE audio_recordings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES interview_sessions(id),
    
    s3_key          VARCHAR(500) NOT NULL,
    s3_bucket       VARCHAR(100) NOT NULL,
    
    format          VARCHAR(20) DEFAULT 'mp3',
    duration_ms     INTEGER,
    size_bytes      BIGINT,
    sample_rate     INTEGER,
    channels        INTEGER DEFAULT 1,
    
    -- Encryption
    encryption_key_id VARCHAR(255),               -- KMS key ID
    
    -- Processing
    is_processed    BOOLEAN DEFAULT false,
    transcription_verified BOOLEAN DEFAULT false,
    
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- ANALYTICS EVENTS
-- ============================================================
CREATE TABLE analytics_events (
    id              BIGSERIAL PRIMARY KEY,
    session_id      UUID REFERENCES interview_sessions(id),
    org_id          UUID REFERENCES organizations(id),
    event_type      VARCHAR(100) NOT NULL,
    event_data      JSONB DEFAULT '{}',
    occurred_at     TIMESTAMPTZ DEFAULT NOW()
) PARTITION BY RANGE (occurred_at);

-- Create monthly partitions
CREATE TABLE analytics_events_2024_01 PARTITION OF analytics_events
    FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
-- (continue monthly...)

CREATE INDEX idx_analytics_session ON analytics_events(session_id);
CREATE INDEX idx_analytics_org ON analytics_events(org_id);
CREATE INDEX idx_analytics_type ON analytics_events(event_type);

-- ============================================================
-- AUDIT LOG
-- ============================================================
CREATE TABLE audit_logs (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID REFERENCES users(id),
    org_id          UUID REFERENCES organizations(id),
    action          VARCHAR(100) NOT NULL,
    resource_type   VARCHAR(50),
    resource_id     UUID,
    ip_address      INET,
    user_agent      TEXT,
    metadata        JSONB DEFAULT '{}',
    occurred_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_audit_user ON audit_logs(user_id);
CREATE INDEX idx_audit_occurred ON audit_logs(occurred_at);
```

### 5.2 Redis Key Schema

```
# Session state (Hash)
session:{session_id}                → full session state dict
session:{session_id}:lock           → distributed lock (TTL: 30s)

# Rate limiting (String with TTL)
ratelimit:ws:{user_id}              → connection count
ratelimit:api:{user_id}:{minute}    → request count

# JWT blacklist (Set)
jwt:blacklist                       → set of revoked token JTIs

# WebSocket room management (Hash)
ws:room:{session_id}                → {pod_id, connection_id, last_ping}

# LLM response cache (String with TTL)
llm:cache:{hash_of_prompt}          → cached response (TTL: 1hr)

# Interview queue (List)
queue:scheduled                     → list of upcoming session IDs

# Pub/Sub channels
channel:session:{session_id}:control
channel:session:{session_id}:audio
```

---

## 6. SECURITY & COMPLIANCE

### 6.1 Authentication Strategy

Use JWT with short-lived access tokens + long-lived refresh tokens stored in HttpOnly cookies.

```
Auth Flow:
  1. User logs in → POST /auth/login
  2. Server validates credentials
  3. Issue: access_token (15 min TTL), refresh_token (30 days, HttpOnly cookie)
  4. WebSocket auth: pass access_token in query param on WS upgrade
     (not in header — browsers don't support WS auth headers)
  5. Server validates JWT on WS upgrade, rejects if invalid/expired
  6. Session token (short-lived, single-use) issued for each interview session

Interview Session Token:
  - Separate from user JWT
  - 4-hour TTL, tied to specific session_id
  - Stored in Redis, invalidated on session end
  - Prevents session hijacking between interviews
```

```python
# services/auth/jwt_handler.py
import jwt
from datetime import datetime, timedelta

def create_interview_token(session_id: str, user_id: str) -> str:
    payload = {
        "sub": user_id,
        "session_id": session_id,
        "type": "interview",
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(hours=4),
        "jti": str(uuid4()),  # unique ID for blacklisting
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="RS256")
```

Use **RS256** (asymmetric) not HS256. Keep private key in AWS Secrets Manager.

### 6.2 Rate Limiting Strategy

```
Limits (per user):
  WebSocket connections: 2 concurrent max
  API requests: 60/min general, 10/min auth endpoints
  Interview starts: 5/hour (anti-abuse)
  
Limits (per org):
  Concurrent interviews: plan-based (Free: 2, Starter: 10, Pro: 50)
  
Implementation: Redis sliding window counter
  Key: ratelimit:{type}:{user_id}:{window}
  Window: 60 seconds
  Atomic increment + expire via Lua script
```

### 6.3 PII Handling

```
PII Data Inventory:
  - Candidate name (users table)
  - Candidate email (users table)
  - Voice recordings (S3, encrypted)
  - Transcript text (may contain personal info)
  - IP addresses (audit_logs, sessions)

Pseudonymization:
  - All analytics events use UUIDs, not names/emails
  - Transcripts stored with session_id reference, not candidate name embedded
  - Audio S3 keys use session_id, not candidate name

Data Retention:
  - Audio recordings: 90 days default, configurable per org
  - Transcripts: 1 year
  - Analytics: 2 years
  - Audit logs: 7 years (compliance)

Right to erasure:
  - Candidate deletion: anonymize users row, delete audio from S3,
    replace transcript text with "[REDACTED]"
  - 30-day deletion queue worker
```

### 6.4 Audio Storage Security

```
S3 Security:
  - Bucket: private (no public access)
  - Server-side encryption: SSE-KMS (AWS KMS managed keys)
  - Bucket policy: deny all public access
  - Access via pre-signed URLs (15 min expiry)
  - VPC endpoint for S3 (no public internet traversal)

Audio at rest:
  - AES-256 via KMS
  - Key rotation: annual
  - Per-org encryption keys for enterprise tier

Audio in transit:
  - TLS 1.3 on all WebSocket connections
  - No audio data in URL paths or query strings
  - Binary WebSocket frames (not base64)
```

### 6.5 Anti-Cheating Measures

These are probabilistic signals, not definitive proof. Flag for human review.

```python
ANTI_CHEAT_SIGNALS = {
    "suspicious_accuracy": {
        "description": "Answers match textbook definitions verbatim",
        "detection": "LLM evaluates phrasing naturalness",
        "weight": 3,
    },
    "long_pause_then_perfect": {
        "description": "Extended silence followed by highly accurate answer",
        "detection": "silence_duration > 8s AND score > 8",
        "weight": 2,
    },
    "audio_artifacts": {
        "description": "Background keyboard typing, multiple voices, echo",
        "detection": "Deepgram word confidence variance > threshold",
        "weight": 3,
    },
    "copy_paste_speed": {
        "description": "Unusually fast speech with no pauses (reading from screen)",
        "detection": "words_per_minute > 180 for technical content",
        "weight": 2,
    },
    "tab_focus_loss": {
        "description": "Candidate switches browser tabs",
        "detection": "Browser visibility API, Page Focus event",
        "weight": 1,
    },
    "multiple_audio_sources": {
        "description": "Audio characteristics change mid-session",
        "detection": "Audio fingerprint drift detection",
        "weight": 4,
    },
}
```

### 6.6 SOC2 / GDPR Considerations

| Control | SOC2 Mapping | Implementation |
|---|---|---|
| Access control | CC6.1 | RBAC, JWT, session tokens |
| Encryption at rest | CC6.7 | KMS, SSE-S3 |
| Encryption in transit | CC6.7 | TLS 1.3, WSS |
| Audit logging | CC7.2 | audit_logs table, all actions |
| Data retention | A1.1 | configurable TTLs, deletion workers |
| Incident response | CC7.3 | PagerDuty, runbooks |
| Vendor management | CC9.2 | Document Deepgram, ElevenLabs, Anthropic DPA |
| GDPR Article 17 | Right to erasure | Deletion queue worker |
| GDPR Article 13 | Privacy notice | Consent capture before interview |
| GDPR Article 28 | DPA with vendors | Sign DPAs with all API vendors |

**Pre-interview consent capture:**
```
Screen shown to candidate before interview starts:
"This interview will be recorded (audio and transcript) for evaluation purposes.
 By continuing, you consent to this recording. Recordings are deleted after 90 days.
 For questions about your data, contact privacy@{org}.com"
[Agree] [Decline]
```

Consent is stored in the interview_sessions row with timestamp.

---

## 7. OBSERVABILITY

### 7.1 Logging Strategy

Use structured JSON logging with correlation IDs. Every log entry includes: `session_id`, `org_id`, `request_id`, `severity`, `timestamp`.

```python
# services/core/logging.py
import structlog

log = structlog.get_logger()

# In interview engine, bind context once per session
session_log = log.bind(
    session_id=session_id,
    org_id=org_id,
    candidate_id=candidate_id,
)

# Use throughout session
session_log.info("turn_complete",
    turn_idx=turn_idx,
    speaker="bot",
    action="ask_question",
    question_id=str(question_id),
    latency_ms=total_latency,
    stt_latency_ms=stt_latency,
    llm_latency_ms=llm_latency,
    tts_latency_ms=tts_latency,
    tokens_used=token_count,
)
```

**Log levels:**
- `DEBUG`: Audio chunks, partial transcripts (dev only, never production)
- `INFO`: Turn completions, state transitions, scores recorded
- `WARN`: STT confidence < 0.7, LLM retry, TTS fallback triggered
- `ERROR`: API failure, session crash, DB write failure
- `CRITICAL`: Security event, data loss risk

**Log retention:**
- Production: 30 days hot (CloudWatch/Datadog), 1 year cold (S3)
- Security logs: 7 years

### 7.2 Distributed Tracing

Use **OpenTelemetry** with traces exported to Datadog or Jaeger.

```python
# Every interview turn becomes a trace span
from opentelemetry import trace

tracer = trace.get_tracer("interview-engine")

async def process_turn(session: Session, audio: bytes) -> TurnResult:
    with tracer.start_as_current_span("interview.turn") as span:
        span.set_attribute("session.id", session.id)
        span.set_attribute("turn.index", session.turn_count)
        
        with tracer.start_as_current_span("stt.transcribe") as stt_span:
            transcript = await deepgram.transcribe(audio)
            stt_span.set_attribute("stt.confidence", transcript.confidence)
            stt_span.set_attribute("stt.words", len(transcript.words))
        
        with tracer.start_as_current_span("llm.generate") as llm_span:
            response = await claude.generate(session.build_prompt(transcript))
            llm_span.set_attribute("llm.input_tokens", response.usage.input_tokens)
            llm_span.set_attribute("llm.output_tokens", response.usage.output_tokens)
            llm_span.set_attribute("llm.model", response.model)
        
        with tracer.start_as_current_span("tts.synthesize") as tts_span:
            audio_url = await elevenlabs.synthesize(response.spoken_text)
            tts_span.set_attribute("tts.chars", len(response.spoken_text))
        
        return TurnResult(transcript=transcript, response=response, audio=audio_url)
```

### 7.3 Key Metrics Dashboard

```
LATENCY METRICS (P50/P95/P99):
  interview.turn.total_latency_ms          Target: < 1500ms P95
  interview.stt.latency_ms                 Target: < 200ms P95
  interview.llm.first_token_ms             Target: < 400ms P95
  interview.tts.first_chunk_ms             Target: < 300ms P95
  interview.websocket.rtt_ms               Target: < 50ms P95

RELIABILITY METRICS:
  interview.session.success_rate           Target: > 99%
  interview.stt.fallback_rate              Target: < 1%
  interview.tts.fallback_rate              Target: < 0.5%
  interview.llm.error_rate                 Target: < 0.1%
  interview.websocket.disconnect_rate      Target: < 2%

BUSINESS METRICS:
  interview.sessions.started_per_hour
  interview.sessions.completed_per_hour
  interview.completion_rate                Target: > 90%
  interview.avg_score_by_role
  interview.avg_duration_minutes

COST METRICS:
  interview.cost.stt_per_session_usd
  interview.cost.llm_per_session_usd
  interview.cost.tts_per_session_usd
  interview.cost.total_per_session_usd
  interview.cost.daily_total_usd
```

### 7.4 Alerting Rules

| Alert | Condition | Severity | Action |
|---|---|---|---|
| High turn latency | P95 > 3000ms (5min window) | Warning | Slack |
| STT failure spike | Error rate > 5% (5min) | Critical | PagerDuty |
| LLM error rate | Error rate > 2% (5min) | Critical | PagerDuty |
| Session completion drop | Rate drops >20% vs 1hr avg | Warning | Slack |
| DB connection pool | Pool usage > 80% | Warning | Slack |
| Redis memory | Usage > 75% | Warning | Slack |
| Cost spike | Daily cost > 2x yesterday | Warning | Email |

### 7.5 Failed Transcript Recovery

```python
# workers/transcript_recovery.py
async def recover_incomplete_transcripts():
    """
    Runs every 5 minutes via Celery beat.
    Finds sessions that ended but have incomplete transcripts.
    """
    sessions = await db.fetch("""
        SELECT s.id, s.audio_s3_key
        FROM interview_sessions s
        LEFT JOIN transcripts t ON s.id = t.session_id
        WHERE s.status = 'completed'
        AND s.ended_at < NOW() - INTERVAL '5 minutes'
        AND (t.is_complete = false OR t.id IS NULL)
        AND s.audio_s3_key IS NOT NULL
    """)
    
    for session in sessions:
        # Re-transcribe from audio using Whisper (batch, more accurate)
        audio = await s3.get_object(session.audio_s3_key)
        transcript = await openai.audio.transcriptions.create(
            model="whisper-1",
            file=audio,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )
        
        await db.execute("""
            INSERT INTO transcripts (session_id, turns, full_text, is_complete)
            VALUES ($1, $2, $3, true)
            ON CONFLICT (session_id) DO UPDATE
            SET full_text = $3, is_complete = true, updated_at = NOW()
        """, session.id, format_whisper_to_turns(transcript), transcript.text)
        
        log.info("transcript_recovered", session_id=str(session.id))
```

### 7.6 Interview Replay Tooling

Build an internal admin UI for replaying interviews:
```
Replay Features:
  - Timeline scrubber synced to audio + transcript
  - Highlight bot turns vs candidate turns
  - Show LLM internal notes alongside transcript
  - Score breakdown per question (expandable)
  - Anti-cheat flag annotations on timeline
  - Side-by-side: audio waveform + transcript + scores
  - Export to PDF report
  
API: GET /admin/sessions/{session_id}/replay
Returns: {audio_url (signed), transcript_turns, scores, flags, llm_notes}
```

---

## 8. DEPLOYMENT STRATEGY

### 8.1 Monolith vs Microservices Decision

**Recommendation for MVP: Modular Monolith** (single deployable, internal module separation)

Rationale:
- Microservices add distributed systems complexity you don't need at < 100 concurrent sessions
- A well-structured monolith is trivially decomposed later (each module → its own service)
- Faster iteration: no inter-service HTTP overhead, shared types, single deploy
- Exception: Auth Service should be separate from Day 1 (security boundary requirement)

```
MVP Architecture:
  interview-api (FastAPI monolith)
    ├── modules/auth/         (separate service in month 2)
    ├── modules/interview/
    ├── modules/audio/
    ├── modules/llm/
    ├── modules/scoring/
    └── modules/admin/
  
  celery-worker (separate process, same codebase)
  
  frontend (Next.js, separate repo)
```

### 8.2 Docker Strategy

```dockerfile
# Dockerfile.api
FROM python:3.12-slim

WORKDIR /app

# Install system deps for audio processing
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root user
RUN useradd -m -u 1000 app && chown -R app:app /app
USER app

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1",           # One worker per container (async, not threaded)
     "--loop", "uvloop",         # Faster async loop
     "--http", "httptools"]      # Faster HTTP parser
```

```yaml
# docker-compose.yml (development)
version: '3.9'
services:
  api:
    build:
      context: .
      dockerfile: Dockerfile.api
    ports: ["8000:8000"]
    environment:
      DATABASE_URL: postgresql://postgres:dev@db:5432/interview_db
      REDIS_URL: redis://redis:6379/0
    volumes: ["./:/app"]  # hot reload in dev
    depends_on: [db, redis]
  
  worker:
    build: { context: ., dockerfile: Dockerfile.api }
    command: celery -A tasks worker --loglevel=info -Q default,transcripts,scoring
    depends_on: [db, redis]
  
  frontend:
    build: { context: ./frontend, dockerfile: Dockerfile.frontend }
    ports: ["3000:3000"]
    environment:
      NEXT_PUBLIC_WS_URL: ws://localhost:8000
  
  db:
    image: postgres:16-alpine
    environment: { POSTGRES_DB: interview_db, POSTGRES_PASSWORD: dev }
    volumes: ["pgdata:/var/lib/postgresql/data"]
  
  redis:
    image: redis:7-alpine
    volumes: ["redisdata:/data"]

volumes: { pgdata: {}, redisdata: {} }
```

### 8.3 Kubernetes Necessity

**Not required for MVP** (< 200 concurrent sessions). Use ECS Fargate instead for simplicity.

Switch to EKS when: concurrent sessions > 100, need auto-scaling < 60s, multi-region required.

**ECS Fargate MVP Setup:**
```yaml
# ecs-task-definition.json (simplified)
CPU: 1024 (1 vCPU)
Memory: 2048MB
Container: interview-api
  PortMappings: [8000]
  Environment:
    DATABASE_URL: {from Secrets Manager}
    REDIS_URL: {from Secrets Manager}
  HealthCheck:
    Command: ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
    Interval: 30s
    Timeout: 5s
    Retries: 3

AutoScaling:
  Min: 2 tasks
  Max: 20 tasks
  ScaleOut: CPU > 70% for 2 min
  ScaleIn: CPU < 30% for 5 min
```

### 8.4 CI/CD Pipeline

```yaml
# .github/workflows/deploy.yml
name: Deploy

on:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run tests
        run: |
          pip install -r requirements-dev.txt
          pytest tests/ --cov=. --cov-report=xml
          coverage xml
      - name: Type check
        run: mypy services/
      - name: Lint
        run: ruff check .

  build:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - name: Build and push Docker image
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: ${{ secrets.ECR_REGISTRY }}/interview-api:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  deploy-staging:
    needs: build
    environment: staging
    steps:
      - name: Deploy to ECS staging
        run: |
          aws ecs update-service \
            --cluster interview-staging \
            --service interview-api \
            --force-new-deployment

  deploy-production:
    needs: deploy-staging
    environment: production
    # Manual approval gate
    steps:
      - name: Run DB migrations
        run: |
          aws ecs run-task \
            --task-definition interview-migrate \
            --overrides '{"containerOverrides": [{"name": "api", "command": ["alembic", "upgrade", "head"]}]}'
      - name: Deploy to ECS production (blue/green)
        run: |
          aws deploy create-deployment \
            --application-name interview-api \
            --deployment-group-name prod \
            --revision imageTag=${{ github.sha }}
```

### 8.5 Cheapest Viable MVP Infrastructure (Month 1)

```
OPTION A: AWS ECS Fargate (Recommended for MVP)
  ECS Fargate (2 tasks, 1 vCPU/2GB):    $35/mo
  RDS Postgres t3.micro (single AZ):     $25/mo
  ElastiCache t3.micro:                  $30/mo
  ALB:                                   $25/mo
  S3 (50GB audio):                       $1.15/mo
  CloudFront:                            $10/mo
  Route53:                               $0.50/mo
  Secrets Manager:                       $1/mo
  CloudWatch:                            $10/mo
  Data transfer:                         $10/mo
  ──────────────────────────────────────────────
  TOTAL INFRA: ~$150/month
  + API costs: $30–300/mo depending on volume
  ──────────────────────────────────────────────
  MVP Total: ~$180–450/month for 100 interviews/day

OPTION B: Single VPS (Ultra-minimal, < 20 interviews/day)
  Hetzner Cloud CPX31 (4 vCPU/8GB): $14/mo
  Hetzner Volume (100GB): $5/mo
  External Postgres (Supabase free tier): $0/mo
  Redis (Upstash free tier): $0/mo
  ──────────────────────────────────────────────
  TOTAL INFRA: ~$20/month
  (Fine for development / very early customers)
```

---

## 9. DEVELOPMENT ROADMAP

### 9.1 MVP Scope (Non-negotiable for launch)

**In scope:**
- WebSocket voice interview (STT + TTS + LLM)
- 5 technical question types (conceptual, scenario, follow-up, behavioral, one coding)
- Session recording + transcript
- Basic scoring (AI-generated)
- Candidate invite flow (email link)
- Recruiter dashboard (session list + report view)
- Auth (email/password)

**Out of scope for MVP:**
- Multi-language support
- Custom question bank UI (hardcode for now)
- Video recording
- Calendar integration
- SSO / OAuth
- Advanced anti-cheat
- PDF report export
- Analytics dashboard

### 9.2 7-Day Sprint Plan

```
DAY 1 — Foundation
  ✅ Repo setup, Docker Compose, CI skeleton
  ✅ PostgreSQL schema + Alembic migrations
  ✅ Redis configuration
  ✅ FastAPI skeleton: auth endpoints, health check
  ✅ Next.js skeleton: auth pages, protected routes
  ✅ JWT auth working end-to-end

DAY 2 — Voice Pipeline
  ✅ Deepgram WebSocket integration (server side)
  ✅ Browser microphone capture + AudioWorklet (16kHz)
  ✅ Browser VAD (Silero ONNX)
  ✅ WebSocket server (FastAPI WebSocket)
  ✅ Audio → STT → transcript displayed in browser
  ✅ ElevenLabs TTS integration (non-streaming first)

DAY 3 — Interview Engine Core
  ✅ Session state machine (Redis)
  ✅ Question bank seeded (20 questions, JSON for now)
  ✅ LLM prompt architecture + XML response parser
  ✅ One full interview turn: STT → LLM → TTS → client
  ✅ Turn management + silence detection

DAY 4 — Interview Flow
  ✅ Full interview FSM: intro → questions → wrap_up
  ✅ Follow-up question generation
  ✅ Score recording per question
  ✅ Barge-in interruption handling
  ✅ Streaming TTS (sentence-by-sentence)
  ✅ Audio recording to S3

DAY 5 — Data + Scoring
  ✅ Transcript persistence (Postgres)
  ✅ End-of-session evaluation (LLM scoring pass)
  ✅ Evaluation saved to DB
  ✅ Async transcript finalization worker
  ✅ Session end flow (graceful completion)

DAY 6 — Frontend Polish + Recruiter Flow
  ✅ Candidate interview UI (audio visualizer, transcript, timer)
  ✅ Recruiter dashboard (sessions list)
  ✅ Session detail view (transcript + scores)
  ✅ Candidate invite flow (email with link)
  ✅ Interview scheduling (date/time selection)

DAY 7 — Hardening + Deploy
  ✅ Error handling + retry logic (all API calls)
  ✅ Basic rate limiting
  ✅ Health checks + readiness probes
  ✅ Deploy to AWS ECS (staging)
  ✅ End-to-end test: full 30-min interview
  ✅ Fix bugs from E2E test
```

### 9.3 14-Day Production-Ready Plan

```
WEEK 2 ADDITIONS:

DAY 8 — Reliability
  ✅ STT fallback (Whisper)
  ✅ TTS fallback (OpenAI TTS)
  ✅ LLM fallback (GPT-4o if Claude fails)
  ✅ WebSocket reconnection (client-side, exponential backoff)
  ✅ Session resume after disconnect
  ✅ Failed transcript recovery worker

DAY 9 — Observability
  ✅ Structured logging (structlog)
  ✅ OpenTelemetry tracing (all spans)
  ✅ Latency metrics per pipeline stage
  ✅ Sentry error tracking
  ✅ Grafana dashboard (latency, errors, sessions/hour)

DAY 10 — Security
  ✅ Rate limiting (Redis sliding window)
  ✅ JWT refresh token rotation
  ✅ Audit logging (all sensitive actions)
  ✅ S3 bucket hardening (SSE-KMS, private, pre-signed URLs)
  ✅ Input validation (all endpoints)
  ✅ Security headers (CORS, CSP, HSTS)

DAY 11 — Anti-Cheat + Quality
  ✅ Tab visibility tracking (frontend)
  ✅ Basic anti-cheat signal recording
  ✅ LLM response quality validation
  ✅ Confidence score tracking
  ✅ Interview replay viewer (admin)

DAY 12 — Scale Testing
  ✅ Load test: 20 concurrent WebSocket sessions
  ✅ Fix connection pool exhaustion
  ✅ Tune Redis connection pool
  ✅ Tune Postgres connection pool (PgBouncer)
  ✅ Latency profiling: identify P99 bottlenecks

DAY 13 — Production Deploy
  ✅ Production AWS environment (separate from staging)
  ✅ RDS Multi-AZ (for production)
  ✅ Auto-scaling policy
  ✅ CloudFront CDN
  ✅ Route53 + SSL cert
  ✅ Blue/green deployment pipeline
  ✅ Backup policy (RDS automated, S3 versioning)

DAY 14 — Final Validation
  ✅ Full E2E test on production
  ✅ Penetration test (basic: OWASP top 10 check)
  ✅ Runbooks for common incidents
  ✅ PagerDuty alerting setup
  ✅ Soft launch: 5 real interviews with friendly beta users
  ✅ Bug fixes from beta
```

### 9.4 Team Requirements

| Role | MVP Need | Allocation |
|---|---|---|
| Backend Engineer | FastAPI, async Python, WebSocket | 1 FTE (you) |
| Frontend Engineer | Next.js, Audio APIs, WebSocket client | 0.5 FTE (or Claude Code) |
| DevOps / Infra | AWS, Docker, CI/CD | 0.25 FTE (or Claude Code) |
| Product / QA | Interview quality, testing | 0.25 FTE |

**Solo founder path**: All of the above with Claude Code. Realistic if you have Python + Next.js experience.

### 9.5 What Claude Code Can Generate (High Confidence)

- All FastAPI endpoint boilerplate and Pydantic models
- Database schema SQL + Alembic migrations
- Redis client wrappers
- LLM prompt templates + response parsers
- Deepgram and ElevenLabs API integrations
- Next.js pages, components, API routes
- Docker and docker-compose files
- GitHub Actions CI/CD pipelines
- Unit test scaffolding (> 70% test coverage achievable)
- Celery task definitions
- Structured logging setup
- OpenTelemetry instrumentation
- AWS CDK / Terraform for infra (ECS, RDS, Redis, S3)

### 9.6 Where Manual Engineering Is Still Required

- Audio worklet / VAD tuning for your specific use case
- Silero VAD WASM build and integration
- Latency profiling and optimization (requires real-device testing)
- Deepgram endpointing parameter tuning (iterative, not code)
- LLM prompt quality iteration (red-teaming, edge cases)
- Interview question rubric design (domain expertise)
- Anti-cheat threshold tuning (empirical)
- Security review (always human eyes on auth code)
- Load testing and performance profiling at scale
- UX decisions: when to barge-in, silence timeouts, voice selection

---

## 10. FINAL STACK RECOMMENDATION

### Recommended Stack

| Layer | Technology | Reasoning |
|---|---|---|
| **Frontend** | Next.js 14 + TypeScript | App router, SSR for auth, excellent WS support |
| **Audio Capture** | Web Audio API + AudioWorklet | Browser-native, no plugins |
| **Browser VAD** | Silero VAD (ONNX Runtime Web) | Best accuracy, runs in Web Worker |
| **WebSocket** | FastAPI WebSocket + WebSocket-manager | Native async, clean API |
| **Backend** | FastAPI + asyncio + uvicorn | Best async Python, type-safe |
| **STT** | Deepgram Nova-2 | Best streaming accuracy, lowest latency |
| **LLM (primary)** | Claude Sonnet 3.5 | Best instruction following, structured output |
| **LLM (fast)** | Claude Haiku 3 | Follow-ups, compression, cheap |
| **TTS (primary)** | ElevenLabs Turbo v2 | Best voice quality for interviews |
| **TTS (fallback)** | OpenAI TTS-1 | Reliable, good quality |
| **Task Queue** | Celery + Redis | Simple, mature, FastAPI-friendly |
| **Database** | PostgreSQL 16 | JSONB, partitioning, full text search |
| **Cache / Session** | Redis 7 | Pub/Sub, Streams, session store |
| **Object Storage** | AWS S3 + KMS | Audio, transcripts, reports |
| **Cloud** | AWS ECS Fargate (MVP) → EKS | Managed, auto-scale, no k8s ops burden at start |
| **CDN** | AWS CloudFront | Audio delivery, Next.js static assets |
| **Monitoring** | Datadog (APM + Logs + Metrics) | All-in-one, excellent WS tracing |
| **Error Tracking** | Sentry | Best DX, session replay |
| **CI/CD** | GitHub Actions | Native, free for public repos |
| **Auth** | JWT (RS256) + HttpOnly cookies | Secure, stateless, WebSocket-compatible |

### Architecture Decision Log

| Decision | Chosen | Rejected | Reason |
|---|---|---|---|
| Backend language | Python / FastAPI | Node.js / TypeScript | Better async audio libs, Celery ecosystem, LLM SDK quality |
| Realtime protocol | WebSocket | WebRTC | WebRTC adds STUN/TURN complexity, not needed for audio streaming |
| STT | Deepgram | Whisper self-hosted | Hosting Whisper on GPU costs $300+/mo; Deepgram is cheaper below 10k hrs/mo |
| LLM | Claude | OpenAI / Gemini | Best structured output compliance, XML parsing reliability |
| TTS | ElevenLabs | AWS Polly | Voice quality is noticeable to candidates — worth the 20x cost |
| Session state | Redis | Postgres | Sub-millisecond reads needed per turn, not possible with DB |
| Infra | ECS Fargate | Kubernetes | No k8s ops needed at MVP scale; migrate to EKS at 500+ concurrent |
| Queue | Celery + Redis | SQS / Kafka | SQS adds per-message cost + latency; Kafka is overengineered at this scale |
| Observability | Datadog | Self-hosted ELK | ELK setup takes a week; Datadog is day-1 value |

### Final Cost Summary

| Scale | Infra/mo | API/mo | Total/mo | Cost per Interview |
|---|---|---|---|---|
| Dev/Test | $20 | $50 | $70 | - |
| 100/day | $150 | $900 | $1,050 | $0.35 |
| 1,000/day | $2,500 | $9,000 | $11,500 | $0.38 |
| 10,000/day | $12,000 | $90,000 | $102,000 | $0.34 |

**Pricing to candidates**: At $0.38/interview cost + infra overhead, viable SaaS pricing is $1–3 per interview (3–8x margin), or $299–999/mo SaaS subscription for high-volume customers.

---

*Document version: 1.0 | Generated: 2024*
*This is a living document — update as architectural decisions evolve.*
