import logging
from collections import defaultdict
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Bound by the query chain for the duration of one request. The tracker forwards every recorded call to it,
# so per-request usage reaches the emitter without call sites knowing about requests. Worker threads
# started with asyncio.to_thread inherit the binding.
usage_sink: ContextVar[Callable[[str, str, int, int], None] | None] = ContextVar("usage_sink", default=None)


@dataclass
class UsageEntry:
    stage: str
    model: str
    prompt_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens


class UsageTracker:
    """Accumulates token usage per request, attributed by stage."""

    def __init__(self):
        self._entries: list[UsageEntry] = []

    def record(self, stage: str, model: str, prompt_tokens: int, output_tokens: int) -> None:
        self._entries.append(
            UsageEntry(
                stage=stage,
                model=model,
                prompt_tokens=prompt_tokens,
                output_tokens=output_tokens,
            )
        )
        sink = usage_sink.get()
        if sink is not None:
            try:
                sink(stage, model, prompt_tokens, output_tokens)
            except Exception:  # noqa: BLE001 - reporting usage must never fail a model call
                logger.warning("usage sink failed", exc_info=True)

    def request_count(self, stage: str | None = None) -> int:
        return sum(1 for entry in self._entries if stage is None or entry.stage == stage)

    def total_tokens(self) -> int:
        return sum(entry.total_tokens for entry in self._entries)

    def by_stage(self) -> dict:
        totals = defaultdict(int)
        for entry in self._entries:
            totals[entry.stage] += entry.total_tokens
        return dict(totals)