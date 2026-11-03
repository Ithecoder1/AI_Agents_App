"""Antigravity CLI Turn Metrics & Telemetry Decorators.

Resolves and formats the 4 conversation metrics:
- System Instructions
- Active Tools
- Active Tools Count
- Injected Schemas
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.application.tools import DynamicToolRegistry, ToolRetriever
from app.domain.tools import ToolDescriptor


@dataclass(frozen=True, slots=True)
class CliTurnMetrics:
    """Snapshot of turn telemetry for requests and responses."""

    system_instructions: str
    active_tools: tuple[str, ...]
    active_tools_count: int
    injected_schemas: tuple[dict[str, Any], ...]
    used_files: tuple[str, ...] = ()
    total_duration: float = 0.0
    usage_metrics: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "system_instructions": self.system_instructions,
            "active_tools": list(self.active_tools),
            "active_tools_count": self.active_tools_count,
            "injected_schemas": list(self.injected_schemas),
            "used_files": list(self.used_files),
            "total_duration": self.total_duration,
            "usage_metrics": dict(self.usage_metrics),
        }


def load_cli_tool_registry() -> DynamicToolRegistry:
    """Find and load antigravity_tools_catalog.json if available."""
    root = Path(__file__).resolve().parents[3]
    candidates = [
        Path.cwd() / "config" / "antigravity_tools_catalog.json",
        Path.cwd() / "project" / "config" / "antigravity_tools_catalog.json",
        root / "config" / "antigravity_tools_catalog.json",
        root / "project" / "config" / "antigravity_tools_catalog.json",
    ]
    for path in candidates:
        if path.is_file():
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                return DynamicToolRegistry.from_catalog_dict(data)
            except Exception:
                pass
    return DynamicToolRegistry()


def resolve_cli_turn_metrics(
    tool_mode: str = "full",
    requested_tools: list[str] | None = None,
    system_instructions: str | None = None,
    registry: DynamicToolRegistry | None = None,
) -> CliTurnMetrics:
    """Resolve active tools, count, schemas, and system instructions for this CLI turn."""
    reg = registry if registry is not None else load_cli_tool_registry()
    active_descs: list[ToolDescriptor] = []

    if tool_mode == "direct_qa":
        active_descs = []
        constraint = (
            "[CONSTRAINT: Direct answer only. Do NOT use any tools, file operations, or commands.]"
        )
    elif requested_tools:
        for t in requested_tools:
            desc = reg.get(t) or ToolDescriptor(name=t, category="custom", summary=f"Tool {t}")
            active_descs.append(desc)
        tools_str = ", ".join(sorted(d.name for d in active_descs))
        constraint = (
            f"[ALLOWED TOOLS STRICT WHITELIST: {tools_str}. "
            "Calling any unlisted tool is strictly forbidden.]"
        )
    elif tool_mode == "dynamic":
        retriever = ToolRetriever(reg)
        active_descs = list(retriever.get_active_tools())
        constraint = "[DYNAMIC TOOLS: search_available_tools is active for JIT discovery.]"
    else:
        # Full mode
        active_descs = list(reg.all_descriptors())
        constraint = "[FULL TOOLKIT: all built-in tools active.]"

    full_system = f"{constraint}\n\n{system_instructions}" if system_instructions else constraint
    schemas = tuple(
        dict(d.schema) if d.schema else {"name": d.name, "description": d.summary}
        for d in active_descs
    )

    return CliTurnMetrics(
        system_instructions=full_system,
        active_tools=tuple(d.name for d in active_descs),
        active_tools_count=len(active_descs),
        injected_schemas=schemas,
    )


def normalize_usage_metrics(raw_usage: dict[str, Any] | None) -> dict[str, int]:
    """Normalize usage dictionary to have standard token metrics keys."""
    u = dict(raw_usage or {})
    in_tok = int(u.get("input_tokens", u.get("prompt_tokens", 0)))
    out_tok = int(u.get("output_tokens", u.get("completion_tokens", 0)))
    think_tok = int(u.get("thinking_tokens", 0))
    cache_tok = int(u.get("cache_read_tokens", 0))
    total_tok = int(u.get("total_tokens", in_tok + out_tok))
    return {
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "thinking_tokens": think_tok,
        "cache_read_tokens": cache_tok,
        "total_tokens": total_tok,
    }


def format_turn_metrics(
    metrics: CliTurnMetrics,
    phase: str = "REQUEST",
    *,
    verbose: bool = False,
    usage: dict[str, Any] | None = None,
) -> str:
    """Format turn metrics cleanly for terminal output."""
    lines: list[str] = [
        "",
        "=" * 65,
        f"* [{phase.upper()} TURN METRICS]",
        "=" * 65,
        f"- Active Tools Count  : {metrics.active_tools_count}",
    ]

    tools_display = (
        ", ".join(metrics.active_tools) if metrics.active_tools else "(None - direct_qa)"
    )
    if len(tools_display) > 80 and not verbose:
        tools_display = tools_display[:77] + "..."
    lines.append(f"- Active Tools        : {tools_display}")

    # System instructions
    sys_display = metrics.system_instructions.strip().replace("\n", " ")
    if len(sys_display) > 80 and not verbose:
        sys_display = sys_display[:77] + "..."
    lines.append(f"- System Instructions : {sys_display}")

    # Injected schemas
    lines.append(f"- Injected Schemas    : {metrics.active_tools_count} schema definition(s)")
    if verbose and metrics.injected_schemas:
        lines.append("  [Schema Details]:")
        for s in metrics.injected_schemas:
            s_name = s.get("name", "tool")
            s_cat = s.get("category", "")
            s_desc = s.get("description", "")
            lines.append(f"  + {s_name} ({s_cat}): {s_desc}")

    eff_usage = metrics.usage_metrics or (normalize_usage_metrics(usage) if usage else {})
    if eff_usage:
        in_t = eff_usage["input_tokens"]
        out_t = eff_usage["output_tokens"]
        th_t = eff_usage["thinking_tokens"]
        c_t = eff_usage["cache_read_tokens"]
        tot_t = eff_usage["total_tokens"]
        lines.append(
            f"- Tokens / Usage      : input={in_t}, output={out_t} "
            f"(think={th_t}, cache={c_t}, total={tot_t})"
        )

    if metrics.total_duration > 0:
        lines.append(f"- Total Duration      : {metrics.total_duration:.2f}s")

    if metrics.used_files:
        files_display = ", ".join(metrics.used_files)
        if len(files_display) > 80 and not verbose:
            files_display = files_display[:77] + "..."
        lines.append(f"- Used Files          : {files_display}")
        lines.append(f"- Used Files Count    : {len(metrics.used_files)}")
    elif verbose:
        lines.append("- Used Files          : (None)")

    lines.append("=" * 65)
    return "\n".join(lines)


def print_full_context_payload(
    system_instructions: str,
    tools: list[str] | tuple[str, ...],
    tools_count: int,
    *,
    used_files: list[str] | tuple[str, ...] | set[str] | None = None,
    total_duration: float | str | None = None,
    duration: float | str | None = None,
    usage: dict[str, Any] | None = None,
    usage_metrics: dict[str, Any] | None = None,
    injected_schemas: Any = None,
    verbose: bool = False,
) -> None:
    """Print the exact post-execution full context payload message."""
    files_list = list(used_files) if used_files else []
    dur_val = total_duration if total_duration is not None else duration
    if isinstance(dur_val, (int, float)):
        dur_str = f"{dur_val:.2f}s"
    elif dur_val:
        dur_str = f"{dur_val}s" if not str(dur_val).endswith("s") else str(dur_val)
    else:
        dur_str = "0.00s"

    raw_usage = usage if usage is not None else usage_metrics
    norm_usage = normalize_usage_metrics(raw_usage)

    print("=" * 40 + " [FULL CONTEXT PAYLOAD] " + "=" * 40)
    print(f"System Instructions: {system_instructions}")
    print(f"Active Tools: {list(tools)}")
    print(f"Active Tools Count: {tools_count}")
    print(f"Used Files: {files_list}")
    print(f"Used Files Count: {len(files_list)}")
    print(f"Total Duration: {dur_str}")
    print(f"Usage Metrics: {norm_usage}")
    if verbose and injected_schemas:
        print(f"Injected Schemas: {list(injected_schemas)}")
    print("=" * 100)
