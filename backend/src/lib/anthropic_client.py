import os
import anthropic
from functools import lru_cache


@lru_cache(maxsize=1)
def get_anthropic_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set")
    return anthropic.Anthropic(api_key=api_key)


def get_model_for_task(task: str) -> str:
    models = {
        "interview": "claude-sonnet-4-6",
        "evaluation": "claude-sonnet-4-6",
        "follow_up": "claude-haiku-4-5-20251001",
        "compression": "claude-haiku-4-5-20251001",
    }
    return models.get(task, "claude-sonnet-4-6")
