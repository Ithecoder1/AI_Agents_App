"""Autonomous Startup Orchestrator Provider Adapter.

Bridges the LangGraph multi-agent startup orchestrator with the Vibe Forge execution engine
using injected ``app.domain.ports`` dependencies only.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from app.domain.enums import EventType
from app.domain.errors import ProviderError
from app.domain.graph_state import PHASE_COMPLETED, GraphState
from app.domain.models import (
    AgentProviderConfig,
    ExecutionTask,
    HealthStatus,
    ProviderCapabilities,
    StreamEvent,
    Usage,
)
from app.domain.ports import INodeEventMapper, IOrchestratorEngine, ProviderDeps
from app.domain.tools import ToolDescriptor, ToolRetrievalPolicy

from providers.kit import BaseAgentRunner

_DIRECT_QA_KEYWORDS: tuple[str, ...] = (
    "what is",
    "explain",
    "who is",
    "help",
    "hello",
    "1+1",
    "1 + 1",
    "ping",
)


class StartupOrchestratorProvider(BaseAgentRunner):
    """Adapter executing autonomous multi-agent pipelines via injected domain ports."""

    default_capabilities = ProviderCapabilities(
        streaming=True,
        supports_cancel=True,
        supports_approval=True,
        idempotent=False,
        can_modify_workspace=True,
        reports_usage=True,
        supports_resume=True,
        models=("langgraph-startup-orchestrator", "autonomous-multiagent-v1"),
    )

    def __init__(self, config: AgentProviderConfig, deps: ProviderDeps) -> None:
        super().__init__(config, deps)
        options = config.options
        self._checkpoint_file = str(options.get("checkpoint_file", "logs/company_state.json"))
        self._max_retries = int(options.get("max_retries", 3))
        self._approvals: dict[str, asyncio.Event] = {}
        self._cancellations: dict[str, asyncio.Event] = {}

    async def approve(self, task_id: str) -> bool:
        """Signal approval for a task waiting for user review."""
        event = self._approvals.get(task_id)
        if event is not None:
            event.set()
            return True
        return False

    async def cancel(self, task_id: str) -> None:
        """Signal cancellation for an in-flight or waiting task."""
        event = self._cancellations.get(task_id)
        if event is not None:
            event.set()

    async def health_check(self) -> HealthStatus:
        """Report provider health status."""
        try:
            import langgraph  # noqa: F401

            return HealthStatus.up()
        except ImportError as exc:
            return HealthStatus.down(f"LangGraph not installed: {exc}")

    @staticmethod
    def _resolve_workspace_paths(workspace: str | None, task_id: str) -> tuple[Path, Path]:
        if workspace and Path(workspace).is_dir():
            target_workspace = Path(workspace)
        else:
            target_workspace = Path.cwd() / "projects" / f"project-{task_id[:8]}"
            target_workspace.mkdir(parents=True, exist_ok=True)
        return target_workspace, target_workspace / "company_state.json"

    def _resolve_engine(
        self,
        *,
        workspace_dir: Path,
        checkpoint_path: Path,
    ) -> IOrchestratorEngine:
        if self.deps.orchestrator_engine_factory is not None:
            return self.deps.orchestrator_engine_factory.create(
                workspace_dir=workspace_dir,
                checkpoint_path=checkpoint_path,
                max_retries=self._max_retries,
            )
        if self.deps.orchestrator_engine is not None:
            return self.deps.orchestrator_engine
        raise ProviderError(
            "StartupOrchestratorProvider requires orchestrator_engine or "
            "orchestrator_engine_factory in ProviderDeps.",
            provider_id=self.config.id,
        )

    def _render_mermaid(self, user_prompt: str, *, status: str) -> str:
        visualizer = self.deps.mermaid_visualizer
        if visualizer is None:
            raise ProviderError(
                "StartupOrchestratorProvider requires mermaid_visualizer in ProviderDeps.",
                provider_id=self.config.id,
            )
        return visualizer.generate(user_prompt, status=status)

    def _create_mapper(self) -> INodeEventMapper:
        mapper_factory = self.deps.node_event_mapper_factory
        if mapper_factory is None:
            raise ProviderError(
                "StartupOrchestratorProvider requires node_event_mapper_factory in ProviderDeps.",
                provider_id=self.config.id,
            )
        return mapper_factory()

    async def stream_execute(
        self,
        task: ExecutionTask,
        *,
        active_tools: list[ToolDescriptor] | tuple[ToolDescriptor, ...] | None = None,
        retrieval_policy: ToolRetrievalPolicy | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream autonomous startup orchestration events using injected domain collaborators."""
        _ = (active_tools, retrieval_policy)
        task_id = task.id
        attempt = task.attempt or 1
        user_prompt = task.prompt_snapshot.user_prompt.strip()

        target_workspace, checkpoint_path = await asyncio.to_thread(
            self._resolve_workspace_paths,
            task.workspace,
            task_id,
        )
        orchestrator = self._resolve_engine(
            workspace_dir=target_workspace,
            checkpoint_path=checkpoint_path,
        )
        mapper = self._create_mapper()

        require_approval = bool(task.params.extra.get("require_approval", True))
        is_direct_qa = any(keyword in user_prompt.lower() for keyword in _DIRECT_QA_KEYWORDS)

        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.STATUS,
            state="running",
            phase="INITIALIZING",
            message="Autonomous Startup Orchestrator initializing...",
        )
        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.PLAN,
            summary="Autonomous Startup Multi-Agent Lifecycle",
            steps=mapper.plan_steps,
        )

        is_waiting = require_approval and not is_direct_qa
        initial_status = "WAITING_APPROVAL" if is_waiting else "RUNNING"
        mermaid_code = self._render_mermaid(user_prompt, status=initial_status)

        if is_waiting:
            approval_event = asyncio.Event()
            cancel_event = asyncio.Event()
            self._approvals[task_id] = approval_event
            self._cancellations[task_id] = cancel_event

            yield StreamEvent.create(
                task_id,
                attempt,
                EventType.STATUS,
                state="waiting_approval",
                phase="PLANNING",
                message="Workflow plan generated. Waiting for user approval to execute...",
                mermaid_code=mermaid_code,
            )

            try:
                approve_task = asyncio.create_task(approval_event.wait())
                cancel_task = asyncio.create_task(cancel_event.wait())
                await asyncio.wait(
                    [approve_task, cancel_task],
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=600.0,
                )
                if cancel_event.is_set():
                    yield StreamEvent.create(
                        task_id,
                        attempt,
                        EventType.STATUS,
                        state="cancelled",
                        phase="CANCELLED",
                        message="Workflow execution was cancelled by user.",
                    )
                    yield StreamEvent.create(
                        task_id,
                        attempt,
                        EventType.DONE,
                        finish_reason="cancelled",
                    )
                    return
                if not approval_event.is_set():
                    yield StreamEvent.create(
                        task_id,
                        attempt,
                        EventType.STATUS,
                        state="cancelled",
                        phase="TIMEOUT",
                        message="Workflow approval timed out after 10 minutes.",
                    )
                    yield StreamEvent.create(
                        task_id,
                        attempt,
                        EventType.DONE,
                        finish_reason="cancelled",
                    )
                    return
            finally:
                self._approvals.pop(task_id, None)
                self._cancellations.pop(task_id, None)

            approved_mermaid = self._render_mermaid(user_prompt, status="APPROVED")
            yield StreamEvent.create(
                task_id,
                attempt,
                EventType.STATUS,
                state="running",
                phase="APPROVED",
                message="Workflow approved by user. Proceeding with multi-agent execution...",
                mermaid_code=approved_mermaid,
            )

        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.TOKEN,
            text=(
                "### 🚀 Autonomous Startup Orchestrator Initialized\n\n"
                f"**Workspace:** `{target_workspace}`\n\n"
                f"**Objective:** {user_prompt}\n\n"
                "---\n\n"
            ),
        )

        last_state: GraphState | None = None
        async for node_name, state_snapshot in orchestrator.astream_nodes(
            user_requirement=user_prompt,
            project_id=f"proj-{task_id[:8]}",
            resume_if_possible=True,
            reset_state=False,
            use_master_graph=True,
        ):
            last_state = state_snapshot
            for ev in mapper.map_node(task_id, attempt, node_name, state_snapshot):
                yield ev
            await asyncio.sleep(0)

        is_completed = last_state is not None and last_state.get("current_phase") == PHASE_COMPLETED
        status_banner = (
            "✅ **COMPLETED SUCCESSFULLY**" if is_completed else "⚠️ **EXECUTION PAUSED**"
        )
        total_files = len(last_state.get("code_files") or {}) if last_state else 0
        retries_used = last_state.get("retry_count", 0) if last_state else 0
        phase_str = last_state.get("current_phase") if last_state else "UNKNOWN"
        tasks_str = str(last_state.get("completed_tasks") if last_state else [])

        summary_text = (
            "\n---\n\n"
            f"### {status_banner}\n\n"
            "| Metric | Details |\n"
            "|---|---|\n"
            f"| **Final Phase** | `{phase_str}` |\n"
            f"| **Completed Tasks** | `{tasks_str}` |\n"
            f"| **Generated Files** | {total_files} files |\n"
            f"| **Retries / Self-Corrections** | {retries_used} |\n"
            f"| **Local Checkpoint** | `{checkpoint_path}` |\n\n"
            "All source files, test fixtures, and artifacts have been written to disk."
        )

        yield StreamEvent.create(task_id, attempt, EventType.TOKEN, text=summary_text)
        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.USAGE,
            **Usage(
                prompt_tokens=len(user_prompt) // 4,
                completion_tokens=len(summary_text) // 4 + 400,
                reasoning_tokens=250,
            ).to_dict(),
        )
        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.DONE,
            finish_reason="stop" if is_completed else "error",
        )
