import os
import anthropic
from functools import lru_cache


@lru_cache(maxsize=1)
def get_anthropic_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set")
    return anthropic.Anthropic(api_key=api_key)


@lru_cache(maxsize=1)
def get_async_anthropic_client() -> anthropic.AsyncAnthropic:
    """Async client for use inside the asyncio event loop (voice pipeline).

    The synchronous client blocks the loop for the full request, stalling every
    other concurrent session; async callers must use this and ``await`` it.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set")
    return anthropic.AsyncAnthropic(api_key=api_key)


def get_model_for_task(task: str) -> str:
    models = {
        "interview": "claude-sonnet-4-6",
        "evaluation": "claude-sonnet-4-6",
        "follow_up": "claude-haiku-4-5-20251001",
        "compression": "claude-haiku-4-5-20251001",
    }
    return models.get(task, "claude-sonnet-4-6")
