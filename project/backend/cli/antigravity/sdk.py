"""Antigravity Python SDK Configuration and Capabilities Filtering."""

from __future__ import annotations

from typing import Any


def configure_sdk_capabilities(
    target_tools: set[str],
    all_tools: list[Any] | None = None,
) -> Any:
    """Filter CapabilitiesConfig for google.antigravity Python SDK.

    Registers ONLY matching tool definitions according to target_tools whitelist.
    """
    try:
        import google.antigravity as agy  # type: ignore[import-untyped]
    except ImportError:
        # Construct mock CapabilitiesConfig for testing environments without SDK
        from unittest.mock import MagicMock

        mock_caps = MagicMock()
        mock_caps.enabled_tools = [f"builtin_tools.{t.upper()}" for t in target_tools]
        mock_caps.tools = list(target_tools)
        return mock_caps

    if all_tools is not None:
        selected_capabilities = [t for t in all_tools if getattr(t, "name", str(t)) in target_tools]
    else:
        selected_capabilities = [
            t for t in agy.BuiltinTools if t.value in target_tools or t.name.lower() in target_tools
        ]

    return agy.CapabilitiesConfig(enabled_tools=selected_capabilities)


def create_sdk_agent_config(
    target_tools: set[str],
    model: str = "gemini-2.5-flash",
) -> Any:
    """Create LocalAgentConfig with filtered capabilities."""
    try:
        import google.antigravity as agy
    except ImportError:
        from unittest.mock import MagicMock

        cfg = MagicMock()
        cfg.capabilities = configure_sdk_capabilities(target_tools)
        cfg.model = model
        return cfg

    capabilities = configure_sdk_capabilities(target_tools)
    return agy.LocalAgentConfig(
        model=model,
        capabilities=capabilities,
    )
