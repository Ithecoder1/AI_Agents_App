"""Antigravity Headless Runner & Just-in-Time Dynamic Tool Provisioning CLI.

Integrates with local `agy` CLI using `--output-format stream-json`, decoupling the 60-tool
schema catalog and provisioning tools dynamically on demand to slash upfront token overhead.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add project/backend to sys.path so 'app' package can be resolved
WORKSPACE_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = WORKSPACE_ROOT / "project" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from app.application.tools import (
    DynamicToolRegistry,
    ToolRetriever,
)
from app.domain.tools import ToolRetrievalPolicy

try:
    from dotenv import load_dotenv
except ImportError:

    def load_dotenv(*_args: object, **_kwargs: object) -> bool:  # type: ignore[misc]
        return False


# Ensure UTF-8 output encoding on Windows consoles for Hebrew and special characters
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Load environment variables
env_file = WORKSPACE_ROOT / "project" / ".env"
if env_file.exists():
    load_dotenv(env_file)
else:
    load_dotenv()


def load_tools_catalog() -> DynamicToolRegistry:
    """Load the 60-tool Antigravity catalog from disk."""
    candidates = [
        WORKSPACE_ROOT / "config" / "antigravity_tools_catalog.json",
        WORKSPACE_ROOT / "project" / "config" / "antigravity_tools_catalog.json",
    ]
    for p in candidates:
        if p.is_file():
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
                return DynamicToolRegistry.from_catalog_dict(data)
    return DynamicToolRegistry()


WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "edit_file",
        "write_file",
        "write_to_file",
        "replace_file_content",
        "multi_replace_file_content",
        "create_file",
    }
)


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
        return None

    if all_tools is not None:
        selected_capabilities = [t for t in all_tools if getattr(t, "name", str(t)) in target_tools]
    else:
        selected_capabilities = [
            t for t in agy.BuiltinTools if t.value in target_tools or t.name.lower() in target_tools
        ]
    try:
        cfg = agy.CapabilitiesConfig(enabled_tools=selected_capabilities)
    except (TypeError, ValueError):
        cfg = agy.CapabilitiesConfig(tools=selected_capabilities)
    with contextlib.suppress(AttributeError, TypeError, ValueError):
        cfg.tools = selected_capabilities
    return cfg


def create_sdk_agent_config(
    target_tools: set[str],
    all_tools: list[Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Create LocalAgentConfig with filtered CapabilitiesConfig for google.antigravity."""
    try:
        import google.antigravity as agy
    except ImportError:
        return None

    capabilities = configure_sdk_capabilities(target_tools, all_tools=all_tools)
    return agy.LocalAgentConfig(capabilities=capabilities, **kwargs)


