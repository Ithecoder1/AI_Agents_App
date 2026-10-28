"""Gemini ``streamGenerateContent`` <-> domain mapping (thinking summaries). Pure functions."""

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
from providers.kit.params import resolve_int_param

_FINISH_REASONS = {"STOP": "stop", "MAX_TOKENS": "length"}
_BLOCKED_FINISH = {
    "SAFETY",
    "RECITATION",
    "BLOCKLIST",
    "PROHIBITED_CONTENT",
    "SPII",
    "IMAGE_SAFETY",
}


def build_request(task: ExecutionTask, model: str, options: Mapping[str, Any]) -> HttpRequest:
    system, turns = task.prompt_snapshot.render_messages()
    params = task.params

    config: dict[str, Any] = {}
    if params.temperature is not None:
        config["temperature"] = params.temperature
    if params.max_tokens is not None:
        config["maxOutputTokens"] = params.max_tokens
    if params.extra.get("json_mode"):
        config["responseMimeType"] = "application/json"
    # -1 = dynamic budget, 0 = thinking off, N = token budget. Thought summaries are requested
    # whenever thinking is on so the UI can render the thinking stage.
    budget = resolve_int_param(task, options, "thinking_budget")
    if budget is not None:
        config["thinkingConfig"] = {"includeThoughts": budget != 0, "thinkingBudget": budget}

    contents: list[dict[str, Any]] = [
        {
            "role": "model" if turn.role == "assistant" else "user",
            "parts": [{"text": turn.content}],
        }
        for turn in turns
    ]
    body: dict[str, Any] = {"contents": contents}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if config:
        body["generationConfig"] = config
    return HttpRequest(
        path=f"/models/{model}:streamGenerateContent", json=body, params={"alt": "sse"}
    )


def parse_chunk(payload: Mapping[str, Any], state: StreamState, provider_id: str) -> list[Delta]:
    error = payload.get("error")
    if error:
        raise _stream_error(error, provider_id)
    block = (payload.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise ContentRejectedError(f"prompt blocked: {block}", provider_id=provider_id)

    usage = payload.get("usageMetadata")
    if isinstance(usage, Mapping):  # cumulative: the last chunk wins
        state.prompt_tokens = int(usage.get("promptTokenCount") or 0)
        state.completion_tokens = int(usage.get("candidatesTokenCount") or 0) + int(
            usage.get("thoughtsTokenCount") or 0
        )

    deltas: list[Delta] = []
    for candidate in payload.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            text = part.get("text")
            if text:
                kind = EventType.THOUGHT if part.get("thought") else EventType.TOKEN
                deltas.append((kind, str(text)))
        finish = candidate.get("finishReason")
        if finish:
            if finish in _BLOCKED_FINISH:
                raise ContentRejectedError(f"response blocked: {finish}", provider_id=provider_id)
            state.finish_reason = _FINISH_REASONS.get(str(finish), str(finish).lower())
    return deltas


def _stream_error(error: Any, provider_id: str) -> ProviderError:
    if isinstance(error, Mapping):
        message = str(error.get("message") or "provider error")
        code = error.get("code")
        if isinstance(code, int):
            return error_from_status(code, message, provider_id=provider_id)
        return TransientProviderError(message, provider_id=provider_id)
    return TransientProviderError(str(error), provider_id=provider_id)
