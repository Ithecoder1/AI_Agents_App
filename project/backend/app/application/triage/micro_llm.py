"""Tier 3: Micro-LLM zero-shot JSON fallback classifier with strict context hygiene."""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
import os
import re
import time
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from app.application.triage.models import (
    DEFAULT_RESOURCE_ALLOCATION,
    TriageDecision,
    TriageIntent,
)
from app.domain.enums import ExecutionMode
from app.domain.models import ExecutionParams, ExecutionTask, Prompt
from app.domain.ports.agent_provider import IAgentProvider

LOCKED_MICRO_LLM_SYSTEM_PROMPT = (
    "You are a zero-context intent triage classifier. "
    "Classify the user prompt into exactly one intent: "
    '"DIRECT_QA", "CODE_TEST_FIX", or "ARCHITECT_PIPELINE".\n'
    "Rules:\n"
    '- "DIRECT_QA": conceptual questions, code explanations, greetings, or general Q&A '
    '(requires_workspace_manifest=false, target_model_tier="fast", token_budget=512).\n'
    '- "CODE_TEST_FIX": inspecting, debugging, refactoring, optimizing, or fixing existing '
    'code/tests (requires_workspace_manifest=true, target_model_tier="reasoning", '
    "token_budget=2048).\n"
    '- "ARCHITECT_PIPELINE": designing, scaffolding, or building new modules, migrations, '
    "endpoints, or services from scratch (requires_workspace_manifest=true, "
    'target_model_tier="reasoning", token_budget=4096).\n\n'
    "Output ONLY a single valid JSON object matching this exact schema (no markdown, no prose):\n"
    "{\n"
    '  "intent": "DIRECT_QA" | "CODE_TEST_FIX" | "ARCHITECT_PIPELINE",\n'
    '  "confidence": <float between 0.0 and 1.0>,\n'
    '  "requires_workspace_manifest": <boolean>,\n'
    '  "target_model_tier": "fast" | "reasoning",\n'
    '  "token_budget": <positive integer>\n'
    "}"
)

_FALLBACK_QA_RE = re.compile(
    r"^\s*(what|why|how|who|when|where|explain|describe|define|tell me)\b",
    re.IGNORECASE,
)
_FALLBACK_FIX_RE = re.compile(
    r"\b(fix|bug|error|fail|failing|pytest|test|tests|refactor|clean up|optimize|debug|patch)\b",
    re.IGNORECASE,
)
_FALLBACK_ARCH_RE = re.compile(
    r"\b(build|create|scaffold|design|architect|migration|endpoint|service|pipeline|"
    r"from scratch|solve|evaluator|calculator)\b",
    re.IGNORECASE,
)


@runtime_checkable
class IMicroLLMClient(Protocol):
    """Minimal raw-prompt-only client interface for Tier 3 micro-classification."""

    def classify(self, system_prompt: str, user_prompt: str) -> str:
        """Send ONLY system_prompt and raw user_prompt and return raw JSON text."""
        ...


class CallableMicroLLMClient:
    """Adapter wrapping a plain ``(system_prompt, user_prompt) -> str`` callable."""

    def __init__(self, fn: Callable[[str, str], str]) -> None:
        self._fn = fn

    def classify(self, system_prompt: str, user_prompt: str) -> str:
        return self._fn(system_prompt, user_prompt)


class GeminiMicroLLMClient:
    """Official ``google-genai`` Micro-LLM client targeting Gemini Flash-Lite / Flash."""

    def __init__(
        self,
        *,
        model: str = "gemini-2.5-flash-lite",
        api_key: str | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self.timeout_s = timeout_s
        self._client: Any = None

    def _get_api_key(self) -> str | None:
        return self._api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    def is_configured(self) -> bool:
        return bool(self._get_api_key())

    def classify(self, system_prompt: str, user_prompt: str) -> str:
        api_key = self._get_api_key()
        if not api_key:
            raise RuntimeError("No GEMINI_API_KEY or GOOGLE_API_KEY configured for Tier 3 triage")

        genai = importlib.import_module("google.genai")
        types_mod = importlib.import_module("google.genai.types")

        if self._client is None:
            self._client = genai.Client(api_key=api_key)

        config = types_mod.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.0,
            max_output_tokens=160,
            response_mime_type="application/json",
        )
        response = self._client.models.generate_content(
            model=self.model,
            contents=user_prompt,
            config=config,
        )
        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Gemini Micro-LLM returned an empty classification response")
        return text.strip()


