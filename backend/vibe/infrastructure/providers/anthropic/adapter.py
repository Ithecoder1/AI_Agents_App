"""Anthropic adapter: Messages API over SSE; extended thinking streamed as ``THOUGHT`` events."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from vibe.domain.models import ExecutionTask, ProviderCapabilities
from vibe.infrastructure.provider_kit import Delta, HttpRequest, HttpStreamingRunner, StreamState
from vibe.infrastructure.providers.anthropic import mapping

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(HttpStreamingRunner):
    default_capabilities = ProviderCapabilities(
        streaming=True,
        supports_cancel=True,
        idempotent=True,
        reports_usage=True,
        models=(
            "claude-sonnet-4-5",
            "claude-3-7-sonnet-20250219",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
        ),
    )
    default_base_url = "https://api.anthropic.com"
    default_model = "claude-sonnet-4-5"
    api_key_env = "ANTHROPIC_API_KEY"

    def auth_headers(self, api_key: str) -> dict[str, str]:
        return {
            "x-api-key": api_key,
            "anthropic-version": str(self.config.options.get("api_version") or ANTHROPIC_VERSION),
        }

    def build_request(self, task: ExecutionTask, model: str) -> HttpRequest:
        return mapping.build_request(task, model, self.config.options)

    def parse_chunk(
        self, event: str, payload: Mapping[str, Any], state: StreamState
    ) -> Iterable[Delta]:
        return mapping.parse_chunk(payload, state, self.config.id)

    def health_request(self) -> HttpRequest:
        return HttpRequest(path="/v1/models", params={"limit": "1"}, method="GET")
