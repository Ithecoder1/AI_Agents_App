"""DEPRECATED: Backward-compatibility shim for orchestrator config and profiles.

Prefer ``app.infrastructure.orchestration.OrchestratorConfigLoader`` and ``ConfigManager``.
"""

from __future__ import annotations

__deprecated__ = True

from app.infrastructure.orchestration.state.profiles import (
    AgentConfig,
    AgentPersonaConfig,
    ConfigManager,
    ContextPolicy,
    IntentRoutingRule,
    OrchestratorConfigLoader,
    OrchestratorProfileConfig,
    TokenEconomyConfig,
    WorkflowConfig,
)

get_config = OrchestratorConfigLoader.default
_default_routing_rules = OrchestratorConfigLoader.default_routing_rules
_default_orchestrator_profiles = OrchestratorConfigLoader.default_profiles
_locate_config_file = OrchestratorConfigLoader.locate_config_file
_matches_prefix = OrchestratorConfigLoader._matches_prefix
_matches_keywords = OrchestratorConfigLoader._matches_keywords

__all__ = [
    "AgentConfig",
    "AgentPersonaConfig",
    "ConfigManager",
    "ContextPolicy",
    "IntentRoutingRule",
    "OrchestratorConfigLoader",
    "OrchestratorProfileConfig",
    "TokenEconomyConfig",
    "WorkflowConfig",
    "_default_orchestrator_profiles",
    "_default_routing_rules",
    "_locate_config_file",
    "_matches_keywords",
    "_matches_prefix",
    "get_config",
]
