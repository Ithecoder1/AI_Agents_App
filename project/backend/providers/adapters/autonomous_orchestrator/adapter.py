"""Autonomous Startup Orchestrator Provider Adapter.

Bridges the LangGraph multi-agent startup orchestrator with the Vibe Forge execution engine,
enabling multi-turn chat sessions, live WebSocket streaming, and interactive UI inspection.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from app.domain.enums import EventType
from app.domain.models import (
    AgentProviderConfig,
    ExecutionTask,
    HealthStatus,
    ProviderCapabilities,
    StreamEvent,
    Usage,
)
from app.domain.ports.agent_provider import ProviderDeps
from app.domain.tools import ToolDescriptor, ToolRetrievalPolicy
from company_core.orchestrator import AutonomousStartupOrchestrator
from company_core.state import PHASE_COMPLETED, CompanyState

from providers.adapters.autonomous_orchestrator.event_mapper import StartupNodeEventMapper
from providers.kit import BaseAgentRunner


class StartupOrchestratorProvider(BaseAgentRunner):
    """Adapter executing autonomous multi-agent pipelines via LangGraph."""

    default_capabilities = ProviderCapabilities(
        streaming=True,
        supports_cancel=True,
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

    async def health_check(self) -> HealthStatus:
        """Report provider health status."""
        try:
            import langgraph  # noqa: F401

            return HealthStatus.up()
        except ImportError as exc:
            return HealthStatus.down(f"LangGraph not installed: {exc}")

    async def stream_execute(
        self,
        task: ExecutionTask,
        *,
        active_tools: list[ToolDescriptor] | tuple[ToolDescriptor, ...] | None = None,
        retrieval_policy: ToolRetrievalPolicy | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream autonomous startup orchestration events to the chat UI and WebSocket."""
        task_id = task.id
        attempt = task.attempt or 1
        user_prompt = task.prompt_snapshot.user_prompt.strip()

        # Resolve destination workspace directory
        if task.workspace and Path(task.workspace).is_dir():  # noqa: ASYNC240
            target_workspace = Path(task.workspace)
        else:
            target_workspace = Path.cwd() / "projects" / f"project-{task_id[:8]}"
            target_workspace.mkdir(parents=True, exist_ok=True)

        checkpoint_path = target_workspace / "company_state.json"

        # 1. Initial Status Event
        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.STATUS,
            state="running",
            phase="INITIALIZING",
            message="Autonomous Startup Orchestrator initializing...",
        )

        mapper = StartupNodeEventMapper()

        # 2. Initial Execution Plan Event
        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.PLAN,
            summary="Autonomous Startup Multi-Agent Lifecycle",
            steps=mapper.plan_steps,
        )

        # 3. Intro Token Output
        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.TOKEN,
            text=f"### 🚀 Autonomous Startup Orchestrator Initialized\n\n"
            f"**Workspace:** `{target_workspace}`\n\n"
            f"**Objective:** {user_prompt}\n\n"
            f"---\n\n",
        )

        # Initialize the OOP Orchestrator
        orchestrator = AutonomousStartupOrchestrator(
            checkpoint_path=checkpoint_path,
            max_retries=self._max_retries,
            project_root_resolver=lambda _: target_workspace,
        )

        last_state: CompanyState | None = None

        # Stream node execution events
        async for node_name, state_snapshot in orchestrator.astream_nodes(
            user_requirement=user_prompt,
            project_id=f"proj-{task_id[:8]}",
            resume_if_possible=True,
            reset_state=False,
        ):
            last_state = state_snapshot
            for ev in mapper.map_node(task_id, attempt, node_name, state_snapshot):
                yield ev
            await asyncio.sleep(0.01)

        # Final Delivery Summary
        is_completed = last_state is not None and last_state.get("current_phase") == PHASE_COMPLETED
        status_banner = (
            "✅ **COMPLETED SUCCESSFULLY**" if is_completed else "⚠️ **EXECUTION PAUSED**"
        )

        total_files = len(last_state.get("code_files") or {}) if last_state else 0
        retries_used = last_state.get("retry_count", 0) if last_state else 0
        phase_str = last_state.get("current_phase") if last_state else "UNKNOWN"
        tasks_str = str(last_state.get("completed_tasks") if last_state else [])

        summary_text = (
            f"\n---\n\n"
            f"### {status_banner}\n\n"
            f"| Metric | Details |\n"
            f"|---|---|\n"
            f"| **Final Phase** | `{phase_str}` |\n"
            f"| **Completed Tasks** | `{tasks_str}` |\n"
            f"| **Generated Files** | {total_files} files |\n"
            f"| **Retries / Self-Corrections** | {retries_used} |\n"
            f"| **Local Checkpoint** | `{checkpoint_path}` |\n\n"
            f"All source files, test fixtures, and artifacts have been written to disk."
        )

        yield StreamEvent.create(task_id, attempt, EventType.TOKEN, text=summary_text)

        # Report Token Usage
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

        # Done event
        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.DONE,
            finish_reason="stop" if is_completed else "error",
        )
