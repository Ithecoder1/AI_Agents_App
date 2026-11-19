"""Tier 2: Local CPU Semantic Router powered by the official ``semantic-router`` package."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from semantic_router import IndexedRouter, Route
from semantic_router.calibration import Calibrator
from semantic_router.encoders import DenseEncoder, HuggingFaceEncoder
from semantic_router.schema import RouteChoice

from app.application.triage.models import (
    DEFAULT_RESOURCE_ALLOCATION,
    VALID_TRIAGE_INTENTS,
    TriageDecision,
    TriageIntent,
)

DEFAULT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_CONFIDENCE_THRESHOLD = 0.82

# Platt calibration mapping all-MiniLM-L6-v2 cosine scores (~0.38+ in-domain, <0.28 OOD)
# to calibrated probabilities aligned with the 0.82 confidence threshold.
DEFAULT_PLATT_CALIBRATOR = Calibrator(method="platt", params={"a": [16.0], "b": [-4.5]})

DEFAULT_SEMANTIC_ROUTES: tuple[Route, ...] = (
    Route(
        name="DIRECT_QA",
        description="Informational questions, explanations, and conceptual comparisons",
        utterances=[
            "How do I reverse a list in Python?",
            "What is the difference between synchronous and asynchronous code?",
            "Explain what this function does.",
        ],
    ),
    Route(
        name="CODE_TEST_FIX",
        description="Code inspection, refactoring, bug fixes, optimization, and test repair",
        utterances=[
            "Inspect my code and refactor it.",
            "Fix the failing pytest in the auth module.",
            "Optimize this function and make the tests pass.",
        ],
    ),
    Route(
        name="ARCHITECT_PIPELINE",
        description="Full feature scaffolding, system architecture, migrations, and endpoints",
        utterances=[
            "Build a new payment gateway module from scratch.",
            "Create a new database migration and REST endpoint for user profiles.",
            "Design and scaffold the service layer for notifications.",
        ],
    ),
)

_ENCODER_LOCK = threading.Lock()
_CACHED_ENCODERS: dict[tuple[str, str, int], HuggingFaceEncoder] = {}
_UNSET = object()


def _local_hf_hub_dir(model_name: str) -> Path | None:
    """Locate workspace HuggingFace hub cache if the requested model is cached locally."""
    cache_dir = Path(__file__).resolve().parents[4] / ".cache" / "huggingface"
    hub_dir = cache_dir / "hub"
    model_slug = f"models--{model_name.replace('/', '--')}"
    if (hub_dir / model_slug).is_dir():
        os.environ.setdefault("HF_HOME", str(cache_dir))
        os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(cache_dir))
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        return hub_dir
    return None


def _get_or_create_cpu_encoder(
    *,
    model_name: str = DEFAULT_MODEL_NAME,
    device: str = "cpu",
    num_threads: int = 2,
) -> HuggingFaceEncoder:
    if device != "cpu":
        raise ValueError(f"Tier 2 semantic router requires device='cpu', got {device!r}")

    cache_key = (model_name, device, max(1, int(num_threads)))
    with _ENCODER_LOCK:
        cached = _CACHED_ENCODERS.get(cache_key)
        if cached is not None:
            return cached

        import torch

        torch.set_num_threads(cache_key[2])
        hub_dir = _local_hf_hub_dir(model_name)
        if hub_dir is not None:
            local_kwargs = {"cache_dir": str(hub_dir), "local_files_only": True}
            encoder = HuggingFaceEncoder(  # type: ignore[no-untyped-call]
                name=model_name,
                device="cpu",
                tokenizer_kwargs=local_kwargs,
                model_kwargs=local_kwargs,
            )
        else:
            encoder = HuggingFaceEncoder(  # type: ignore[no-untyped-call]
                name=model_name,
                device="cpu",
            )
        model = getattr(encoder, "_model", None)
        if model is not None and callable(getattr(model, "eval", None)):
            model.eval()
        _CACHED_ENCODERS[cache_key] = encoder
        return encoder


class RouteLayer(IndexedRouter):
    """Synchronous CPU-safe wrapper around ``semantic_router.IndexedRouter``."""

    def score_query(self, query: str) -> dict[str, float]:
        """Compute raw route cosine similarities against pre-indexed route vectors."""
        if not self._cache or not self.index.is_ready():
            return {}
        dense = self.encoder([query])[0]
        scores, names = self.index.query(vector=np.asarray(dense), top_k=self.top_k)
        hits: dict[str, list[float]] = {name: [] for name in self._cache}
        for name, score in zip(names, scores, strict=True):
            hits.setdefault(name, []).append(float(score))
        return {name: self.aggregate(vals) if vals else 0.0 for name, vals in hits.items()}

    def score_probs(self, query: str) -> dict[str, float]:
        """Compute calibrated confidence scores for every configured route."""
        raw_scores = self.score_query(query)
        if not raw_scores:
            return {}
        return self._probs(raw_scores)

    def __call__(
        self,
        query: str,
        limit: int | None = 1,
        threshold: float | None = None,
    ) -> RouteChoice | list[RouteChoice]:
        return self._choose(self.score_query(query), limit, threshold)


class LocalCpuSemanticRouter:
    """Tier 2 local CPU semantic router backed by ``semantic_router`` + ``all-MiniLM-L6-v2``."""

    def __init__(
        self,
        routes: Sequence[Route] = DEFAULT_SEMANTIC_ROUTES,
        *,
        encoder: DenseEncoder | None = None,
        model_name: str = DEFAULT_MODEL_NAME,
        device: str = "cpu",
        num_threads: int = 2,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        calibrator: Any = _UNSET,
    ) -> None:
        if device != "cpu":
            raise ValueError(f"Tier 2 semantic router requires device='cpu', got {device!r}")
        if not (0.0 < confidence_threshold <= 1.0):
            raise ValueError(
                f"confidence_threshold must be in (0.0, 1.0], got {confidence_threshold!r}"
            )
        self._routes: tuple[Route, ...] = tuple(routes)
        self._encoder: DenseEncoder | None = encoder
        self._model_name = model_name
        self._device = device
        self._num_threads = max(1, int(num_threads))
        self._threshold = float(confidence_threshold)
        self._calibrator: Calibrator | None = (
            DEFAULT_PLATT_CALIBRATOR if calibrator is _UNSET else calibrator
        )
        self._route_layer: RouteLayer | None = None
        self._is_warmed = False

    @property
    def confidence_threshold(self) -> float:
        return self._threshold

    @property
    def is_warmed(self) -> bool:
        return self._is_warmed

    @property
    def encoder(self) -> DenseEncoder | None:
        return self._encoder

    @property
    def route_layer(self) -> RouteLayer | None:
        return self._route_layer

    def warmup(self) -> None:
        """Initialize the CPU encoder and build the ``RouteLayer`` index once at startup."""
        if self._is_warmed:
            return
        if self._encoder is None:
            self._encoder = _get_or_create_cpu_encoder(
                model_name=self._model_name,
                device=self._device,
                num_threads=self._num_threads,
            )
        self._route_layer = RouteLayer(
            encoder=self._encoder,
            routes=list(self._routes),
            aggregation="max",
            top_k=max(5, sum(len(r.utterances) for r in self._routes)),
            confidence_threshold=self._threshold,
            calibrator=self._calibrator,
        )
        # Prime the CPU inference path once at startup so the first query avoids JIT/pool spikes.
        self._route_layer.score_query("warmup")
        self._is_warmed = True

    def score_routes(self, user_prompt: str) -> dict[TriageIntent, float]:
        """Evaluate confidence scores across all configured routes."""
        if not self._is_warmed or self._route_layer is None:
            self.warmup()
        assert self._route_layer is not None
        raw = self._route_layer.score_probs(user_prompt)
        return {name: float(score) for name, score in raw.items() if name in VALID_TRIAGE_INTENTS}

    def evaluate(self, user_prompt: str) -> TriageDecision | None:
        """Pass ``user_prompt`` through ``RouteLayer``; return ``None`` if unresolved."""
        t0 = time.perf_counter()
        if not self._is_warmed or self._route_layer is None:
            self.warmup()
        assert self._route_layer is not None

        raw_choice = self._route_layer(user_prompt, limit=1, threshold=self._threshold)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        choice = raw_choice[0] if isinstance(raw_choice, list) and raw_choice else raw_choice
        if not isinstance(choice, RouteChoice):
            return None
        if choice.name is None or choice.name not in VALID_TRIAGE_INTENTS:
            return None

        confidence = float(choice.confidence) if choice.confidence is not None else 0.0
        if confidence < self._threshold:
            return None

        intent = choice.name
        alloc = DEFAULT_RESOURCE_ALLOCATION[intent]
        return TriageDecision(
            intent=intent,
            confidence=min(1.0, max(0.0, confidence)),
            requires_workspace_manifest=alloc.requires_workspace_manifest,
            target_model_tier=alloc.target_model_tier,
            token_budget=alloc.token_budget,
            tier=2,
            latency_ms=elapsed_ms,
            matched_route=intent,
            reason=(
                f"Tier 2 semantic-router matched {intent} "
                f"(confidence={confidence:.3f} >= {self._threshold:.2f}, "
                f"model={self._model_name})"
            ),
        )


__all__ = [
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "DEFAULT_MODEL_NAME",
    "DEFAULT_PLATT_CALIBRATOR",
    "DEFAULT_SEMANTIC_ROUTES",
    "HuggingFaceEncoder",
    "LocalCpuSemanticRouter",
    "Route",
    "RouteLayer",
]
