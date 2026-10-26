"""``{{variable}}`` templating for prompts (pure, no external dependencies)."""

from __future__ import annotations

import re
from collections.abc import Mapping

from app.domain.errors import ValidationError

_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def find_variables(text: str) -> set[str]:
    """Names referenced as ``{{name}}`` in *text*."""
    return set(_VAR.findall(text))


def render(text: str, values: Mapping[str, object]) -> str:
    """Substitute ``{{name}}`` placeholders. Missing names raise :class:`ValidationError`."""
    missing = sorted(find_variables(text) - set(values))
    if missing:
        raise ValidationError(f"Undefined prompt variable(s): {', '.join(missing)}")
    return _VAR.sub(lambda m: str(values[m.group(1)]), text)
