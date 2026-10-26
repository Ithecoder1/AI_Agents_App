"""Typed application settings (plain dataclasses; loading/merging lives in infrastructure)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ServerSettings:
    host: str = "127.0.0.1"  # loopback by default: this app can drive agents that edit files
    port: int = 8000


@dataclass(frozen=True)
class SecuritySettings:
    auth_token: str = ""  # required when binding to a non-loopback host
    workspace_roots: tuple[str, ...] = ()  # absolute paths agents may operate in
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")


@dataclass(frozen=True)
class RunnerSettings:
    concurrency: int = 4
    queue_size: int = 100
    subscriber_buffer: int = 1000  # per-WebSocket event buffer; slow consumers are dropped


@dataclass(frozen=True)
class ResilienceSettings:
    max_attempts: int = 3  # per provider, before falling back
    base_delay_s: float = 1.0
    max_delay_s: float = 30.0
    jitter: bool = True
    retry_after_cap_s: float = 60.0  # if a provider asks us to wait longer, fall back instead
    breaker_failure_threshold: int = 5
    breaker_reset_s: float = 30.0
    connect_timeout_s: float = 10.0
    first_token_timeout_s: float = 60.0
    idle_timeout_s: float = 120.0
    total_timeout_s: float = 900.0


@dataclass(frozen=True)
class StorageSettings:
    db_url: str = "sqlite+aiosqlite:///./data/app.db"
    artifacts_dir: str = "data/artifacts"
    retention_days: int = 30


@dataclass(frozen=True)
class AppSettings:
    server: ServerSettings = field(default_factory=ServerSettings)
    security: SecuritySettings = field(default_factory=SecuritySettings)
    runner: RunnerSettings = field(default_factory=RunnerSettings)
    resilience: ResilienceSettings = field(default_factory=ResilienceSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
