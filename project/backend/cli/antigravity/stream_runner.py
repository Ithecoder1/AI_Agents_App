"""Stream execution runner for Antigravity agy subprocess."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from providers.adapters.antigravity.command import (
    AntigravityCommandBuilder,
)


async def run_agy_stream(
    prompt: str,
    tool_mode: str = "full",
    requested_tools: list[str] | None = None,
    tools_str: str = "",
    extra_passthrough_args: list[str] | None = None,
    mode: str | None = None,
    executable: str = "agy",
    verbose: bool = False,
    log_file: str | Path | None = None,
    skip_permissions: bool = False,
) -> int:
    """Execute agy subprocess, stream NDJSON, print to terminal and record file log."""
    is_direct_qa = tool_mode == "direct_qa"
    tools_set = set(requested_tools or [])
    final_prompt = AntigravityCommandBuilder.decorate_prompt(prompt, is_direct_qa, tools_set)

    cmd = AntigravityCommandBuilder.build_command(
        executable=executable,
        final_prompt=final_prompt,
        is_direct_qa=is_direct_qa,
        target_tools=tools_set,
        skip_permissions=skip_permissions,
        extra_args=extra_passthrough_args or [],
        mode=mode,
    )

    log_path: Path | None = None
    if log_file:
        log_path = Path(log_file).resolve()  # noqa: ASYNC240
        log_path.parent.mkdir(parents=True, exist_ok=True)
        now_str = datetime.now(timezone.utc).isoformat()
        with open(log_path, "a", encoding="utf-8") as f:  # noqa: ASYNC230
            f.write(f"\n{'=' * 70}\n")
            f.write(f"=== Antigravity Session Trace: {now_str} ===\n")
            f.write(f"{'=' * 70}\n")
            f.write(f"Workspace : {Path.cwd()}\n")
            f.write(f"Prompt    :\n{final_prompt}\n")
            f.write(f"{'-' * 70}\n")

    def write_log(entry_type: str, content: str) -> None:
        if log_path:
            t_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{t_stamp}] [{entry_type}] {content}\n")

    write_log("COMMAND", f"{executable} {' '.join(cmd[1:])}")

    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(Path.cwd()),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def read_stream() -> None:
        if process.stdout is None:
            return
        async for raw_line in process.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            if verbose:
                print(f"[STREAM_EVENT] {line}")
            write_log("RAW_JSON", line)

            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                write_log("STDOUT", line)
                continue

            event_type = payload.get("event")
            if event_type == "init":
                init_data = payload.get("init", {})
                write_log("INIT", f"tools={init_data.get('tools')}")
            elif event_type == "step_update":
                step = payload.get("step_update", {})
                st = step.get("step_type")
                if st == "tool":
                    tool_info = step.get("tool_info", {})
                    name = step.get("tool_name") or tool_info.get("name", "tool")
                    if step.get("state") == "ACTIVE":
                        write_log("TOOL_CALL", f"{name}({tool_info.get('parameters', {})})")
                    elif step.get("state") == "DONE":
                        write_log("TOOL_RESULT", f"{name} -> {tool_info.get('output', '')}")
                thought = step.get("thought_delta") or step.get("thought")
                if thought:
                    write_log("THOUGHT", thought)
                delta = step.get("text_delta")
                if delta:
                    write_log("RESPONSE_DELTA", delta)
            elif event_type == "result":
                write_log("RESULT", json.dumps(payload.get("result", {})))
                write_log("SESSION_COMPLETE", "result event received")

    await read_stream()
    rc = await process.wait()
    write_log("SESSION_END", f"exit_code={rc}")
    return rc
