"""Usage value object domain model."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Usage:
    """Token economy and cost telemetry value object."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None
    # Thinking tokens. A SUBSET of ``completion_tokens`` (already counted there), reported
    # separately so the UI can break the output down.
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: Usage) -> Usage:
        cost = None
        if self.cost_usd is not None or other.cost_usd is not None:
            cost = (self.cost_usd or 0.0) + (other.cost_usd or 0.0)
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            cost,
            self.reasoning_tokens + other.reasoning_tokens,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": self.cost_usd,
            "reasoning_tokens": self.reasoning_tokens,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Usage:
        cost = data.get("cost_usd")
        return cls(
            int(data.get("prompt_tokens", 0) or 0),
            int(data.get("completion_tokens", 0) or 0),
            float(cost) if cost is not None else None,
            int(data.get("reasoning_tokens", 0) or 0),
        )
