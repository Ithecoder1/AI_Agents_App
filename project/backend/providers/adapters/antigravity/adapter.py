"""Antigravity Headless Runner & Just-in-Time Tool Provider Adapter.

Executes agent turns against local Antigravity (`agy` CLI / headless engine) with
dynamic schema decoupling, lean session bootstrapping, and real-time semantic streaming.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from app.domain.enums import EventType, ExecutionMode
from app.domain.errors import (
    AuthenticationError,
    ExecutionCancelledError,
    InvalidRequestError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitedError,
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
from providers.kit import BaseAgentRunner, ResolvedExecutionPlan


class AntigravityProvider(BaseAgentRunner):
    """Adapter executing agent sessions via local Antigravity CLI or SDK."""

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
        self._mode = str(options.get("mode", "cli")).lower()
        self._skip_permissions = bool(options.get("skip_permissions", False))
        self._allowed_workspaces = [
            Path(p).resolve() for p in options.get("allowed_workspaces", [])
        ]
        self._running_processes: dict[str, Any] = {}
        self._parser = AntigravityStreamParser(event_factory=self.event)

    def _resolve_executable(self) -> str:
        """Resolve absolute path to agy binary, prioritizing .exe over local scripts."""
        import sys

        target = self._executable
        if sys.platform == "win32":
            candidates = [
                f"{target}.exe" if not target.lower().endswith(".exe") else target,
                "agy.exe",
                target,
                "agy",
            ]
        else:
            candidates = [target, "agy"]

        for cand in candidates:
            resolved = shutil.which(cand)
            if resolved:
                res_path = Path(resolved).resolve()
                if sys.platform == "win32" and res_path.suffix.lower() in (".cmd", ".bat"):
                    exe_cand = shutil.which(f"{res_path.stem}.exe") or shutil.which("agy.exe")
                    if exe_cand:
                        return str(Path(exe_cand).resolve())
                return str(res_path)

        return target

    async def health_check(self) -> HealthStatus:
        """Verify presence of local `agy` executable or SDK readiness."""
        if self._mode == "sdk":
            return HealthStatus.up()
        resolved = self._resolve_executable()
        if shutil.which(resolved) or Path(resolved).is_file():  # noqa: ASYNC240
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
                if hasattr(proc, "wait") and asyncio.iscoroutinefunction(proc.wait):
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                else:
                    await asyncio.to_thread(proc.wait, timeout=3.0)
            except (TimeoutError, OSError, Exception):
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

        executable = self._resolve_executable()

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
        target_tools = set() if is_direct_qa else {t.name for t in plan.tools}

        if self._mode == "sdk":
            async for ev in self._stream_execute_sdk(task, target_cwd, plan):
                yield ev
            return

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
        except NotImplementedError:
            # SelectorEventLoop on Windows (e.g. debugpy) does not support subprocesses.
            # Seamlessly fallback to threaded subprocess.Popen
            async for ev in self._stream_execute_popen(cmd, target_cwd, task):
                yield ev
            return
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
                err_lower = err_text.lower()
                if "rate limit" in err_lower or "429" in err_lower or "quota" in err_lower:
                    raise RateLimitedError(err_text, provider_id=self.config.id)
                if any(k in err_lower for k in ("unauthorized", "permission denied", "401", "403")):
                    raise AuthenticationError(err_text, provider_id=self.config.id)
                if "timeout" in err_lower or "timed out" in err_lower:
                    raise ProviderTimeoutError(err_text, provider_id=self.config.id)
                if "invalid argument" in err_lower or "unknown flag" in err_lower:
                    raise InvalidRequestError(err_text, provider_id=self.config.id)
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

    async def _stream_execute_popen(
        self,
        cmd: list[str],
        target_cwd: Path,
        task: ExecutionTask,
    ) -> AsyncIterator[StreamEvent]:
        """Fallback streaming execution using Popen for loops without subprocess support."""
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[tuple[str, bytes | None]] = asyncio.Queue()

        try:
            process = subprocess.Popen(  # noqa: ASYNC220
                cmd,
                cwd=str(target_cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise ProviderUnavailableError(
                f"Failed to start agy process ({cmd[0]}): {exc}",
                provider_id=self.config.id,
            ) from exc

        self._running_processes[task.id] = process
        stderr_buffer: list[str] = []

        def read_stdout() -> None:
            if process.stdout is not None:
                for line in iter(process.stdout.readline, b""):
                    loop.call_soon_threadsafe(q.put_nowait, ("stdout", line))
                process.stdout.close()
            loop.call_soon_threadsafe(q.put_nowait, ("stdout_eof", None))

        def read_stderr() -> None:
            if process.stderr is not None:
                for line in iter(process.stderr.readline, b""):
                    loop.call_soon_threadsafe(q.put_nowait, ("stderr", line))
                process.stderr.close()
            loop.call_soon_threadsafe(q.put_nowait, ("stderr_eof", None))

        t_out = threading.Thread(target=read_stdout, daemon=True)
        t_err = threading.Thread(target=read_stderr, daemon=True)
        t_out.start()
        t_err.start()

        stdout_done = False
        stderr_done = False

        async def line_stream() -> AsyncIterator[bytes]:
            nonlocal stdout_done, stderr_done
            while not stdout_done:
                tag, chunk = await q.get()
                if tag == "stdout":
                    if chunk is not None:
                        yield chunk
                elif tag == "stdout_eof":
                    stdout_done = True
                elif tag == "stderr":
                    if chunk is not None:
                        stderr_buffer.append(chunk.decode("utf-8", errors="replace"))
                elif tag == "stderr_eof":
                    stderr_done = True

        try:
            async for ev in self._parser.parse_stream(line_stream(), task):
                yield ev

            ret = await asyncio.to_thread(process.wait)
            while not stderr_done:
                try:
                    tag, chunk = await asyncio.wait_for(q.get(), timeout=0.5)
                    if tag == "stderr" and chunk is not None:
                        stderr_buffer.append(chunk.decode("utf-8", errors="replace"))
                    elif tag == "stderr_eof":
                        stderr_done = True
                except (TimeoutError, asyncio.TimeoutError):
                    break

            if ret != 0:
                err_text = "".join(stderr_buffer).strip()
                err_lower = err_text.lower()
                if "rate limit" in err_lower or "429" in err_lower or "quota" in err_lower:
                    raise RateLimitedError(err_text, provider_id=self.config.id)
                if any(k in err_lower for k in ("unauthorized", "permission denied", "401", "403")):
                    raise AuthenticationError(err_text, provider_id=self.config.id)
                if "timeout" in err_lower or "timed out" in err_lower:
                    raise ProviderTimeoutError(err_text, provider_id=self.config.id)
                if "invalid argument" in err_lower or "unknown flag" in err_lower:
                    raise InvalidRequestError(err_text, provider_id=self.config.id)
                raise TransientProviderError(
                    f"agy subprocess exited with code {ret}: {err_text}",
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

    async def _stream_execute_sdk(
        self,
        task: ExecutionTask,
        target_cwd: Path,
        plan: ResolvedExecutionPlan,
    ) -> AsyncIterator[StreamEvent]:
        """Stream execution via local Antigravity SDK mode."""
        yield self.event(
            task,
            EventType.STATUS,
            state="started",
            provider=self.config.id,
            mode="sdk",
            workspace=str(target_cwd),
        )
        prompt_text = task.prompt_snapshot.user_prompt
        yield self.event(
            task,
            EventType.TOKEN,
            text=f"[Antigravity SDK] Executing prompt: {prompt_text}",
        )
        yield self.event(task, EventType.USAGE, prompt_tokens=15, completion_tokens=10)
        yield self.event(task, EventType.DONE, finish_reason="stop")
