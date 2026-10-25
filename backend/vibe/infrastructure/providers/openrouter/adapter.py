"""OpenRouter adapter: OpenAI-compatible chat completions over SSE, with reasoning streamed
as ``THOUGHT`` events (the "thinking" stage)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from vibe.domain.models import ExecutionTask, ProviderCapabilities
from vibe.infrastructure.provider_kit import Delta, HttpRequest, HttpStreamingRunner, StreamState
from vibe.infrastructure.providers.openrouter import mapping


class OpenRouterProvider(HttpStreamingRunner):
    default_capabilities = ProviderCapabilities(
        streaming=True,
        supports_cancel=True,
        idempotent=True,
        reports_usage=True,
        models=(
            "anthropic/claude-sonnet-4",
            "openai/gpt-4o",
            "openai/gpt-4o-mini",
            "google/gemini-2.5-flash",
        ),
    )
    default_base_url = "https://openrouter.ai/api/v1"
    default_model = "openai/gpt-4o-mini"
    api_key_env = "OPENROUTER_API_KEY"

    def auth_headers(self, api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"}

    def build_request(self, task: ExecutionTask, model: str) -> HttpRequest:
        return mapping.build_request(task, model, self.config.options)

    def parse_chunk(
        self, event: str, payload: Mapping[str, Any], state: StreamState
    ) -> Iterable[Delta]:
        return mapping.parse_chunk(payload, state, self.config.id)

    def health_request(self) -> HttpRequest:
        return HttpRequest(path="/key", method="GET")
