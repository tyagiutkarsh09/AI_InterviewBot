"""Short-lived storage for a generated interview plan draft.

The admin previews a generated plan (and can regenerate) BEFORE a session exists,
so the draft lives in Redis (1h TTL) under plan_draft:{id}. Falls back to an
in-memory dict when Redis is down, mirroring services/audio/voice_session.py.
"""
import json
import logging
import os
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

DRAFT_TTL = 3600  # 1 hour
_redis_client = None
_use_memory_fallback = False
_MEMORY: dict[str, str] = {}


def _client():
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
        return _redis_client
    except Exception as exc:
        logger.warning("Redis unavailable for plan drafts (%s), using in-memory store", exc)
        _use_memory_fallback = True
        return None


def _key(draft_id: str) -> str:
    return f"plan_draft:{draft_id}"


def save_plan_draft(payload: dict[str, Any]) -> str:
    draft_id = str(uuid.uuid4())
    blob = json.dumps(payload)
    client = _client()
    if client:
        client.set(_key(draft_id), blob, ex=DRAFT_TTL)
    else:
        _MEMORY[draft_id] = blob
    return draft_id


def get_plan_draft(draft_id: str) -> Optional[dict[str, Any]]:
    client = _client()
    blob = client.get(_key(draft_id)) if client else _MEMORY.get(draft_id)
    return json.loads(blob) if blob else None
