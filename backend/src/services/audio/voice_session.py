"""
Voice session state management in Redis.

Namespace: voice_session:{session_id} — Hash, TTL 4hr
Lock:       voice_session:{session_id}:lock — String, TTL 30s
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

VOICE_SESSION_TTL = 14400  # 4 hours
LOCK_TTL = 30

_redis_client = None
_use_memory_fallback = False


def _client():
    """Return synchronous Redis client (or None for in-memory fallback)."""
    global _redis_client, _use_memory_fallback
    if _use_memory_fallback:
        return None
    if _redis_client is not None:
        return _redis_client
    import redis as _redis  # type: ignore[import-untyped]
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        c = _redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
        c.ping()
        _redis_client = c
        logger.info("Voice sessions connected to Redis at %s", url)
        return _redis_client
    except Exception as exc:
        logger.warning("Redis unavailable for voice sessions (%s), using in-memory store", exc)
        _use_memory_fallback = True
        return None


_MEMORY: dict[str, dict[str, Any]] = {}


def _key(session_id: str) -> str:
    return f"voice_session:{session_id}"


def _lock_key(session_id: str) -> str:
    return f"voice_session:{session_id}:lock"


def create_voice_session(
    session_id: str,
    candidate_name: str,
    job_role: str,
    experience_level: str,
    required_skills: list[str],
    questions_json: str = "[]",
) -> dict[str, Any]:
    """Create initial voice session hash in Redis."""
    now = datetime.now(timezone.utc).isoformat()

    questions = json.loads(questions_json)
    transcript: list[dict[str, str]] = []
    initial_state = "INITIALIZING"

    if questions:
        first_q_text = questions[0].get("question_text", "")
        if first_q_text:
            transcript.append({
                "speaker": "bot",
                "text": first_q_text,
                "timestamp": now,
                "type": "question",
            })
            initial_state = "WAITING_FOR_CANDIDATE"

    data: dict[str, Any] = {
        "state": initial_state,
        "candidate_name": candidate_name,
        "job_role": job_role,
        "experience_level": experience_level,
        "required_skills": json.dumps(required_skills),
        "questions": questions_json,
        "current_question_idx": 0,
        "follow_up_count": 0,
        "running_scores": json.dumps({}),
        "transcript": json.dumps(transcript),
        "started_at": now,
        "turn_count": 0,
        "barge_in_count": 0,
        "silence_strikes": 0,
        "connection_state": "connected",
    }
    client = _client()
    if client:
        client.hset(_key(session_id), mapping=data)
        client.expire(_key(session_id), VOICE_SESSION_TTL)
    else:
        _MEMORY[session_id] = dict(data)
    return data


def get_voice_session(session_id: str) -> Optional[dict[str, Any]]:
    """Rehydrate full session state from Redis."""
    client = _client()
    if client:
        raw = client.hgetall(_key(session_id))
        return raw if raw else None
    return _MEMORY.get(session_id)


def set_voice_field(session_id: str, field: str, value: Any) -> None:
    client = _client()
    if client:
        client.hset(_key(session_id), field, value)
        client.expire(_key(session_id), VOICE_SESSION_TTL)
    elif session_id in _MEMORY:
        _MEMORY[session_id][field] = value


def increment_voice_field(session_id: str, field: str, amount: int = 1) -> int:
    client = _client()
    if client:
        result = int(client.hincrby(_key(session_id), field, amount))
        client.expire(_key(session_id), VOICE_SESSION_TTL)
        return result
    if session_id in _MEMORY:
        current = int(_MEMORY[session_id].get(field, 0))
        _MEMORY[session_id][field] = current + amount
        return current + amount
    return amount


def append_transcript_turn(session_id: str, speaker: str, text: str, entry_type: str = "candidate") -> None:
    client = _client()
    if client:
        raw = client.hget(_key(session_id), "transcript") or "[]"
    elif session_id in _MEMORY:
        raw = _MEMORY[session_id].get("transcript", "[]")
    else:
        raw = "[]"

    turns: list = json.loads(raw)
    turns.append({
        "speaker": speaker,
        "text": text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": entry_type,
    })
    serialized = json.dumps(turns)

    if client:
        client.hset(_key(session_id), "transcript", serialized)
    elif session_id in _MEMORY:
        _MEMORY[session_id]["transcript"] = serialized


def pause_voice_session(session_id: str) -> None:
    """On client disconnect — pause but preserve state."""
    set_voice_field(session_id, "connection_state", "paused")


def resume_voice_session(session_id: str) -> None:
    set_voice_field(session_id, "connection_state", "connected")


def acquire_lock(session_id: str) -> bool:
    client = _client()
    if client:
        result = client.set(_lock_key(session_id), "1", nx=True, ex=LOCK_TTL)
        return result is not None
    return True


def release_lock(session_id: str) -> None:
    client = _client()
    if client:
        client.delete(_lock_key(session_id))
