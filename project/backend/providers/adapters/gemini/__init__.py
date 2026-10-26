"""Gemini adapter (httpx + SSE streaming, thought summaries surfaced as thoughts)."""

from providers.adapters.gemini.adapter import GeminiProvider

__all__ = ["GeminiProvider"]
