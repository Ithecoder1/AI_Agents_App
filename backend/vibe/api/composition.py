"""Composition root helpers: turn config files into a populated :class:`ProviderRegistry`."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from vibe.application.registry import ProviderRegistry, ProviderState
from vibe.application.settings import AppSettings
from vibe.domain.ports.agent_provider import ProviderDeps
from vibe.infrastructure.config import (
    EnvSecretResolver,
    load_environment,
    load_provider_configs,
    load_settings,
)

_log = logging.getLogger(__name__)


def repo_root() -> Path:
    """``<repo>/`` -- this file is ``<repo>/backend/vibe/api/composition.py``."""
    return Path(__file__).resolve().parents[3]


def build_registry(
    config_dir: Path, env: Mapping[str, str]
) -> tuple[ProviderRegistry, AppSettings]:
    """Load settings + providers and register everything.

    Provider problems never abort startup; they appear as ``FAILED`` entries.
    """
    settings = load_settings(config_dir, env)
    registry = ProviderRegistry(ProviderDeps(secrets=EnvSecretResolver(env)))

    result = load_provider_configs(config_dir / "providers.yaml", env)
    for config in result.configs:
        registry.register(config)
    for failure in result.failures:
        registry.register_failure(failure.provider_id, failure.reason, failure.config)

    for entry in registry.entries():
        level = logging.WARNING if entry.state is ProviderState.FAILED else logging.INFO
        _log.log(
            level, "provider %-24s %-8s %s", entry.config.id, entry.state.value, entry.error or ""
        )
    return registry, settings


def default_registry() -> tuple[ProviderRegistry, AppSettings]:
    root = repo_root()
    env = load_environment(root / ".env")
    return build_registry(root / "config", env)
