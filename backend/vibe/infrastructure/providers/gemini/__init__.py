"""Gemini adapter (httpx + SSE streaming, thought summaries surfaced as thoughts)."""

from vibe.infrastructure.providers.gemini.adapter import GeminiProvider

__all__ = ["GeminiProvider"]
