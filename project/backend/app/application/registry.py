"""Dynamic provider registry.

Resolves the target infrastructure from runtime configuration. Adding a provider never
requires touching this file: the adapter is located through (1) the config's ``adapter``
import path (``"package.module:Callable"``) or (2) the ``app.providers`` entry-point
group, keyed by ``kind``. Import paths come from operator-controlled config and are
therefore trusted; never build them from request data.

One broken provider must not take the app down: failures during loading are recorded on
the entry (state ``FAILED`` + reason) and surfaced through the API/UI.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from importlib import metadata
from typing import Any

from app.domain.errors import (
    ConfigurationError,
    ProviderNotFoundError,
    ProviderUnavailableError,
)
from app.domain.models import AgentProviderConfig, HealthStatus
from app.domain.ports.agent_provider import IAgentProvider, ProviderDeps, ProviderFactory

ENTRY_POINT_GROUP = "app.providers"

_log = logging.getLogger(__name__)

EntryPointLookup = Callable[[str], ProviderFactory | None]


class ProviderState(str, Enum):
    READY = "ready"
    DISABLED = "disabled"
    FAILED = "failed"


@dataclass
class ProviderEntry:
    config: AgentProviderConfig
    state: ProviderState
    error: str | None = None
    instance: IAgentProvider | None = None


def entry_point_lookup(kind: str, group: str = ENTRY_POINT_GROUP) -> ProviderFactory | None:
    """Default entry-point resolver (Python 3.10+ ``entry_points(group=...)`` API)."""
    for ep in metadata.entry_points(group=group):
        if ep.name == kind:
            loaded: ProviderFactory = ep.load()
            return loaded
    return None


def load_factory(spec: str) -> ProviderFactory:
    """Import ``"package.module:Attr"`` and return the callable."""
    module_name, sep, attr = spec.partition(":")
    if not sep or not module_name or not attr:
        raise ConfigurationError(f"Adapter {spec!r} must look like 'package.module:Callable'")
    module = importlib.import_module(module_name)
    target: Any = module
    for part in attr.split("."):
        target = getattr(target, part)
    if not callable(target):
        raise ConfigurationError(f"Adapter {spec!r} is not callable")
    factory: ProviderFactory = target
    return factory


class ProviderRegistry:
    def __init__(
        self,
        deps: ProviderDeps,
        *,
        entry_points: EntryPointLookup = entry_point_lookup,
        health_ttl_s: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._deps = deps
        self._entry_points = entry_points
        self._health_ttl_s = health_ttl_s
        self._clock = clock
        self._entries: dict[str, ProviderEntry] = {}
        self._health: dict[str, tuple[float, HealthStatus]] = {}

    # -- registration ------------------------------------------------------- #
    def register(self, config: AgentProviderConfig) -> ProviderEntry:
        """Register and (if enabled) eagerly instantiate a provider. Never raises for a bad
        adapter -- the failure is recorded on the returned entry. Duplicate ids do raise."""
        if config.id in self._entries:
            raise ConfigurationError(f"Duplicate provider id {config.id!r}")
        if not config.enabled:
            entry = ProviderEntry(config, ProviderState.DISABLED, "disabled in configuration")
        else:
            try:
                instance = self._instantiate(config)
                entry = ProviderEntry(config, ProviderState.READY, instance=instance)
            except Exception as exc:
                _log.warning("Provider %r failed to load: %s", config.id, exc)
                entry = ProviderEntry(config, ProviderState.FAILED, f"{type(exc).__name__}: {exc}")
        self._entries[config.id] = entry
        return entry

    def register_failure(
        self, provider_id: str, reason: str, config: AgentProviderConfig | None = None
    ) -> ProviderEntry:
        """Record a provider that could not even be configured (invalid YAML entry, etc.)."""
        if provider_id in self._entries:
            raise ConfigurationError(f"Duplicate provider id {provider_id!r}")
        cfg = config or AgentProviderConfig(
            id=provider_id if provider_id else "invalid", kind="unknown", enabled=False
        )
        entry = ProviderEntry(cfg, ProviderState.FAILED, reason)
        self._entries[cfg.id] = entry
        return entry

    def _instantiate(self, config: AgentProviderConfig) -> IAgentProvider:
        factory: ProviderFactory | None
        if config.adapter:
            factory = load_factory(config.adapter)
        else:
            factory = self._entry_points(config.kind)
            if factory is None:
                raise ConfigurationError(
                    f"No 'adapter' set and no entry point in group {ENTRY_POINT_GROUP!r} "
                    f"for kind {config.kind!r}"
                )
        instance = factory(config, self._deps)
        if not isinstance(instance, IAgentProvider):
            raise ConfigurationError(
                f"{type(instance).__name__} does not implement the IAgentProvider contract"
            )
        return instance

    # -- lookup ------------------------------------------------------------- #
    def entries(self) -> list[ProviderEntry]:
        return list(self._entries.values())

    def get_entry(self, provider_id: str) -> ProviderEntry:
        try:
            return self._entries[provider_id]
        except KeyError:
            raise ProviderNotFoundError(f"Unknown provider {provider_id!r}") from None

    def resolve(self, provider_id: str) -> IAgentProvider:
        """Return a ready provider or raise (``ProviderNotFoundError`` /
        ``ProviderUnavailableError`` -- the latter is eligible for fallback routing)."""
        entry = self.get_entry(provider_id)
        if entry.state is not ProviderState.READY or entry.instance is None:
            raise ProviderUnavailableError(
                f"Provider {provider_id!r} is {entry.state.value}: {entry.error}",
                provider_id=provider_id,
            )
        return entry.instance

    # -- health ------------------------------------------------------------- #
    async def health(self, provider_id: str, *, force: bool = False) -> HealthStatus:
        entry = self.get_entry(provider_id)
        if entry.state is not ProviderState.READY or entry.instance is None:
            return HealthStatus.down(entry.error or entry.state.value)
        cached = self._health.get(provider_id)
        now = self._clock()
        if cached and not force and now - cached[0] < self._health_ttl_s:
            return cached[1]
        try:
            status = await entry.instance.health_check()
        except Exception as exc:
            status = HealthStatus.down(f"health_check raised {type(exc).__name__}: {exc}")
        self._health[provider_id] = (now, status)
        return status

    async def health_all(self, *, force: bool = False) -> dict[str, HealthStatus]:
        ids = list(self._entries)
        results = await asyncio.gather(*(self.health(i, force=force) for i in ids))
        return dict(zip(ids, results, strict=True))

    async def aclose(self) -> None:
        for entry in self._entries.values():
            if entry.instance is not None:
                try:
                    await entry.instance.aclose()
                except Exception:
                    _log.exception("Error closing provider %r", entry.config.id)
