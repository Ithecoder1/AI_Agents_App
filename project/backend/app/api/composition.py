"""Composition root helpers: turn config files into a populated :class:`ProviderRegistry`."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

from app.application.chat_sessions import ChatSessionService
from app.application.chat_turns import ChatTurnService
from app.application.registry import ProviderRegistry, ProviderState
from app.application.settings import AppSettings
from app.application.workspace_manager import WorkspaceManager
from app.domain.ports.agent_provider import ProviderDeps
from app.infrastructure.config import (
    EnvSecretResolver,
    load_environment,
    load_provider_configs,
    load_settings,
)
from app.infrastructure.persistence import (
    SqlAlchemyChatRepository,
    create_engine,
    create_session_factory,
    resolve_database_url,
    upgrade_database,
)
from app.infrastructure.workspace import YamlManifestParser

_log = logging.getLogger(__name__)


def repo_root() -> Path:
    """``<project>/`` -- this file is ``<project>/backend/app/api/composition.py``."""
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


def build_workspace_manager(root: Path | None = None) -> WorkspaceManager:
    base = root or repo_root()
    return WorkspaceManager(base, manifest_parser=YamlManifestParser())


def default_workspace_manager() -> WorkspaceManager:
    return build_workspace_manager(repo_root())


def build_chat_services(
    settings: AppSettings,
    registry: ProviderRegistry,
    workspace_manager: WorkspaceManager,
    root: Path | None = None,
) -> tuple[AsyncEngine, SqlAlchemyChatRepository, ChatSessionService, ChatTurnService]:
    base = root or repo_root()
    url = resolve_database_url(settings.storage.db_url, base)
    upgrade_database(url)
    engine = create_engine(url)
    session_factory = create_session_factory(engine)
    chat_repo = SqlAlchemyChatRepository(session_factory)
    chat_session_service = ChatSessionService(chat_repo)
    chat_turn_service = ChatTurnService(
        chat_session_service, chat_repo, registry, workspace_manager
    )
    return engine, chat_repo, chat_session_service, chat_turn_service


def default_chat_services(
    settings: AppSettings,
    registry: ProviderRegistry,
    workspace_manager: WorkspaceManager,
) -> tuple[AsyncEngine, SqlAlchemyChatRepository, ChatSessionService, ChatTurnService]:
    return build_chat_services(settings, registry, workspace_manager, repo_root())
