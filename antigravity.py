"""Antigravity Headless Runner & JIT Dynamic Tool CLI Entrypoint.

Delegates execution to the decoupled application orchestrator and provider subsystem.
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Add project/backend to sys.path so 'app', 'providers', and 'cli' can be resolved
WORKSPACE_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = WORKSPACE_ROOT / "project" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from cli.antigravity import (
    configure_sdk_capabilities,
    create_sdk_agent_config,
    parse_args,
    run_agy_stream,
    run_cli_main,
)

__all__ = [
    "configure_sdk_capabilities",
    "create_sdk_agent_config",
    "parse_args",
    "run_agy_stream",
    "run_cli_main",
]

if __name__ == "__main__":
    sys.exit(run_cli_main())
