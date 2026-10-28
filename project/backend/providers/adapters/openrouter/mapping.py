"""OpenRouter (OpenAI-compatible ``/chat/completions``) <-> domain mapping. Pure functions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain.enums import EventType
from app.domain.errors import ContentRejectedError, ProviderError, TransientProviderError
from app.domain.models import ExecutionTask

from providers.kit import (
    Delta,
    HttpRequest,
    StreamState,
    error_from_status,
)
from providers.kit.params import resolve_param


def build_request(task: ExecutionTask, model: str, options: Mapping[str, Any]) -> HttpRequest:
    system, turns = task.prompt_snapshot.render_messages()
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    for turn in turns:
        messages.append({"role": turn.role, "content": turn.content})

    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "usage": {"include": True},
    }
    params = task.params
    if params.temperature is not None:
        body["temperature"] = params.temperature
    if params.max_tokens is not None:
        body["max_tokens"] = params.max_tokens
    if options.get("provider_routing"):
        body["provider"] = dict(options["provider_routing"])
    # Thinking stage: e.g. {"effort": "high"} or {"max_tokens": 2000}.
    reasoning = resolve_param(task, options, "reasoning")
    if reasoning:
        body["reasoning"] = dict(reasoning)
    if params.extra.get("json_mode"):
        body["response_format"] = {"type": "json_object"}

    headers: dict[str, str] = {}
    if options.get("site_url"):
        headers["HTTP-Referer"] = str(options["site_url"])
    if options.get("app_name"):
        headers["X-Title"] = str(options["app_name"])
    return HttpRequest(path="/chat/completions", json=body, headers=headers)


def _stream_error(error: Any, provider_id: str) -> ProviderError:
    if isinstance(error, Mapping):
        message = str(error.get("message") or "provider error")
        code = error.get("code")
        if isinstance(code, int):
            return error_from_status(code, message, provider_id=provider_id)
        return TransientProviderError(message, provider_id=provider_id)
    return TransientProviderError(str(error), provider_id=provider_id)


def parse_chunk(payload: Mapping[str, Any], state: StreamState, provider_id: str) -> list[Delta]:
    if payload.get("error"):
        raise _stream_error(payload["error"], provider_id)

    usage = payload.get("usage")
    if isinstance(usage, Mapping):
        state.prompt_tokens = int(usage.get("prompt_tokens") or 0)
        state.completion_tokens = int(usage.get("completion_tokens") or 0)
        cost = usage.get("cost")
        state.cost_usd = float(cost) if cost is not None else None

    deltas: list[Delta] = []
    for choice in payload.get("choices") or []:
        delta = choice.get("delta") or {}
        reasoning = delta.get("reasoning") or delta.get("reasoning_content")
        if reasoning:
            deltas.append((EventType.THOUGHT, str(reasoning)))
        content = delta.get("content")
        if content:
            deltas.append((EventType.TOKEN, str(content)))
        finish = choice.get("finish_reason")
        if finish:
            if finish == "content_filter":
                raise ContentRejectedError(
                    "response blocked by content filter", provider_id=provider_id
                )
            if finish == "error":
                raise _stream_error(choice.get("error") or "upstream error", provider_id)
            state.finish_reason = str(finish)
    return deltas
