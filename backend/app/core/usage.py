from collections import defaultdict
from dataclasses import dataclass, field


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

    def total_tokens(self) -> int:
        return sum(entry.total_tokens for entry in self._entries)

    def by_stage(self) -> dict:
        totals = defaultdict(int)
        for entry in self._entries:
            totals[entry.stage] += entry.total_tokens
        return dict(totals)