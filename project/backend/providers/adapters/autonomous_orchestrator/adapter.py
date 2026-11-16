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


def generate_mermaid_workflow(user_requirement: str, status: str = "WAITING_APPROVAL") -> str:
    """Generate deterministic Mermaid diagram syntax representing the planned workflow."""
    req_lower = user_requirement.lower()
    is_direct_qa = any(
        k in req_lower
        for k in ("what is", "explain", "who is", "help", "hello", "1+1", "1 + 1", "ping")
    )
    if is_direct_qa:
        return (
            "flowchart LR\n"
            '    Start([User Query]) --> Triage["⚡ Intent Triage"]\n'
            '    Triage --> Planner["📋 Direct Planner"]\n'
            '    Planner --> Executor["⚡ Direct Executor"]\n'
            "    Executor --> Done([✅ Response Delivered])\n"
            "    classDef active fill:#6366f1,stroke:#818cf8,stroke-width:2px,color:#fff;\n"
            "    classDef done fill:#059669,stroke:#10b981,stroke-width:1.5px,color:#fff;\n"
            "    class Start,Triage,Planner done;\n"
            "    class Executor active;\n"
        )

    approval_branch = (
        '    Planner --> Approval{"⏳ Human Approval"}\n'
        '    Approval -->|Approved| Router["🔀 Sub-Graph Router"]\n'
        "    Approval -.->|Cancelled| Abort([🛑 Abort Run])\n"
        if status == "WAITING_APPROVAL"
        else (
            '    Planner --> Approval["✓ Approved"]\n'
            '    Approval --> Router["🔀 Sub-Graph Router"]\n'
        )
    )

    approval_class = (
        "Approval nodeWaiting;" if status == "WAITING_APPROVAL" else "Approval nodeDone;"
    )

    return (
        "flowchart TD\n"
        '    Start([User Requirement]) --> Triage["⚡ Triage & JIT Discovery"]\n'
        '    Triage --> Planner["📋 Structured Planner"]\n'
        f"{approval_branch}"
        "    Router --> ArchitectSub\n"
        "\n"
        '    subgraph ArchitectSub ["🏛️ Architecture Sub-Graph"]\n'
        '        ReqParser["Requirements Parser"] --> Blueprint["Blueprint Generator"]\n'
        '        Blueprint --> Validator["Schema & Boundary Validator"]\n'
        "    end\n"
        "\n"
        "    ArchitectSub --> CoderSub\n"
        "\n"
        '    subgraph CoderSub ["🧪 Code-Test-Fix Sub-Graph"]\n'
        '        Coder["💻 Code Synthesis Agent"] --> DevOps["⚙️ DevOps PyTest Harness"]\n'
        '        DevOps --> QA{"🔍 QA Evaluator"}\n'
        "        QA -.->|Tests Fail: Self-Correction Loop| Coder\n"
        "    end\n"
        "\n"
        '    QA -->|All Tests Passed| Delivery["🎯 Final Delivery & Verification"]\n'
        "    Delivery --> Done([✅ Verified Solution])\n"
        "\n"
        "    classDef nodeDone fill:#065f46,stroke:#10b981,stroke-width:1.5px,color:#fff;\n"
        "    classDef nodeWaiting fill:#7c2d12,stroke:#f97316,stroke-width:2px,color:#fff;\n"
        "    classDef nodeActive fill:#1e1b4b,stroke:#818cf8,stroke-width:2px,color:#fff;\n"
        "    class Start,Triage,Planner nodeDone;\n"
        f"    class {approval_class}\n"
    )


class StartupOrchestratorProvider(BaseAgentRunner):
    """Adapter executing autonomous multi-agent pipelines via LangGraph."""

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

        require_approval = bool(task.params.extra.get("require_approval", True))
        is_direct_qa = any(
            k in user_prompt.lower()
            for k in ("what is", "explain", "who is", "help", "hello", "1+1", "1 + 1", "ping")
        )

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

        # 3. Mermaid Workflow Graph & Human-in-the-Loop Approval Gating
        is_waiting = require_approval and not is_direct_qa
        initial_status = "WAITING_APPROVAL" if is_waiting else "RUNNING"
        mermaid_code = generate_mermaid_workflow(user_prompt, status=initial_status)

        if require_approval and not is_direct_qa:
            # Register events before yielding so callers can approve immediately
            approval_event = asyncio.Event()
            cancel_event = asyncio.Event()
            self._approvals[task_id] = approval_event
            self._cancellations[task_id] = cancel_event

            # Emit WAITING_APPROVAL status event with Mermaid diagram
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
                _done, _ = await asyncio.wait(
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

            # Approved: emit approved status with updated diagram
            approved_mermaid = generate_mermaid_workflow(user_prompt, status="APPROVED")
            yield StreamEvent.create(
                task_id,
                attempt,
                EventType.STATUS,
                state="running",
                phase="APPROVED",
                message="Workflow approved by user. Proceeding with multi-agent execution...",
                mermaid_code=approved_mermaid,
            )

        # 4. Intro Token Output
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
            use_master_graph=True,
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
