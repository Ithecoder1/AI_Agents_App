from __future__ import annotations

from typing import Protocol


class SecretResolver(Protocol):
    """Looks up secrets by name (env vars today; a vault tomorrow)."""

    def get(self, name: str) -> str | None: ...
