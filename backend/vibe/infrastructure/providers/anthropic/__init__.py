"""Anthropic adapter (httpx + SSE streaming, extended thinking surfaced as thoughts)."""

from vibe.infrastructure.providers.anthropic.adapter import AnthropicProvider

__all__ = ["AnthropicProvider"]
