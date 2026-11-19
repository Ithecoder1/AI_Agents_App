"""Domain-aligned data models and strict JSON schema contracts for cascading intent triage."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

TriageIntent = Literal["DIRECT_QA", "CODE_TEST_FIX", "ARCHITECT_PIPELINE"]
TargetModelTier = Literal["fast", "reasoning"]
TriageTier = Literal[1, 2, 3]

VALID_TRIAGE_INTENTS: tuple[TriageIntent, ...] = (
    "DIRECT_QA",
    "CODE_TEST_FIX",
    "ARCHITECT_PIPELINE",
)
VALID_TARGET_MODEL_TIERS: tuple[TargetModelTier, ...] = ("fast", "reasoning")
_REQUIRED_JSON_KEYS = frozenset(
    {
        "intent",
        "confidence",
        "requires_workspace_manifest",
        "target_model_tier",
        "token_budget",
    }
)
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ResourceAllocation:
    """Default execution resource allocation bound to a resolved triage intent."""

    requires_workspace_manifest: bool
    target_model_tier: TargetModelTier
    token_budget: int


DEFAULT_RESOURCE_ALLOCATION: dict[TriageIntent, ResourceAllocation] = {
    "DIRECT_QA": ResourceAllocation(
        requires_workspace_manifest=False,
        target_model_tier="fast",
        token_budget=512,
    ),
    "CODE_TEST_FIX": ResourceAllocation(
        requires_workspace_manifest=True,
        target_model_tier="reasoning",
        token_budget=2048,
    ),
    "ARCHITECT_PIPELINE": ResourceAllocation(
        requires_workspace_manifest=True,
        target_model_tier="reasoning",
        token_budget=4096,
    ),
}


@dataclass(frozen=True, slots=True)
class SemanticRoute:
    """Canonical route definition with representative sample utterances for Tier 2 embedding."""

    intent: TriageIntent
    utterances: tuple[str, ...]
    description: str = ""

    def __post_init__(self) -> None:
        if self.intent not in VALID_TRIAGE_INTENTS:
            raise ValueError(f"Unsupported triage intent: {self.intent!r}")
        if not self.utterances:
            raise ValueError(f"SemanticRoute {self.intent!r} must define at least one utterance")


@dataclass(frozen=True, slots=True)
class TriageDecision:
    """Immutable classification decision emitted by the 3-tier cascading triage pipeline."""

    intent: TriageIntent
    confidence: float
    requires_workspace_manifest: bool
    target_model_tier: TargetModelTier
    token_budget: int
    tier: TriageTier
    latency_ms: float = 0.0
    matched_route: str | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.intent not in VALID_TRIAGE_INTENTS:
            raise ValueError(f"Invalid triage intent: {self.intent!r}")
        if math.isnan(self.confidence) or not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be within [0.0, 1.0], got {self.confidence!r}")
        if self.target_model_tier not in VALID_TARGET_MODEL_TIERS:
            raise ValueError(f"Invalid target_model_tier: {self.target_model_tier!r}")
        if self.token_budget <= 0:
            raise ValueError(f"token_budget must be positive, got {self.token_budget!r}")
        if self.tier not in (1, 2, 3):
            raise ValueError(f"tier must be 1, 2, or 3, got {self.tier!r}")

    def to_dict(self) -> dict[str, Any]:
        """Serialize strictly to the canonical 5-field intent & resource allocation schema."""
        return {
            "intent": self.intent,
            "confidence": round(float(self.confidence), 4),
            "requires_workspace_manifest": self.requires_workspace_manifest,
            "target_model_tier": self.target_model_tier,
            "token_budget": int(self.token_budget),
        }

    def to_telemetry_dict(self) -> dict[str, Any]:
        """Serialize full decision including cascading tier and latency diagnostics."""
        payload = self.to_dict()
        payload["tier"] = self.tier
        payload["latency_ms"] = round(float(self.latency_ms), 3)
        payload["matched_route"] = self.matched_route
        payload["reason"] = self.reason
        return payload

    @classmethod
    def from_json(
        cls,
        raw: str | Mapping[str, Any],
        *,
        tier: TriageTier = 3,
        latency_ms: float = 0.0,
        matched_route: str | None = None,
        reason: str = "",
    ) -> TriageDecision:
        """Parse and strictly validate a JSON payload against the triage output schema."""
        if isinstance(raw, str):
            text = raw.strip()
            fence_match = _JSON_FENCE_RE.match(text)
            if fence_match:
                text = fence_match.group(1).strip()
            try:
                parsed: Any = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid triage JSON payload: {exc}") from exc
        elif isinstance(raw, Mapping):
            parsed = dict(raw)
        else:
            raise ValueError(f"Expected JSON string or mapping, got {type(raw).__name__}")

        if not isinstance(parsed, dict):
            raise ValueError("Triage JSON payload must be a JSON object")

        keys = set(parsed.keys())
        missing = _REQUIRED_JSON_KEYS - keys
        if missing:
            raise ValueError(f"Missing required triage JSON keys: {sorted(missing)}")
        extra = keys - _REQUIRED_JSON_KEYS
        if extra:
            raise ValueError(f"Unexpected extra triage JSON keys: {sorted(extra)}")

        intent = parsed["intent"]
        if not isinstance(intent, str) or intent not in VALID_TRIAGE_INTENTS:
            raise ValueError(f"Invalid intent value: {intent!r}")

        confidence = parsed["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError(f"confidence must be a float, got {confidence!r}")

        requires_manifest = parsed["requires_workspace_manifest"]
        if not isinstance(requires_manifest, bool):
            raise ValueError(
                f"requires_workspace_manifest must be a bool, got {requires_manifest!r}"
            )

        target_tier = parsed["target_model_tier"]
        if not isinstance(target_tier, str) or target_tier not in VALID_TARGET_MODEL_TIERS:
            raise ValueError(f"Invalid target_model_tier value: {target_tier!r}")

        token_budget = parsed["token_budget"]
        if isinstance(token_budget, bool) or not isinstance(token_budget, int):
            raise ValueError(f"token_budget must be an integer, got {token_budget!r}")

        return cls(
            intent=intent,
            confidence=float(confidence),
            requires_workspace_manifest=requires_manifest,
            target_model_tier=target_tier,
            token_budget=token_budget,
            tier=tier,
            latency_ms=latency_ms,
            matched_route=matched_route,
            reason=reason,
        )


__all__ = [
    "DEFAULT_RESOURCE_ALLOCATION",
    "VALID_TARGET_MODEL_TIERS",
    "VALID_TRIAGE_INTENTS",
    "ResourceAllocation",
    "SemanticRoute",
    "TargetModelTier",
    "TriageDecision",
    "TriageIntent",
    "TriageTier",
]
