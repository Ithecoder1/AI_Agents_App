"""Environment loading: ``.env`` file layered under real environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values


def load_environment(
    dotenv_path: Path | None = None, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Return ``.env`` values overlaid by the real environment (real env always wins)."""
    merged: dict[str, str] = {}
    if dotenv_path is not None and dotenv_path.is_file():
        merged.update({k: v for k, v in dotenv_values(dotenv_path).items() if v is not None})
    merged.update(os.environ if environ is None else environ)
    return merged


class EnvSecretResolver:
    """``SecretResolver`` backed by an environment mapping."""

    def __init__(self, env: Mapping[str, str]) -> None:
        self._env = dict(env)

    def get(self, name: str) -> str | None:
        value = self._env.get(name)
        return value if value else None
