"""Load provider definitions from ``config/providers.yaml`` (+ ``providers.local.yaml``).

Failure isolation: one invalid or unresolvable provider entry becomes a
:class:`ProviderLoadFailure`; the rest still load. Only a malformed *file* is fatal.

Layering: ``providers.local.yaml`` (git-ignored) overrides/extends the base file, merging
entries by ``id`` (deep merge for mappings, replacement for lists/scalars).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from app.domain.errors import ConfigurationError, ValidationError
from app.domain.models import AgentProviderConfig
from app.infrastructure.config.interpolation import interpolate

_SCHEMA_NAME = "providers.schema.json"


@dataclass(frozen=True)
class ProviderLoadFailure:
    provider_id: str  # best effort; "" if the entry had no usable id
    reason: str
    config: AgentProviderConfig | None = None  # present when the entry itself was valid


@dataclass(frozen=True)
class ProviderLoadResult:
    configs: tuple[AgentProviderConfig, ...]
    failures: tuple[ProviderLoadFailure, ...]


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in {path}: {exc}") from exc


def _deep_merge(base: Mapping[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _merge_provider_lists(
    base: list[dict[str, Any]], local: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged = list(base)
    index = {p.get("id"): i for i, p in enumerate(merged) if isinstance(p, dict)}
    for entry in local:
        if isinstance(entry, dict) and entry.get("id") in index:
            i = index[entry["id"]]
            merged[i] = _deep_merge(merged[i], entry)
        else:
            merged.append(entry)
    return merged


def _format_errors(validator: Draft202012Validator, instance: Any) -> str:
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    return "; ".join(
        f"{'/'.join(map(str, e.absolute_path)) or '<entry>'}: {e.message}" for e in errors
    )


def load_provider_configs(
    path: Path,
    env: Mapping[str, str],
    *,
    schema_path: Path | None = None,
    local_path: Path | None = None,
) -> ProviderLoadResult:
    if not path.is_file():
        raise ConfigurationError(f"Provider config not found: {path}")
    schema_file = schema_path or path.with_name(_SCHEMA_NAME)
    schema = json.loads(schema_file.read_text(encoding="utf-8"))
    file_validator = Draft202012Validator(schema)
    entry_validator = Draft202012Validator({"$ref": "#/$defs/provider", "$defs": schema["$defs"]})

    document = _read_yaml(path) or {}
    local = local_path or path.with_name(path.stem + ".local" + path.suffix)
    if local.is_file():
        local_doc = _read_yaml(local) or {}
        document = {
            **document,
            "providers": _merge_provider_lists(
                list(document.get("providers", [])), list(local_doc.get("providers", []))
            ),
        }

    if not isinstance(document, dict) or file_validator.is_valid(document) is False:
        raise ConfigurationError(
            f"{path.name} is invalid: {_format_errors(file_validator, document)}"
        )

    configs: list[AgentProviderConfig] = []
    failures: list[ProviderLoadFailure] = []
    seen: set[str] = set()

    for raw in document["providers"]:
        raw_id = raw.get("id") if isinstance(raw.get("id"), str) else ""
        if not entry_validator.is_valid(raw):
            failures.append(ProviderLoadFailure(raw_id, _format_errors(entry_validator, raw)))
            continue
        if raw["id"] in seen:
            failures.append(ProviderLoadFailure(raw["id"], f"duplicate provider id {raw['id']!r}"))
            continue
        seen.add(raw["id"])

        enabled = bool(raw.get("enabled", True))
        try:
            # Disabled providers keep their raw ${VAR} text: they may reference unset secrets.
            data = interpolate(raw, env) if enabled else dict(raw)
            configs.append(_to_config(data))
        except (ConfigurationError, ValidationError) as exc:
            failures.append(
                ProviderLoadFailure(raw["id"], str(exc), _to_config(dict(raw), force_disabled=True))
            )
    return ProviderLoadResult(tuple(configs), tuple(failures))


def _to_config(data: Mapping[str, Any], *, force_disabled: bool = False) -> AgentProviderConfig:
    return AgentProviderConfig(
        id=data["id"],
        kind=data["kind"],
        display_name=data.get("display_name", ""),
        adapter=data.get("adapter"),
        enabled=False if force_disabled else bool(data.get("enabled", True)),
        max_concurrency=int(data.get("max_concurrency", 4)),
        options=dict(data.get("options") or {}),
        capabilities=dict(data.get("capabilities") or {}),
    )
