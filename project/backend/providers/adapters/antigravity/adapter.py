"""Antigravity Headless Runner & Just-in-Time Tool Provider Adapter.

Executes agent turns against local Antigravity (`agy` CLI / headless engine) with
dynamic schema decoupling, lean session bootstrapping, and real-time semantic streaming.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

from app.domain.enums import EventType, ExecutionMode
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
)
from app.domain.ports.agent_provider import ProviderDeps
from app.domain.tools import ToolDescriptor, ToolRetrievalPolicy

from providers.adapters.antigravity.command import AntigravityCommandBuilder
from providers.adapters.antigravity.stream import AntigravityStreamParser
from providers.kit import BaseAgentRunner


class AntigravityProvider(BaseAgentRunner):
    """Adapter executing agent sessions via local Antigravity CLI (`agy -p`)."""

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
        self._parser = AntigravityStreamParser(event_factory=self.event)

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

    async def stream_execute(
        self,
        task: ExecutionTask,
        *,
        active_tools: list[ToolDescriptor] | tuple[ToolDescriptor, ...] | None = None,
        retrieval_policy: ToolRetrievalPolicy | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Execute prompt via `agy -p` subprocess, yielding real-time semantic StreamEvents."""
        try:
            _system, turns = task.prompt_snapshot.render_messages()
            user_prompt = turns[-1].content if turns else ""
        except ValidationError as exc:
            raise InvalidRequestError(str(exc), provider_id=self.config.id) from exc

        executable = shutil.which(self._executable) or shutil.which("agy") or self._executable

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

        plan = self.resolve_execution_plan(
            task,
            active_tools=active_tools,
            retrieval_policy=retrieval_policy,
        )
        is_direct_qa = (plan.mode == ExecutionMode.DIRECT_QA) or plan.suppress_tools
        target_tools = {t.name for t in plan.tools}

        final_prompt = AntigravityCommandBuilder.decorate_prompt(
            user_prompt, is_direct_qa, target_tools
        )

        raw_extra = (
            task.params.extra.get("extra_args") or task.params.extra.get("passthrough_args") or []
        )
        extra_args: list[str] = (
            [str(a) for a in raw_extra] if isinstance(raw_extra, list) else [str(raw_extra)]
        )
        skip_perm = self._skip_permissions or bool(task.params.extra.get("skip_permissions"))

        cmd = AntigravityCommandBuilder.build_command(
            executable,
            final_prompt,
            is_direct_qa=is_direct_qa,
            target_tools=target_tools,
            skip_permissions=skip_perm,
            extra_args=extra_args,
            mode=task.params.extra.get("mode"),
        )

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

        try:
            assert process.stdout is not None
            async for ev in self._parser.parse_stream(process.stdout, task):
                yield ev

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
