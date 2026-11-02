"""Provider configuration and capability domain models."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from typing import Any

from app.domain.errors import ValidationError

_PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SECRET_KEY_HINTS = ("key", "token", "secret", "password", "credential", "authorization")


def _mask(options: Mapping[str, Any]) -> dict[str, Any]:
    masked: dict[str, Any] = {}
    for key, value in options.items():
        if isinstance(value, Mapping):
            masked[key] = _mask(value)
        elif any(hint in key.lower() for hint in _SECRET_KEY_HINTS) and value:
            masked[key] = "***"
        else:
            masked[key] = value
    return masked


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    streaming: bool = True
    supports_cancel: bool = False
    # True only if re-running the same task is harmless. Agents that edit files are NOT.
    idempotent: bool = False
    can_modify_workspace: bool = False
    reports_usage: bool = False
    # Can continue a dropped stream instead of restarting from scratch.
    supports_resume: bool = False
    models: tuple[str, ...] = ()

    def merged(self, overrides: Mapping[str, Any]) -> ProviderCapabilities:
        """Apply config overrides, rejecting unknown keys."""
        known = {f.name for f in fields(self)}
        unknown = sorted(set(overrides) - known)
        if unknown:
            raise ValidationError(f"Unknown capability override(s): {', '.join(unknown)}")
        clean = dict(overrides)
        if "models" in clean:
            clean["models"] = tuple(clean["models"])
        return replace(self, **clean)


@dataclass(frozen=True, slots=True)
class HealthStatus:
    ok: bool
    reason: str | None = None
    latency_ms: float | None = None

    def __bool__(self) -> bool:
        return self.ok

    @classmethod
    def up(cls, latency_ms: float | None = None) -> HealthStatus:
        return cls(True, None, latency_ms)

    @classmethod
    def down(cls, reason: str) -> HealthStatus:
        return cls(False, reason, None)


@dataclass(frozen=True, slots=True)
class AgentProviderConfig:
    id: str
    kind: str
    display_name: str = ""
    # "package.module:Callable" -- a class or function ``(config, deps) -> IAgentProvider``.
    # If omitted, resolved through the ``app.providers`` entry-point group by ``kind``.
    adapter: str | None = None
    enabled: bool = True
    max_concurrency: int = 4
    # repr=False: options may contain resolved secrets. Use ``safe_options()`` for display.
    options: Mapping[str, Any] = field(default_factory=dict, repr=False)
    # Overrides applied on top of the adapter's ``default_capabilities``.
    capabilities: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _PROVIDER_ID.match(self.id):
            raise ValidationError(
                f"Invalid provider id {self.id!r}: use lowercase letters, digits, '-' or '_'"
            )
        if not self.kind:
            raise ValidationError(f"Provider {self.id!r} needs a non-empty 'kind'")
        if self.max_concurrency < 1:
            raise ValidationError(f"Provider {self.id!r}: max_concurrency must be >= 1")

    @property
    def label(self) -> str:
        return self.display_name or self.id

    def safe_options(self) -> dict[str, Any]:
        """Options with secret-looking values masked -- safe for logs and the API."""
        return _mask(self.options)