class ProviderMicroLLMClient:
    """Adapter wrapping any domain ``IAgentProvider`` with strict zero-context hygiene."""

    def __init__(
        self,
        provider: IAgentProvider,
        *,
        model: str | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        self._provider = provider
        self._model = model
        self._timeout_s = timeout_s

    def classify(self, system_prompt: str, user_prompt: str) -> str:
        # Context hygiene: zero workspace files, zero active tools, zero chat history.
        prompt = Prompt(
            user_prompt=user_prompt,
            system_instructions=system_prompt,
            literal=True,
        )
        params = ExecutionParams(
            provider_id=self._provider.config.id,
            model=self._model,
            temperature=0.0,
            max_tokens=160,
            execution_mode=ExecutionMode.DIRECT_QA,
            active_tools=(),
        )
        task = ExecutionTask.create(prompt, params, workspace=None).start(self._provider.config.id)

        async def _run() -> str:
            res = await self._provider.execute(task, active_tools=())
            return res.output_text

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(_run())).result(timeout=self._timeout_s)
        return asyncio.run(_run())


def _safe_deterministic_fallback(user_prompt: str, reason: str) -> TriageDecision:
    """Deterministic safe-fallback decision when Tier 3 remote API is unavailable or fails."""
    cleaned = user_prompt.strip()
    intent: TriageIntent = "DIRECT_QA"
    if _FALLBACK_ARCH_RE.search(cleaned):
        intent = "ARCHITECT_PIPELINE"
    elif _FALLBACK_FIX_RE.search(cleaned):
        intent = "CODE_TEST_FIX"
    elif _FALLBACK_QA_RE.search(cleaned):
        intent = "DIRECT_QA"
    else:
        intent = "DIRECT_QA"

    alloc = DEFAULT_RESOURCE_ALLOCATION[intent]
    return TriageDecision(
        intent=intent,
        confidence=0.6,
        requires_workspace_manifest=alloc.requires_workspace_manifest,
        target_model_tier=alloc.target_model_tier,
        token_budget=alloc.token_budget,
        tier=3,
        matched_route="tier3_safe_fallback",
        reason=reason,
    )


class Tier3MicroLLMClassifier:
    """Tier 3 remote micro-LLM classifier enforcing strict JSON schema and zero context bloat."""

    def __init__(
        self,
        client: IMicroLLMClient | None = None,
        *,
        system_prompt: str = LOCKED_MICRO_LLM_SYSTEM_PROMPT,
        fallback_on_error: bool = True,
    ) -> None:
        self._client = client
        self._system_prompt = system_prompt
        self._fallback_on_error = fallback_on_error

    @property
    def client(self) -> IMicroLLMClient | None:
        return self._client

    def classify(self, user_prompt: str) -> TriageDecision:
        """Invoke Tier 3 micro-LLM with ONLY the raw user prompt and parse strict JSON output."""
        t0 = time.perf_counter()
        if self._client is None:
            decision = _safe_deterministic_fallback(
                user_prompt,
                reason="Tier 3 Micro-LLM client not configured; used safe fallback",
            )
            return TriageDecision(
                intent=decision.intent,
                confidence=decision.confidence,
                requires_workspace_manifest=decision.requires_workspace_manifest,
                target_model_tier=decision.target_model_tier,
                token_budget=decision.token_budget,
                tier=3,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                matched_route=decision.matched_route,
                reason=decision.reason,
            )

        try:
            raw_json = self._client.classify(self._system_prompt, user_prompt)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return TriageDecision.from_json(
                raw_json,
                tier=3,
                latency_ms=elapsed_ms,
                matched_route="tier3_micro_llm",
                reason="Tier 3 Micro-LLM resolved ambiguous intent via strict JSON schema",
            )
        except Exception as exc:
            if not self._fallback_on_error:
                raise
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            fallback = _safe_deterministic_fallback(
                user_prompt,
                reason=f"Tier 3 Micro-LLM fallback after error: {exc}",
            )
            return TriageDecision(
                intent=fallback.intent,
                confidence=fallback.confidence,
                requires_workspace_manifest=fallback.requires_workspace_manifest,
                target_model_tier=fallback.target_model_tier,
                token_budget=fallback.token_budget,
                tier=3,
                latency_ms=elapsed_ms,
                matched_route=fallback.matched_route,
                reason=fallback.reason,
            )


__all__ = [
    "LOCKED_MICRO_LLM_SYSTEM_PROMPT",
    "CallableMicroLLMClient",
    "GeminiMicroLLMClient",
    "IMicroLLMClient",
    "ProviderMicroLLMClient",
    "Tier3MicroLLMClassifier",
]
