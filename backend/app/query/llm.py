import asyncio
import json
import logging
import re
from typing import Any

from google.genai import types

from app.core.config import settings
from app.core.gemini_client import GeminiClient

logger = logging.getLogger(__name__)

_client = GeminiClient()

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_JSON_SPAN = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


def _config(max_output_tokens: int) -> types.GenerateContentConfig:
    # Thinking made these short calls 3-5x slower. MINIMAL, not budget 0: the Lite models reject a zero budget.
    return types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL),
        max_output_tokens=max_output_tokens,
    )


async def generate(stage: str, prompt: str, max_output_tokens: int) -> str:
    return await asyncio.to_thread(
        _client.generate, stage, settings.QUERY_MODEL, prompt, _config(max_output_tokens)
    )


async def generate_json(stage: str, prompt: str, max_output_tokens: int) -> Any | None:
    try:
        text = await generate(stage, prompt, max_output_tokens)
    except Exception:  # noqa: BLE001 - callers decide the fallback
        logger.warning("%s: LLM call failed", stage, exc_info=True)
        return None
    parsed = parse_json(text)
    if parsed is None:
        logger.warning("%s: unparseable LLM output: %.200r", stage, text)
    return parsed


def parse_json(text: str | None) -> Any | None:
    if not text:
        return None
    cleaned = _FENCE.sub("", text.strip())
    for candidate in (cleaned, *_JSON_SPAN.findall(cleaned)):
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    return None
