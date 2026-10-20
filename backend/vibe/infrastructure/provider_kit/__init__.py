"""Shared toolkit for provider authors (the ONLY shared code between providers).

Lives outside ``vibe.infrastructure.providers`` so the "providers are independent"
architecture contract can stay strict.
"""

from vibe.infrastructure.provider_kit.base import BaseAgentRunner, fold_events

__all__ = ["BaseAgentRunner", "fold_events"]
