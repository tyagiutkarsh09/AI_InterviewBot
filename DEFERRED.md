# Deferred Features

## Voice Pipeline (Deepgram STT + ElevenLabs TTS)
**Why deferred**: Requires API keys for Deepgram and ElevenLabs, plus browser AudioWorklet/VAD implementation. The interview flow works fully via text input.
**Implementation path**: See `ai-interview-bot-arch.md` sections 2.1-2.7. Add `DEEPGRAM_API_KEY` and `ELEVENLABS_API_KEY` to `.env`, then implement `backend/src/lib/deepgram_client.py` and `backend/src/lib/tts_client.py`.

## WebSocket Real-time Protocol
**Why deferred**: REST API provides the full interview flow. WebSocket is needed for streaming STT transcripts and TTS audio.
**Implementation path**: Add `/ws/interview/{session_id}` endpoint in `backend/src/routes/websocket.py`. Frontend `src/lib/websocket.ts` stub is ready.

## PostgreSQL Persistence
**Why deferred**: Sessions use in-memory storage for the MVP. Data survives process restarts if Redis is configured.
**Implementation path**: Run `docker-compose up db`, set `DATABASE_URL` in `.env`. Uncomment DB usage in `backend/src/lib/database.py`.

## Audio Recording to S3
**Why deferred**: Requires AWS credentials and ffmpeg.
**Implementation path**: Set `AWS_*` env vars. See arch section 6.4.

## JWT Authentication
**Why deferred**: All endpoints are open for the MVP demo. Auth middleware stub is in place.
**Implementation path**: Set `JWT_SECRET` env var, enable `verify_token` dependency in routes.

## PDF Report Export
**Why deferred**: Report is rendered in-browser. PDF generation requires `weasyprint` or a headless browser.

## Voice Pipeline Dependencies (install before running voice features)

**Backend:**
- `pip install deepgram-sdk` — Deepgram STT client
- `pip install openai` — OpenAI TTS fallback (optional)
- Add to `backend/requirements.txt`: `deepgram-sdk>=3.0.0`

**Frontend:**
- `cd frontend && npm install onnxruntime-web` — Silero VAD ONNX runtime
- **Silero VAD model**: Download `silero_vad.onnx` → `frontend/public/models/silero_vad.onnx`
  Source: https://github.com/snakers4/silero-vad (use the ONNX export)
- **ONNX WASM files**: Copy `ort-wasm*.wasm` from `node_modules/onnxruntime-web/dist/` → `frontend/public/onnx/`
- Note: VAD falls back to energy-based detection if ONNX model is unavailable

## Celery Background Workers
**Why deferred**: Transcript finalization and scoring run synchronously for MVP.
**Implementation path**: Add `celery_app.py`, define tasks in `backend/src/workers/`.
