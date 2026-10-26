"""Anthropic Messages API <-> domain mapping (extended thinking). Pure functions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain.enums import EventType
from app.domain.errors import (
    AuthenticationError,
    ContentRejectedError,
    InvalidRequestError,
    ProviderError,
    RateLimitedError,
    TransientProviderError,
    ValidationError,
)
from app.domain.models import ExecutionTask

from providers.kit import Delta, HttpRequest, StreamState
from providers.kit.params import resolve_int_param

DEFAULT_MAX_TOKENS = 8192
MIN_THINKING_BUDGET = 1024
_ANSWER_HEADROOM = 4096

_FINISH_REASONS = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length"}


def build_request(task: ExecutionTask, model: str, options: Mapping[str, Any]) -> HttpRequest:
    system, user = task.prompt_snapshot.render()
    params = task.params
    budget = resolve_int_param(task, options, "thinking_budget")

    max_tokens = params.max_tokens
    if budget:
        if budget < MIN_THINKING_BUDGET:
            raise ValidationError(f"thinking_budget must be >= {MIN_THINKING_BUDGET}")
        if max_tokens is None:
            max_tokens = int(options.get("default_max_tokens", DEFAULT_MAX_TOKENS))
            max_tokens = max(max_tokens, budget + _ANSWER_HEADROOM)
        elif budget >= max_tokens:
            raise ValidationError("thinking_budget must be lower than max_tokens")
    elif max_tokens is None:
        max_tokens = int(options.get("default_max_tokens", DEFAULT_MAX_TOKENS))

    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "stream": True,
        "messages": [{"role": "user", "content": user}],
    }
    if system:
        body["system"] = system
    if budget:
        # The API rejects custom temperatures while extended thinking is enabled.
        body["thinking"] = {"type": "enabled", "budget_tokens": budget}
    elif params.temperature is not None:
        body["temperature"] = params.temperature
    return HttpRequest(path="/v1/messages", json=body)


def _stream_error(error: Any, provider_id: str) -> ProviderError:
    kind = str(error.get("type", "")) if isinstance(error, Mapping) else ""
    message = (
        str(error.get("message") or kind or "provider error")
        if isinstance(error, Mapping)
        else str(error)
    )
    if kind == "rate_limit_error":
        return RateLimitedError(message, provider_id=provider_id)
    if kind == "authentication_error" or kind == "permission_error":
        return AuthenticationError(message, provider_id=provider_id)
    if kind in ("invalid_request_error", "not_found_error", "request_too_large"):
        return InvalidRequestError(message, provider_id=provider_id)
    return TransientProviderError(message, provider_id=provider_id)  # overloaded_error, api_error


def parse_chunk(payload: Mapping[str, Any], state: StreamState, provider_id: str) -> list[Delta]:
    kind = payload.get("type")
    if kind == "error":
        raise _stream_error(payload.get("error"), provider_id)

    if kind == "message_start":
        usage = (payload.get("message") or {}).get("usage") or {}
        state.prompt_tokens = (
            int(usage.get("input_tokens") or 0)
            + int(usage.get("cache_creation_input_tokens") or 0)
            + int(usage.get("cache_read_input_tokens") or 0)
        )
        state.completion_tokens = int(usage.get("output_tokens") or 0)
        return []

    if kind == "content_block_delta":
        delta = payload.get("delta") or {}
        if delta.get("type") == "thinking_delta" and delta.get("thinking"):
            return [(EventType.THOUGHT, str(delta["thinking"]))]
        if delta.get("type") == "text_delta" and delta.get("text"):
            return [(EventType.TOKEN, str(delta["text"]))]
        return []  # signature_delta, input_json_delta, ...

    if kind == "message_delta":
        usage = payload.get("usage") or {}
        if usage.get("output_tokens") is not None:
            state.completion_tokens = int(usage["output_tokens"])
        stop = (payload.get("delta") or {}).get("stop_reason")
        if stop == "refusal":
            raise ContentRejectedError("the model refused the request", provider_id=provider_id)
        if stop:
            state.finish_reason = _FINISH_REASONS.get(str(stop), str(stop))
    return []
