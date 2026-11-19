"""CPU-optimized 3-tier cascading intent triage pipeline and compatibility exports."""

from __future__ import annotations

from app.application.triage.heuristics import (
    EXACT_SLASH_COMMANDS,
    LIGHTWEIGHT_SYSTEM_INSTRUCTIONS,
    Tier1HeuristicFilter,
    is_lightweight_query,
    should_load_workspace_context,
)
from app.application.triage.micro_llm import (
    LOCKED_MICRO_LLM_SYSTEM_PROMPT,
    CallableMicroLLMClient,
    GeminiMicroLLMClient,
    IMicroLLMClient,
    ProviderMicroLLMClient,
    Tier3MicroLLMClassifier,
)
from app.application.triage.models import (
    DEFAULT_RESOURCE_ALLOCATION,
    VALID_TARGET_MODEL_TIERS,
    VALID_TRIAGE_INTENTS,
    ResourceAllocation,
    SemanticRoute,
    TargetModelTier,
    TriageDecision,
    TriageIntent,
    TriageTier,
)
from app.application.triage.semantic_router import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_MODEL_NAME,
    DEFAULT_PLATT_CALIBRATOR,
    DEFAULT_SEMANTIC_ROUTES,
    HuggingFaceEncoder,
    LocalCpuSemanticRouter,
    Route,
    RouteLayer,
)
from app.application.triage.service import (
    CascadingTriageService,
    bootstrap_triage_service,
    get_triage_service,
)

__all__ = [
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "DEFAULT_MODEL_NAME",
    "DEFAULT_PLATT_CALIBRATOR",
    "DEFAULT_RESOURCE_ALLOCATION",
    "DEFAULT_SEMANTIC_ROUTES",
    "EXACT_SLASH_COMMANDS",
    "LIGHTWEIGHT_SYSTEM_INSTRUCTIONS",
    "LOCKED_MICRO_LLM_SYSTEM_PROMPT",
    "VALID_TARGET_MODEL_TIERS",
    "VALID_TRIAGE_INTENTS",
    "CallableMicroLLMClient",
    "CascadingTriageService",
    "GeminiMicroLLMClient",
    "HuggingFaceEncoder",
    "IMicroLLMClient",
    "LocalCpuSemanticRouter",
    "ProviderMicroLLMClient",
    "ResourceAllocation",
    "Route",
    "RouteLayer",
    "SemanticRoute",
    "TargetModelTier",
    "Tier1HeuristicFilter",
    "Tier3MicroLLMClassifier",
    "TriageDecision",
    "TriageIntent",
    "TriageTier",
    "bootstrap_triage_service",
    "get_triage_service",
    "is_lightweight_query",
    "should_load_workspace_context",
]
