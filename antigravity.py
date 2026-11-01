"""Antigravity Headless Runner & Just-in-Time Dynamic Tool Provisioning CLI.

Integrates with local `agy` CLI using `--output-format stream-json`, decoupling the 60-tool
schema catalog and provisioning tools dynamically on demand to slash upfront token overhead.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

# Add project/backend/app to sys.path so app domain and application modules can be resolved
WORKSPACE_ROOT = Path(__file__).resolve().parent
APP_ROOT = WORKSPACE_ROOT / "project" / "backend" / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.application.tools import (  # type: ignore[import-not-found]
    DynamicToolRegistry,
    ToolRetriever,
)
from app.domain.tools import ToolRetrievalPolicy  # type: ignore[import-not-found]
from dotenv import load_dotenv

# Ensure UTF-8 output encoding on Windows consoles for Hebrew and special characters
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

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


async def run_agy_stream(
    prompt: str,
    *,
    tool_mode: str = "dynamic",
    requested_tools: list[str] | None = None,
    skip_permissions: bool = False,
    cwd: Path | None = None,
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
        mode_desc = f"DYNAMIC_JIT: Lean baseline ({len(retriever.get_active_tools())} tool schemas vs 60 in catalog)"

    cmd = [executable, "-p", prompt, "--output-format", "stream-json"]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")

    print(f"[*] Mode: {mode_desc}")
    print(f"[*] Active Schemas: {list(retriever.get_active_tool_names())}")
    print(f"[*] Command: {' '.join(cmd[:4])} ...")
    print(f"[*] Workspace: {work_dir}")
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
            stderr_lines.append(line.decode("utf-8", errors="replace"))

    stderr_task = asyncio.create_task(drain_stderr())

    in_thinking = False
    in_response = False

    try:
        assert process.stdout is not None
        async for raw_line in process.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                # Raw line or warning
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
                print(
                    f"[*] Agent session initialized (ID: {short_id}, Manifest: {len(tools)} tools, Mode: {mode})"
                )

            elif event == "step_update":
                step = payload.get("step_update", {})
                step_type = step.get("step_type")
                state = step.get("state")

                # Handle Thought / Thinking tokens
                thought = (
                    step.get("thought_delta")
                    or step.get("thinking_delta")
                    or step.get("thought")
                )
                if thought:
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
                        tool_name = step.get("tool_name") or tool_info.get(
                            "name", "unknown"
                        )
                        params = tool_info.get("parameters", {})
                        if isinstance(params, dict):
                            params_str = ", ".join(
                                f"{k}={v!r}" for k, v in params.items()
                            )
                        else:
                            params_str = str(params)

                        # Track and print tool activation
                        retriever.activate_tool(tool_name)
                        sys.stdout.write(f"\n[Tool: {tool_name}] {params_str}\n")
                        sys.stdout.flush()
                    elif state == "DONE":
                        tool_info = step.get("tool_info", {})
                        output = tool_info.get("output")
                        if output:
                            preview = str(output).strip().replace("\n", " ")
                            if len(preview) > 120:
                                preview = preview[:117] + "..."
                            sys.stdout.write(f"[Tool Result] {preview}\n")
                            sys.stdout.flush()

                # Handle Agent text response chunks
                text_delta = step.get("text_delta")
                if text_delta:
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
                print(
                    f"\n\n[*] Result: {status} in {duration:.1f}s | "
                    f"Tokens: {in_tokens} in / {out_tokens} out (thinking: {thinking_tokens})"
                )

        await process.wait()
        await stderr_task

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


def parse_args() -> argparse.Namespace:
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
        help="Comma-separated list of explicit tools to activate upfront (e.g. view_file,write_to_file)",
    )
    parser.add_argument(
        "--dangerously-skip-permissions",
        "--skip-permissions",
        action="store_true",
        dest="skip_permissions",
        help="Auto-approve all tool permission requests",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    tool_mode = "direct_qa" if args.direct_qa else "dynamic"
    requested_tools = [t.strip() for t in args.tools.split(",") if t.strip()]

    await run_agy_stream(
        prompt=args.prompt,
        tool_mode=tool_mode,
        requested_tools=requested_tools,
        skip_permissions=args.skip_permissions,
    )


if __name__ == "__main__":
    asyncio.run(main())
