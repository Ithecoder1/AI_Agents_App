"""Small helpers for reading provider-specific tuning knobs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain.errors import ValidationError
from app.domain.models import ExecutionTask


def resolve_param(task: ExecutionTask, options: Mapping[str, Any], key: str) -> Any:
    """Per-task ``params.extra[key]`` wins over the provider-level ``options[key]``."""
    if key in task.params.extra:
        return task.params.extra[key]
    return options.get(key)


def resolve_int_param(task: ExecutionTask, options: Mapping[str, Any], key: str) -> int | None:
    value = resolve_param(task, options, key)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValidationError(f"{key} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{key} must be an integer") from exc
