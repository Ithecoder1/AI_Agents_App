"""Shared builders for tests."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from vibe.domain.models import (
    AgentProviderConfig,
    ExecutionParams,
    ExecutionTask,
    Prompt,
)
from vibe.domain.ports.agent_provider import ProviderDeps
from vibe.infrastructure.config import EnvSecretResolver

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_CONFIG = REPO_ROOT / "config"


def make_prompt(user: str = "Say hello to the world", **kwargs: Any) -> Prompt:
    return Prompt(user_prompt=user, **kwargs)


def make_task(
    provider_id: str = "fake", user: str = "Say hello to the world", **param_kwargs: Any
) -> ExecutionTask:
    return ExecutionTask.create(
        make_prompt(user), ExecutionParams(provider_id=provider_id, **param_kwargs)
    )


def running_task(provider_id: str = "fake", **kwargs: Any) -> ExecutionTask:
    """A task already in RUNNING with attempt 1 (what providers actually receive)."""
    return make_task(provider_id, **kwargs).start(provider_id)


def make_config(
    id_: str = "fake",
    kind: str = "fake",
    *,
    options: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> AgentProviderConfig:
    return AgentProviderConfig(id=id_, kind=kind, options=dict(options or {}), **kwargs)


def make_deps(env: Mapping[str, str] | None = None) -> ProviderDeps:
    return ProviderDeps(secrets=EnvSecretResolver(env or {}))
