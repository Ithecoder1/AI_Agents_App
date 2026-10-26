"""Gemini adapter: ``streamGenerateContent`` over SSE; thought summaries as ``THOUGHT`` events."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.domain.models import ExecutionTask, ProviderCapabilities

from providers.adapters.gemini import mapping
from providers.kit import Delta, HttpRequest, HttpStreamingRunner, StreamState


class GeminiProvider(HttpStreamingRunner):
    default_capabilities = ProviderCapabilities(
        streaming=True,
        supports_cancel=True,
        idempotent=True,
        reports_usage=True,
        models=(
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.0-flash",
            "gemini-3-flash-preview",
            "gemini-3.1-flash-lite",
            "gemini-3.8-flash",
        ),
    )
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"
    default_model = "gemini-3-flash-preview"
    api_key_env = "GEMINI_API_KEY"

    def auth_headers(self, api_key: str) -> dict[str, str]:
        return {"x-goog-api-key": api_key}

    def _api_key(self) -> str | None:
        return super()._api_key() or self.secret("GOOGLE_API_KEY")

    def build_request(self, task: ExecutionTask, model: str) -> HttpRequest:
        return mapping.build_request(task, model, self.config.options)

    def parse_chunk(
        self, event: str, payload: Mapping[str, Any], state: StreamState
    ) -> Iterable[Delta]:
        return mapping.parse_chunk(payload, state, self.config.id)

    def health_request(self) -> HttpRequest:
        return HttpRequest(path="/models", params={"pageSize": "1"}, method="GET")
