"""Load :class:`AppSettings` from ``config/app.yaml`` (+ ``app.local.yaml``) and env vars.

Precedence (high -> low): environment > ``app.local.yaml`` > ``app.yaml`` > dataclass defaults.

Environment forms:
  * ``VIBE_<SECTION>__<KEY>=value``  e.g. ``VIBE_SERVER__PORT=9000``
  * convenience aliases: ``VIBE_AUTH_TOKEN``, ``VIBE_DB_URL``, ``VIBE_WORKSPACE_ROOTS``

Unknown keys are errors: a typo in config must fail loudly, not silently use a default.
"""

from __future__ import annotations

import dataclasses
import typing
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypeVar

import yaml

from app.application.settings import AppSettings
from app.domain.errors import ConfigurationError

T = TypeVar("T")

_ALIASES: dict[str, tuple[str, str]] = {
    "VIBE_AUTH_TOKEN": ("security", "auth_token"),
    "VIBE_DB_URL": ("storage", "db_url"),
    "VIBE_WORKSPACE_ROOTS": ("security", "workspace_roots"),
}
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _coerce(value: Any, hint: Any, where: str) -> Any:
    origin = typing.get_origin(hint)
    try:
        if hint is bool:
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in _TRUE:
                return True
            if text in _FALSE:
                return False
            raise ValueError(text)
        if hint is int:
            return int(value)
        if hint is float:
            return float(value)
        if hint is str:
            return str(value)
        if origin is tuple:
            if isinstance(value, str):
                value = [v.strip() for v in value.split(",") if v.strip()]
            return tuple(str(v) for v in value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{where}: cannot interpret {value!r} as {hint}") from exc
    return value


def _build(cls: type[T], data: Mapping[str, Any], path: str) -> T:
    hints = typing.get_type_hints(cls)
    fields = {f.name for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
    unknown = sorted(set(data) - fields)
    if unknown:
        raise ConfigurationError(f"Unknown setting(s) in '{path}': {', '.join(unknown)}")
    kwargs: dict[str, Any] = {}
    for name, value in data.items():
        hint = hints[name]
        where = f"{path}.{name}"
        if isinstance(hint, type) and dataclasses.is_dataclass(hint):
            if not isinstance(value, Mapping):
                raise ConfigurationError(f"'{where}' must be a mapping")
            kwargs[name] = _build(hint, value, where)
        else:
            kwargs[name] = _coerce(value, hint, where)
    return cls(**kwargs)


def _merge(base: dict[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in over.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = dict(value) if isinstance(value, Mapping) else value
    return base


def _env_overrides(env: Mapping[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, (section, key) in _ALIASES.items():
        if env.get(name):
            out.setdefault(section, {})[key] = env[name]
    for name, value in env.items():
        if name.startswith("VIBE_") and "__" in name:
            section, _, key = name[len("VIBE_") :].partition("__")
            if section and key:
                out.setdefault(section.lower(), {})[key.lower()] = value
    return out


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigurationError(f"{path} must contain a mapping at the top level")
    return loaded


def load_settings(config_dir: Path, env: Mapping[str, str]) -> AppSettings:
    merged: dict[str, Any] = {}
    _merge(merged, _read(config_dir / "app.yaml"))
    _merge(merged, _read(config_dir / "app.local.yaml"))
    _merge(merged, _env_overrides(env))
    return _build(AppSettings, merged, "settings")
