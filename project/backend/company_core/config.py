"""Backward-compatibility shim for YAML-backed orchestrator config and profiles."""

from __future__ import annotations

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
    _default_orchestrator_profiles,
    _default_routing_rules,
    _locate_config_file,
    _matches_keywords,
    _matches_prefix,
    get_config,
)

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
