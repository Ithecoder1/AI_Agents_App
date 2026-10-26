"""Anthropic adapter (httpx + SSE streaming, extended thinking surfaced as thoughts)."""

from providers.adapters.anthropic.adapter import AnthropicProvider

__all__ = ["AnthropicProvider"]