def get_installed_plugins(executable: str = "agy") -> list[dict[str, Any]]:
    """Retrieve installed plugins metadata via `agy plugin list --json`."""
    try:
        res = subprocess.run(
            [executable, "plugin", "list", "--json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    if res.returncode == 0 and res.stdout.strip():
        try:
            data = json.loads(res.stdout)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            return []
    return []


def disable_unrelated_plugins(executable: str, target_tools: set[str]) -> list[str]:
    """Disable active plugins whose skills/tools are not in target_tools whitelist.

    Returns the list of plugin names that were newly disabled so they can be restored later.
    """
    plugins = get_installed_plugins(executable)
    disabled: list[str] = []

    if plugins:
        for p in plugins:
            name = p.get("name")
            if not name or not p.get("enabled", False):
                continue
            skills = p.get("skills", [])
            # If no skill from this plugin is in target_tools, disable it
            if not any(s in target_tools for s in skills):
                try:
                    res = subprocess.run(
                        [executable, "plugin", "disable", str(name)],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=5.0,
                    )
                    if res.returncode == 0:
                        disabled.append(str(name))
                except (OSError, subprocess.SubprocessError):
                    continue
    else:
        # Fallback: if 'science' plugin is not in target_tools, disable it
        if not any(t in target_tools for t in ("science", "alphafold", "pdb")):
            try:
                res = subprocess.run(
                    [executable, "plugin", "disable", "science"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5.0,
                )
                if res.returncode == 0:
                    disabled.append("science")
            except (OSError, subprocess.SubprocessError):
                pass

    if disabled:
        print(f"[*] Fenced session plugins: disabled unrelated plugins {disabled}")
    return disabled


def restore_plugins(executable: str, plugins_to_restore: list[str]) -> None:
    """Re-enable plugins that were temporarily disabled during the session."""
    for name in plugins_to_restore:
        try:
            subprocess.run(
                [executable, "plugin", "enable", name],
                capture_output=True,
                text=True,
                check=False,
                timeout=5.0,
            )
        except (OSError, subprocess.SubprocessError):
            continue


async def run_agy_stream(
    prompt: str,
    *,
    tool_mode: str = "dynamic",
    requested_tools: list[str] | None = None,
    tools_str: str = "",
    mode: str | None = None,
    skip_permissions: bool = False,
    extra_passthrough_args: list[str] | None = None,
    cwd: Path | None = None,
    verbose: bool = False,
    log_file: str | Path | None = None,
) -> int:
    """Run `agy -p <prompt> --output-format stream-json` with Dynamic Tool Retrieval."""
    executable = shutil.which("agy") or "agy"
    work_dir = cwd or WORKSPACE_ROOT
    catalog = load_tools_catalog()

    # 1. Agent Capability Triage (0-tool vs Dynamic Baseline)
    is_direct_qa = tool_mode in (
        "direct_qa",
        "none",
        "0-tool",
    ) or prompt.lower().startswith("direct_qa:")
    if is_direct_qa:
        policy = ToolRetrievalPolicy.tool_less()
        retriever = ToolRetriever(registry=catalog, policy=policy)
        mode_desc = "DIRECT_QA: Pure text reasoning (0 tool schemas injected)"
    else:
        policy = ToolRetrievalPolicy.lean_dynamic(
            base_tools=("search_available_tools",),
            max_active_tools=5,
        )
        retriever = ToolRetriever(
            registry=catalog,
            policy=policy,
            initial_tools=requested_tools or (),
        )
        mode_desc = (
            f"DYNAMIC_JIT: Lean baseline "
            f"({len(retriever.get_active_tools())} tool schemas vs 60 in catalog)"
        )

    target_tools = set(requested_tools or ())
    if not target_tools and tools_str.strip():
        target_tools = {t.strip() for t in tools_str.split(",") if t.strip()}

    disabled_plugins: list[str] = []
    if target_tools:
        disabled_plugins = disable_unrelated_plugins(executable, target_tools)

    # 2. Prompt Decoration & Constraints
    final_prompt = prompt
    if is_direct_qa:
        constraint = (
            "[CONSTRAINT: Direct answer only. Do NOT use any tools, file operations, or commands.]"
        )
        if constraint not in final_prompt:
            final_prompt = f"{constraint}\n\n{final_prompt}"
    elif target_tools:
        whitelist_str = tools_str.strip() or ", ".join(sorted(target_tools))
        boundary = (
            f"[ALLOWED TOOLS STRICT WHITELIST: {whitelist_str}. "
            "Calling any unlisted tool is strictly forbidden.]"
        )
        if boundary not in final_prompt:
            final_prompt = f"{boundary}\n\n{final_prompt}"

    # 3. Command Builder & Flag Mapping
    cmd = [executable, "-p", final_prompt, "--output-format", "stream-json"]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")

    passthrough = list(extra_passthrough_args or [])
    effort_flags = []
    if "--effort" not in passthrough:
        effort_flags = ["--effort", "low"]

    has_write_tools = bool(target_tools & WRITE_TOOLS)

    if is_direct_qa or (mode == "plan"):
        cmd.extend(["--mode", "plan", "--disable-slash-commands", *effort_flags])
    elif target_tools:
        if not has_write_tools:
            cmd.extend(["--mode", "plan", "--disable-slash-commands"])
        else:
            cmd.append("--disable-slash-commands")
    elif mode:
        cmd.extend(["--mode", mode])

    if passthrough:
        i = 0
        while i < len(passthrough):
            arg = passthrough[i]
            if arg in ("--disable-slash-commands", "--dangerously-skip-permissions") and arg in cmd:
                i += 1
                continue
            if arg in ("--mode", "--effort") and arg in cmd:
                i += 2
                continue
            cmd.append(arg)
            i += 1

    # Setup file logging if requested
    log_path: Path | None = None
    if log_file:
        log_path = Path(log_file).resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        now_str = datetime.now(timezone.utc).isoformat()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'=' * 70}\n")
            f.write(f"=== Antigravity Session Trace: {now_str} ===\n")
            f.write(f"{'=' * 70}\n")
            f.write(f"Workspace : {work_dir}\n")
            f.write(f"Mode      : {mode_desc}\n")
            f.write(f"Prompt    :\n{final_prompt}\n")
            f.write(f"{'-' * 70}\n")

    def write_log(entry_type: str, content: str) -> None:
        if log_path:
            t_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{t_stamp}] [{entry_type}] {content}\n")

    write_log("COMMAND", f"{executable} {' '.join(cmd[1:])}")

    print(f"[*] Mode: {mode_desc}")
    print(f"[*] Active Schemas: {list(retriever.get_active_tool_names())}")
    print(f'[*] Command: {executable} -p "..." {" ".join(cmd[3:])}')
    print(f"[*] Workspace: {work_dir}")
    if log_path:
        print(f"[*] Session Log: {log_path}")
    print("[*] Awaiting response stream...\n")

    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(work_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stderr_lines: list[str] = []

    async def drain_stderr() -> None:
        if process.stderr is None:
            return
        async for line in process.stderr:
            err_line = line.decode("utf-8", errors="replace")
            stderr_lines.append(err_line)
            write_log("STDERR", err_line.strip())
            if verbose:
                sys.stderr.write(f"[STDERR] {err_line}")
                sys.stderr.flush()

    stderr_task = asyncio.create_task(drain_stderr())

    in_thinking = False
    in_response = False

    try:
        assert process.stdout is not None
        async for raw_line in process.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            write_log("RAW_JSON", line)

            if verbose:
                sys.stdout.write(f"\n[STREAM_EVENT] {line}\n")
                sys.stdout.flush()

            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                # Raw line or warning
                write_log("RAW_TEXT", line)
                sys.stdout.write(f"\n[Raw] {line}\n")
                sys.stdout.flush()
                continue

            event = payload.get("event")

            if event == "init":
                init_data = payload.get("init", {})
                conv_id = payload.get("conversation_id", "")
                tools = init_data.get("tools", [])
                mode = init_data.get("permission_mode", "default")
                short_id = conv_id[:8] + "..." if len(conv_id) > 8 else conv_id
                write_log(
                    "INIT",
                    f"conversation_id={conv_id} tools_count={len(tools)} mode={mode}",
                )
                print(
                    f"[*] Agent session initialized (ID: {short_id}, "
                    f"Manifest: {len(tools)} tools, Mode: {mode})"
                )

            elif event == "step_update":
                step = payload.get("step_update", {})
                step_type = step.get("step_type")
                state = step.get("state")

                # Handle Thought / Thinking tokens
                thought = (
                    step.get("thought_delta") or step.get("thinking_delta") or step.get("thought")
                )
                if thought:
                    write_log("THOUGHT", thought)
                    if not in_thinking:
                        sys.stdout.write("\n[Thinking] ")
                        in_thinking = True
                        in_response = False
                    sys.stdout.write(thought)
                    sys.stdout.flush()

                # Handle Tool invocations
                if step_type == "tool":
                    if state == "ACTIVE":
                        if in_thinking or in_response:
                            sys.stdout.write("\n")
                            in_thinking = False
                            in_response = False
                        tool_info = step.get("tool_info", {})
                        tool_name = step.get("tool_name") or tool_info.get("name", "unknown")
                        params = tool_info.get("parameters", {})
                        if isinstance(params, dict):
                            params_str = ", ".join(f"{k}={v!r}" for k, v in params.items())
                        else:
                            params_str = str(params)

                        write_log("TOOL_CALL", f"{tool_name} params={params_str}")
                        retriever.activate_tool(tool_name)
                        sys.stdout.write(f"\n[Tool: {tool_name}] {params_str}\n")
                        sys.stdout.flush()
                    elif state == "DONE":
                        tool_info = step.get("tool_info", {})
                        output = tool_info.get("output")
                        write_log("TOOL_RESULT", f"{tool_name} output={output}")
                        if output:
                            preview = str(output).strip().replace("\n", " ")
                            if len(preview) > 120:
                                preview = preview[:117] + "..."
                            sys.stdout.write(f"[Tool Result] {preview}\n")
                            sys.stdout.flush()

                # Handle Agent text response chunks
                text_delta = step.get("text_delta")
                if text_delta:
                    write_log("RESPONSE_DELTA", text_delta)
                    if in_thinking:
                        sys.stdout.write("\n")
                        in_thinking = False
                    if not in_response:
                        sys.stdout.write("\n[Response]\n")
                        in_response = True
                    sys.stdout.write(text_delta)
                    sys.stdout.flush()

            elif event == "result":
                res = payload.get("result", {})
                status = res.get("status", "SUCCESS")
                duration = res.get("duration_seconds", 0.0)
                usage = res.get("usage", {})
                in_tokens = usage.get("input_tokens", 0)
                out_tokens = usage.get("output_tokens", 0)
                thinking_tokens = usage.get("thinking_tokens", 0)
                write_log(
                    "RESULT",
                    f"status={status} duration={duration:.2f}s usage={usage}",
                )
                print(
                    f"\n\n[*] Result: {status} in {duration:.1f}s | "
                    f"Tokens: {in_tokens} in / {out_tokens} out (thinking: {thinking_tokens})"
                )

        await process.wait()
        await stderr_task
        write_log("SESSION_COMPLETE", f"returncode={process.returncode}")

        if process.returncode != 0:
            err_msg = "".join(stderr_lines).strip()
            print(
                f"\n[!] agy exited with code {process.returncode}: {err_msg}",
                file=sys.stderr,
            )

        return process.returncode or 0

    except (asyncio.CancelledError, KeyboardInterrupt):
        print("\n[*] Terminating agent process...")
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=3.0)
        except (TimeoutError, OSError):
            process.kill()
        raise
    finally:
        if disabled_plugins:
            restore_plugins(executable, disabled_plugins)
        if log_path:
            write_log("SESSION_END", "Session ended")


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description="Antigravity Headless Runner with Dynamic Tool Retrieval"
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        default="what are you know about my project? tell me in short sentence",
        help="Target prompt for the agent",
    )
    parser.add_argument(
        "--direct-qa",
        action="store_true",
        help="Bypass tool injection entirely (0-tool pure text mode)",
    )
    parser.add_argument(
        "--dynamic",
        action="store_true",
        default=True,
        help="Use Dynamic Tool Retrieval (lean baseline with search_available_tools)",
    )
    parser.add_argument(
        "--tools",
        type=str,
        default="",
        help=(
            "Comma-separated list of explicit tools to activate upfront "
            "(e.g. view_file,write_to_file)"
        ),
    )
    parser.add_argument(
        "--mode",
        type=str,
        default=None,
        choices=["accept-edits", "auto", "plan"],
        help="Set the agent execution mode for this session (accept-edits, auto, plan)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print verbose streaming events and raw JSON payloads directly to console",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default="",
        help="File path to save the complete session interaction trace and raw event logs",
    )
    parser.add_argument(
        "--dangerously-skip-permissions",
        "--skip-permissions",
        action="store_true",
        dest="skip_permissions",
        help="Auto-approve all tool permission requests",
    )
    return parser.parse_known_args()


async def main() -> None:
    args, extra_passthrough_args = parse_args()
    tool_mode = "direct_qa" if args.direct_qa else "dynamic"
    target_tools = set(args.tools.split(",")) if args.tools else set()
    requested_tools = [t.strip() for t in target_tools if t.strip()]

    await run_agy_stream(
        prompt=args.prompt,
        tool_mode=tool_mode,
        requested_tools=requested_tools,
        tools_str=args.tools,
        mode=args.mode,
        skip_permissions=args.skip_permissions,
        extra_passthrough_args=extra_passthrough_args,
        verbose=args.verbose,
        log_file=args.log_file,
    )


if __name__ == "__main__":
    asyncio.run(main())
