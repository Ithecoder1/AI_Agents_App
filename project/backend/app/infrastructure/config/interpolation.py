"""``${VAR}`` / ``${VAR:-default}`` interpolation for YAML values."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.domain.errors import ConfigurationError

_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


def interpolate(value: Any, env: Mapping[str, str]) -> Any:
    """Recursively substitute env references in strings inside dicts/lists.

    A reference without a default whose variable is unset (or empty) raises
    :class:`ConfigurationError` naming the variable.
    """
    if isinstance(value, str):

        def repl(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            found = env.get(name)
            if found:
                return found
            if default is not None:
                return default
            raise ConfigurationError(f"Environment variable {name} is not set")

        return _PATTERN.sub(repl, value)
    if isinstance(value, Mapping):
        return {k: interpolate(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate(v, env) for v in value]
    return value
