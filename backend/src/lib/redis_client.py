import os
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_redis_client = None
_use_memory_fallback = False
_memory_store: dict = {}


def _get_redis():
    global _redis_client, _use_memory_fallback
    if _use_memory_fallback:
        return None
    if _redis_client is not None:
        return _redis_client
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        import redis
        client = redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2)
        client.ping()
        _redis_client = client
        logger.info("Connected to Redis at %s", redis_url)
        return _redis_client
    except Exception as exc:
        logger.warning("Redis unavailable (%s), using in-memory store", exc)
        _use_memory_fallback = True
        return None


def set_json(key: str, value: dict, ttl_seconds: int = 14400) -> None:
    client = _get_redis()
    if client:
        client.setex(key, ttl_seconds, json.dumps(value))
    else:
        _memory_store[key] = value


def get_json(key: str) -> Optional[dict]:
    client = _get_redis()
    if client:
        raw = client.get(key)
        return json.loads(raw) if raw else None
    return _memory_store.get(key)


def delete_key(key: str) -> None:
    client = _get_redis()
    if client:
        client.delete(key)
    else:
        _memory_store.pop(key, None)


def key_exists(key: str) -> bool:
    client = _get_redis()
    if client:
        return bool(client.exists(key))
    return key in _memory_store
