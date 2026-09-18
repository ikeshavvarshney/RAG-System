import asyncio
import json
import logging
import re
from typing import Any

from app.core.config import settings
from app.core.gemini_client import GeminiClient

logger = logging.getLogger(__name__)

_client = GeminiClient()

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_JSON_SPAN = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


async def generate(stage: str, prompt: str) -> str:
    return await asyncio.to_thread(_client.generate, stage, settings.QUERY_MODEL, prompt)


async def generate_json(stage: str, prompt: str) -> Any | None:
    try:
        text = await generate(stage, prompt)
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
