"""Minimal Server-Sent-Events parser for ``httpx`` streaming responses."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class SSEMessage:
    event: str
    data: str


async def iter_sse(response: httpx.Response) -> AsyncIterator[SSEMessage]:
    """Yield one :class:`SSEMessage` per dispatched event; comments/keep-alives are skipped."""
    event = "message"
    data: list[str] = []
    async for line in response.aiter_lines():
        if not line:
            if data:
                yield SSEMessage(event, "\n".join(data))
            event, data = "message", []
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if name == "event":
            event = value
        elif name == "data":
            data.append(value)
    if data:
        yield SSEMessage(event, "\n".join(data))
