"""Stream execution runner for Antigravity agy subprocess."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from providers.adapters.antigravity.command import (
    AntigravityCommandBuilder,
)

from .metrics import (
    format_turn_metrics,
    normalize_usage_metrics,
    print_full_context_payload,
    resolve_cli_turn_metrics,
)


def extract_file_paths(data: Any, collector: list[str]) -> None:
    """Extract referenced file paths from tool parameters or event payloads."""
    if isinstance(data, dict):
        file_keys = {
            "absolutepath",
            "targetfile",
            "filepath",
            "path",
            "filename",
            "file",
            "sourcefile",
            "destfile",
            "searchpath",
        }
        for k, v in data.items():
            k_norm = k.lower().replace("_", "").replace("-", "")
            if k_norm in file_keys:
                if isinstance(v, str) and v.strip() and v.strip() not in collector:
                    collector.append(v.strip())
                elif isinstance(v, (list, tuple)):
                    for item in v:
                        if isinstance(item, str) and item.strip() and item.strip() not in collector:
                            collector.append(item.strip())
            elif isinstance(v, (dict, list)):
                extract_file_paths(v, collector)
    elif isinstance(data, (list, tuple)):
        for item in data:
            extract_file_paths(item, collector)


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
    system_instructions: str | None = None,
    show_metrics: bool = True,
    output_format: str = "text",
) -> int:
    """Execute agy subprocess, stream NDJSON, print to terminal and record file log."""
    is_direct_qa = tool_mode == "direct_qa"
    tools_set = set(requested_tools or [])
    final_prompt = AntigravityCommandBuilder.decorate_prompt(prompt, is_direct_qa, tools_set)

    turn_metrics = resolve_cli_turn_metrics(
        tool_mode=tool_mode,
        requested_tools=requested_tools,
        system_instructions=system_instructions,
    )

    if show_metrics and verbose:
        print(format_turn_metrics(turn_metrics, phase="REQUEST", verbose=verbose))

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
            f.write(f"\n{'=' * 70}\n=== Antigravity Session Trace: {now_str} ===\n{'=' * 70}\n")
            f.write(f"Workspace : {Path.cwd()}\nPrompt    :\n{final_prompt}\n{'-' * 70}\n")

    def write_log(entry_type: str, content: str) -> None:
        if log_path:
            t_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{t_stamp}] [{entry_type}] {content}\n")

    write_log("COMMAND", f"{executable} {' '.join(cmd[1:])}")
    write_log("REQUEST_METRICS", json.dumps(turn_metrics.to_dict()))

    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(Path.cwd()),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    start_time = time.perf_counter()
    in_thinking, in_response, has_printed_text = False, False, False
    last_usage: dict[str, Any] | None = None
    last_duration: float = 0.0
    used_files: list[str] = []
    collected_events: list[dict[str, Any]] = []

    async def read_stream() -> None:
        nonlocal in_thinking, in_response, has_printed_text, last_usage, last_duration
        if process.stdout is None:
            return
        async for raw_line in process.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            write_log("RAW_JSON", line)
            if output_format == "stream-json":
                print(line, flush=True)
            if verbose:
                print(f"[STREAM_EVENT] {line}", flush=True)

            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                write_log("STDOUT", line)
                if output_format == "text":
                    print(f"\n[Raw] {line}")
                continue

            if output_format == "json":
                collected_events.append(payload)

            event_type = payload.get("event")
            if event_type == "init":
                init_data = payload.get("init", {})
                write_log("INIT", f"tools={init_data.get('tools')}")
                if output_format == "text":
                    conv_id = payload.get("conversation_id", "")
                    tools = init_data.get("tools", [])
                    mode_val = init_data.get("permission_mode", "default")
                    short_id = conv_id[:8] + "..." if len(conv_id) > 8 else conv_id
                    print(
                        f"[*] Agent session initialized (ID: {short_id}, "
                        f"Manifest: {len(tools)} tools, Mode: {mode_val})"
                    )

            elif event_type == "step_update":
                step = payload.get("step_update", {})
                st, state = step.get("step_type"), step.get("state")
                thought = (
                    step.get("thought_delta") or step.get("thinking_delta") or step.get("thought")
                )
                if thought:
                    write_log("THOUGHT", thought)
                    if output_format == "text":
                        if not in_thinking:
                            print("\n[Thinking] ", end="", flush=True)
                            in_thinking, in_response = True, False
                        print(thought, end="", flush=True)

                if st == "tool":
                    extract_file_paths(step, used_files)
                    tool_info = step.get("tool_info", {})
                    name = step.get("tool_name") or tool_info.get("name", "tool")
                    params = tool_info.get("parameters", {})
                    if state == "ACTIVE":
                        p_str = (
                            ", ".join(f"{k}={v!r}" for k, v in params.items())
                            if isinstance(params, dict)
                            else str(params)
                        )
                        write_log("TOOL_CALL", f"{name}({params})")
                        if output_format == "text":
                            if in_thinking or in_response:
                                print()
                                in_thinking, in_response = False, False
                            print(f"\n[Tool: {name}] {p_str}")
                    elif state == "DONE":
                        out = tool_info.get("output")
                        write_log("TOOL_RESULT", f"{name} -> {out}")
                        if output_format == "text" and out:
                            preview = str(out).strip().replace("\n", " ")
                            if len(preview) > 120:
                                preview = preview[:117] + "..."
                            print(f"[Tool Result] {preview}")

                delta = step.get("text_delta")
                if delta:
                    write_log("RESPONSE_DELTA", delta)
                    if output_format == "text":
                        has_printed_text = True
                        if in_thinking:
                            print()
                            in_thinking = False
                        if not in_response:
                            print("\n[Response]")
                            in_response = True
                        print(delta, end="", flush=True)

            elif event_type == "result":
                res = payload.get("result", {})
                last_usage = res.get("usage", {})
                try:
                    last_duration = float(res.get("duration_seconds", 0.0))
                except (ValueError, TypeError):
                    last_duration = 0.0
                write_log("RESULT", json.dumps(res))
                write_log("SESSION_COMPLETE", "result event received")
                if output_format == "text":
                    status = res.get("status", "SUCCESS")
                    dur = last_duration
                    in_tok = last_usage.get("prompt_tokens", last_usage.get("input_tokens", 0))
                    out_tok = last_usage.get(
                        "completion_tokens", last_usage.get("output_tokens", 0)
                    )
                    th_tok = last_usage.get("thinking_tokens", 0)
                    if in_response or in_thinking:
                        print()
                    print(
                        f"\n\n[*] Result: {status} in {dur:.1f}s | "
                        f"Tokens: {in_tok} in / {out_tok} out (thinking: {th_tok})"
                    )

    try:
        await read_stream()
    finally:
        if output_format == "json":
            print(json.dumps(collected_events, indent=2))
        elif has_printed_text or in_thinking:
            print()

        rc = await process.wait()
        write_log("SESSION_END", f"exit_code={rc}")

        elapsed = time.perf_counter() - start_time
        final_dur = last_duration if last_duration > 0 else elapsed

        if show_metrics:
            print_full_context_payload(
                system_instructions=turn_metrics.system_instructions,
                tools=turn_metrics.active_tools,
                tools_count=turn_metrics.active_tools_count,
                used_files=used_files,
                total_duration=final_dur,
                usage=last_usage or {},
                injected_schemas=turn_metrics.injected_schemas,
                executed_command=subprocess.list2cmdline(cmd),
                verbose=verbose,
            )

        resp_metrics = {
            **turn_metrics.to_dict(),
            "used_files": used_files,
            "total_duration": final_dur,
            "usage": normalize_usage_metrics(last_usage),
            "exit_code": rc,
        }
        write_log("RESPONSE_METRICS", json.dumps(resp_metrics))
    return rc
