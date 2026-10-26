"""Offline HTTP fixtures for the SSE-based providers (``httpx.MockTransport``)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

import httpx


def sse(*payloads: Mapping[str, Any] | str, events: list[str] | None = None) -> bytes:
    """Encode payloads as an SSE body (strings are sent verbatim, e.g. ``[DONE]``)."""
    out: list[str] = []
    for index, payload in enumerate(payloads):
        if events:
            out.append(f"event: {events[index]}\n")
        data = payload if isinstance(payload, str) else json.dumps(payload)
        out.append(f"data: {data}\n\n")
    return "".join(out).encode()


class _SlowStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], delay_s: float) -> None:
        self._chunks = chunks
        self._delay_s = delay_s

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            await asyncio.sleep(self._delay_s)
            yield chunk


def stream_transport(
    body: bytes,
    *,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
    on_request: Callable[[httpx.Request], None] | None = None,
) -> httpx.MockTransport:
    """POSTs get *body* as an event stream; any other method (health probe) gets ``{}``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if on_request is not None:
            on_request(request)
        if request.method != "POST":
            return httpx.Response(200, json={})
        return httpx.Response(
            status, headers={"content-type": "text/event-stream", **(headers or {})}, content=body
        )

    return httpx.MockTransport(handler)


def slow_transport(chunk: bytes, *, count: int = 50, delay_s: float = 0.1) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return httpx.Response(200, json={})
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_SlowStream([chunk] * count, delay_s),
        )

    return httpx.MockTransport(handler)
