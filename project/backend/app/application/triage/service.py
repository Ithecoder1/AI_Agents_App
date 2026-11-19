"""Cascading 3-tier intent triage service and warm-bootstrapping helpers."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import replace

from app.application.triage.heuristics import Tier1HeuristicFilter
from app.application.triage.micro_llm import (
    GeminiMicroLLMClient,
    IMicroLLMClient,
    Tier3MicroLLMClassifier,
)
from app.application.triage.models import TriageDecision, TriageIntent
from app.application.triage.semantic_router import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    LocalCpuSemanticRouter,
)


def _default_micro_llm_client() -> IMicroLLMClient | None:
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        return GeminiMicroLLMClient()
    return None


class CascadingTriageService:
    """CPU-optimized 3-tier cascading intent triage pipeline.

    Tier 1: Deterministic zero-token heuristics (empty, slash commands, greetings, math).
    Tier 2: Local CPU semantic router (all-MiniLM-L6-v2 / compatible CPU embedder).
    Tier 3: Remote Micro-LLM zero-shot JSON fallback with strict context hygiene.
    """

    def __init__(
        self,
        *,
        tier1_filter: Tier1HeuristicFilter | None = None,
        semantic_router: LocalCpuSemanticRouter | None = None,
        micro_llm: Tier3MicroLLMClassifier | None = None,
        micro_llm_client: IMicroLLMClient | None = None,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        auto_warmup: bool = True,
    ) -> None:
        self._tier1 = tier1_filter or Tier1HeuristicFilter()
        self._tier2 = semantic_router or LocalCpuSemanticRouter(
            confidence_threshold=confidence_threshold
        )
        if micro_llm is not None:
            self._tier3 = micro_llm
        else:
            client = (
                micro_llm_client if micro_llm_client is not None else _default_micro_llm_client()
            )
            self._tier3 = Tier3MicroLLMClassifier(client=client)

        if auto_warmup:
            self.warmup()

    @property
    def is_warmed(self) -> bool:
        return self._tier2.is_warmed

    @property
    def tier1_filter(self) -> Tier1HeuristicFilter:
        return self._tier1

    @property
    def semantic_router(self) -> LocalCpuSemanticRouter:
        return self._tier2

    @property
    def micro_llm(self) -> Tier3MicroLLMClassifier:
        return self._tier3

    def warmup(self) -> None:
        """Load local CPU embedding model and pre-compute route vectors once at startup."""
        self._tier2.warmup()

    def score_routes(self, user_prompt: str) -> dict[TriageIntent, float]:
        """Return Tier 2 cosine similarity scores across all pre-defined routes."""
        return self._tier2.score_routes(user_prompt)

    def triage(self, user_prompt: str) -> TriageDecision:
        """Classify a raw user prompt through Tiers 1 -> 2 -> 3 with zero workspace I/O."""
        t0 = time.perf_counter()

        # Tier 1: Deterministic Zero-Token Heuristics
        tier1_decision = self._tier1.evaluate(user_prompt)
        if tier1_decision is not None:
            return replace(tier1_decision, latency_ms=(time.perf_counter() - t0) * 1000.0)

        # Tier 2: Local CPU Semantic Router
        tier2_decision = self._tier2.evaluate(user_prompt)
        if tier2_decision is not None:
            return replace(tier2_decision, latency_ms=(time.perf_counter() - t0) * 1000.0)

        # Tier 3: Micro-LLM Fallback (raw user prompt only; zero workspace files)
        tier3_decision = self._tier3.classify(user_prompt)
        return replace(tier3_decision, latency_ms=(time.perf_counter() - t0) * 1000.0)

    def classify(self, user_prompt: str) -> TriageDecision:
        """Alias for :meth:`triage`."""
        return self.triage(user_prompt)


_SINGLETON_LOCK = threading.Lock()
_GLOBAL_TRIAGE_SERVICE: CascadingTriageService | None = None


def bootstrap_triage_service(
    *,
    force: bool = False,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    micro_llm_client: IMicroLLMClient | None = None,
) -> CascadingTriageService:
    """Warm-bootstrap and cache the global ``CascadingTriageService`` instance."""
    global _GLOBAL_TRIAGE_SERVICE
    with _SINGLETON_LOCK:
        if _GLOBAL_TRIAGE_SERVICE is None or force:
            service = CascadingTriageService(
                confidence_threshold=confidence_threshold,
                micro_llm_client=micro_llm_client,
                auto_warmup=True,
            )
            _GLOBAL_TRIAGE_SERVICE = service
        else:
            _GLOBAL_TRIAGE_SERVICE.warmup()
        return _GLOBAL_TRIAGE_SERVICE


def get_triage_service() -> CascadingTriageService:
    """Return the warm-bootstrapped global ``CascadingTriageService`` singleton."""
    if _GLOBAL_TRIAGE_SERVICE is not None:
        return _GLOBAL_TRIAGE_SERVICE
    return bootstrap_triage_service()


__all__ = [
    "CascadingTriageService",
    "bootstrap_triage_service",
    "get_triage_service",
]
