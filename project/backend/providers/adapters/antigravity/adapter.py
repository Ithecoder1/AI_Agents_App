"""Antigravity Headless Runner & Just-in-Time Tool Provider Adapter.

Executes agent turns against local Antigravity (`agy` CLI / headless engine) with
dynamic schema decoupling, lean session bootstrapping, and real-time semantic streaming.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

from app.domain.enums import EventType
from app.domain.errors import (
    ExecutionCancelledError,
    InvalidRequestError,
    ProviderUnavailableError,
    TransientProviderError,
    ValidationError,
)
from app.domain.models import (
    AgentProviderConfig,
    ExecutionTask,
    HealthStatus,
    ProviderCapabilities,
    StreamEvent,
    Usage,
)
from app.domain.ports.agent_provider import ProviderDeps

from providers.kit import BaseAgentRunner


class AntigravityProvider(BaseAgentRunner):
    """Adapter executing agent sessions via local Antigravity CLI (`agy -p`).

    Adheres to Clean Architecture: depends only on `app.domain` and `providers.kit`.
    Implements Dynamic Tool Retrieval to avoid upfront 60-tool schema injection.
    """

    default_capabilities = ProviderCapabilities(
        streaming=True,
        supports_cancel=True,
        idempotent=False,
        can_modify_workspace=True,
        reports_usage=True,
        models=("gemini-3.5-flash", "gemini-3.6-flash", "default"),
    )

    def __init__(self, config: AgentProviderConfig, deps: ProviderDeps) -> None:
        super().__init__(config, deps)
        options = config.options
        self._executable = str(options.get("executable", "agy"))
        self._skip_permissions = bool(options.get("skip_permissions", False))
        self._allowed_workspaces = [
            Path(p).resolve() for p in options.get("allowed_workspaces", [])
        ]
        self._running_processes: dict[str, asyncio.subprocess.Process] = {}

    async def health_check(self) -> HealthStatus:
        """Verify presence of local `agy` executable."""
        resolved = shutil.which(self._executable) or shutil.which("agy")
        if resolved:
            return HealthStatus.up()
        return HealthStatus.down(
            f"Antigravity CLI executable {self._executable!r} not found in PATH"
        )

    async def cancel(self, task_id: str) -> None:
        """Cancel running agent subprocess."""
        proc = self._running_processes.get(task_id)
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except (TimeoutError, OSError):
                proc.kill()

    async def stream_execute(self, task: ExecutionTask) -> AsyncIterator[StreamEvent]:
        """Execute prompt via `agy -p` subprocess, yielding real-time semantic StreamEvents."""
        try:
            _system, turns = task.prompt_snapshot.render_messages()
            user_prompt = turns[-1].content if turns else ""
        except ValidationError as exc:
            raise InvalidRequestError(str(exc), provider_id=self.config.id) from exc

        # Resolve executable
        executable = shutil.which(self._executable) or shutil.which("agy") or self._executable

        # Workspace security check
        target_cwd = Path.cwd()
        if task.workspace:
            ws_path = Path(task.workspace).resolve()  # noqa: ASYNC240
            if self._allowed_workspaces and not any(
                ws_path == allowed or allowed in ws_path.parents
                for allowed in self._allowed_workspaces
            ):
                raise InvalidRequestError(
                    f"Workspace {ws_path} is outside allowed boundaries: "
                    f"{self._allowed_workspaces}",
                    provider_id=self.config.id,
                )
            target_cwd = ws_path

        # Determine tool mode (Agent Capability Triage)
        # If prompt is a direct QA question or explicitly flagged as text-only,
        # run in lean mode without tool injection.
        is_direct_qa = bool(
            task.params.extra.get("direct_qa")
            or "direct_qa" in user_prompt.lower()
            or task.prompt_snapshot.system_instructions.startswith("DIRECT_QA:")
        )

        # Build CLI command
        cmd = [executable, "-p", user_prompt, "--output-format", "stream-json"]
        if self._skip_permissions or task.params.extra.get("skip_permissions"):
            cmd.append("--dangerously-skip-permissions")

        # Yield initial status event
        mode_label = "direct_qa (0-tool)" if is_direct_qa else "dynamic_tools"
        yield self.event(
            task,
            EventType.STATUS,
            state="started",
            provider=self.config.id,
            mode=mode_label,
            workspace=str(target_cwd),
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(target_cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise ProviderUnavailableError(
                f"Failed to start agy process ({executable}): {exc}",
                provider_id=self.config.id,
            ) from exc

        self._running_processes[task.id] = process
        stderr_buffer: list[str] = []

        async def drain_stderr() -> None:
            if process.stderr is not None:
                async for err_line in process.stderr:
                    stderr_buffer.append(err_line.decode("utf-8", errors="replace"))

        stderr_task = asyncio.create_task(drain_stderr())

        input_tokens = 0
        output_tokens = 0

        try:
            assert process.stdout is not None
            async for raw_line in process.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    # Non-JSON or log line
                    yield self.event(task, EventType.LOG, line=line)
                    continue

                event_name = payload.get("event")

                if event_name == "init":
                    init_meta = payload.get("init", {})
                    conv_id = payload.get("conversation_id", "")
                    yield self.event(
                        task,
                        EventType.STATUS,
                        state="running",
                        conversation_id=conv_id,
                        tools_count=len(init_meta.get("tools", [])),
                    )

                elif event_name == "step_update":
                    step = payload.get("step_update", {})
                    step_type = step.get("step_type")
                    state = step.get("state")

                    # 1. Thought / Thinking tokens
                    thought = (
                        step.get("thought_delta")
                        or step.get("thinking_delta")
                        or step.get("thought")
                    )
                    if thought:
                        yield self.event(task, EventType.THOUGHT, text=thought)

                    # 2. Tool events
                    if step_type == "tool":
                        tool_info = step.get("tool_info", {})
                        tool_name = step.get("tool_name") or tool_info.get("name", "tool")
                        if state == "ACTIVE":
                            yield self.event(
                                task,
                                EventType.TOOL_CALL,
                                name=tool_name,
                                args=tool_info.get("parameters", {}),
                            )
                        elif state == "DONE":
                            yield self.event(
                                task,
                                EventType.TOOL_RESULT,
                                name=tool_name,
                                result=tool_info.get("output", ""),
                            )

                    # 3. Agent text response
                    text_delta = step.get("text_delta")
                    if text_delta:
                        yield self.event(task, EventType.TOKEN, text=text_delta)

                elif event_name == "result":
                    res = payload.get("result", {})
                    usage_data = res.get("usage", {})
                    input_tokens = usage_data.get("input_tokens", 0)
                    output_tokens = usage_data.get("output_tokens", 0)

                    usage = Usage(
                        prompt_tokens=input_tokens,
                        completion_tokens=output_tokens,
                        cost_usd=0.0,
                    )
                    yield self.event(task, EventType.USAGE, **usage.to_dict())

            await process.wait()
            await stderr_task

            if process.returncode != 0:
                err_text = "".join(stderr_buffer).strip()
                raise TransientProviderError(
                    f"agy subprocess exited with code {process.returncode}: {err_text}",
                    provider_id=self.config.id,
                )

            yield self.event(task, EventType.DONE, finish_reason="stop")

        except asyncio.CancelledError:
            await self.cancel(task.id)
            raise ExecutionCancelledError(
                "Execution cancelled by client", provider_id=self.config.id
            ) from None
        finally:
            self._running_processes.pop(task.id, None)
