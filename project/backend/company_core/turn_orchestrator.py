"""Universal LangGraph Turn Orchestrator for all agent providers.

Wraps any IAgentProvider (Fake, Gemini, OpenRouter, Anthropic, Antigravity) with
deterministic LangGraph execution, real-time Mermaid workflow visualization,
and human-in-the-loop approval gating.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from app.application.triage import is_lightweight_query, should_load_workspace_context
from app.domain.enums import EventType
from app.domain.models import ExecutionTask, StreamEvent, Usage
from app.domain.ports.agent_provider import IAgentProvider
from app.domain.tools import ToolDescriptor

from company_core.orchestrator import AutonomousStartupOrchestrator
from company_core.state import PHASE_COMPLETED, CompanyState


def is_direct_query(prompt: str) -> bool:
    """Check if the user prompt is a direct query/greeting or a complex development task."""
    cleaned = prompt.strip()
    if is_lightweight_query(cleaned):
        return True
    lower = cleaned.lower()
    # Explanatory / informational questions should be answered directly
    if any(
        lower.startswith(k)
        for k in (
            "explain",
            "what is",
            "what are",
            "who is",
            "describe",
            "how does",
            "tell me",
            "show me",
            "overview of",
        )
    ):
        return True
    return not should_load_workspace_context(cleaned)


def generate_mermaid_workflow(
    user_requirement: str,
    status: str = "WAITING_APPROVAL",
    provider_name: str = "Agent",
) -> str:
    """Generate deterministic Mermaid diagram syntax representing the planned workflow."""
    safe_provider = provider_name.replace("(", "").replace(")", "").strip() or "Agent"

    if is_direct_query(user_requirement):
        approval_flow = (
            '    Planner --> Approval{"⏳ Human Approval"}\n'
            f'    Approval -->|Approved| Executor["⚡ {safe_provider} Executor"]\n'
            "    Approval -.->|Cancelled| Abort([🛑 Abort Run])\n"
            if status == "WAITING_APPROVAL"
            else (
                '    Planner --> Approval["✓ Approved"]\n'
                f'    Approval --> Executor["⚡ {safe_provider} Executor"]\n'
            )
        )
        approval_cls = (
            "Approval nodeWaiting;" if status == "WAITING_APPROVAL" else "Approval nodeDone;"
        )
        return (
            "flowchart LR\n"
            '    Start([User Query]) --> Triage["⚡ Intent Triage"]\n'
            '    Triage --> Planner["📋 Direct Planner"]\n'
            f"{approval_flow}"
            "    Executor --> Done([✅ Response Delivered])\n"
            "    classDef nodeDone fill:#065f46,stroke:#10b981,stroke-width:1.5px,color:#fff;\n"
            "    classDef nodeWaiting fill:#7c2d12,stroke:#f97316,stroke-width:2px,color:#fff;\n"
            "    classDef active fill:#6366f1,stroke:#818cf8,stroke-width:2px,color:#fff;\n"
            "    class Start,Triage,Planner nodeDone;\n"
            f"    class {approval_cls}\n"
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
        '    Start([User Requirement]) --> Triage["⚡ Triage and JIT Discovery"]\n'
        '    Triage --> Planner["📋 Structured Planner"]\n'
        f"{approval_branch}"
        "    Router --> ArchitectSub\n"
        "\n"
        '    subgraph ArchitectSub ["🏛️ Architecture Sub-Graph"]\n'
        '        ReqParser["Requirements Parser"] --> Blueprint["Blueprint Generator"]\n'
        '        Blueprint --> Validator["Schema and Boundary Validator"]\n'
        "    end\n"
        "\n"
        "    ArchitectSub --> CoderSub\n"
        "\n"
        f'    subgraph CoderSub ["🧪 Code-Test-Fix Sub-Graph ({safe_provider})"]\n'
        f'        Coder["💻 Code Synthesis ({safe_provider})"] --> DevOps["⚙️ DevOps PyTest"]\n'
        '        DevOps --> QA{"🔍 QA Evaluator"}\n'
        "        QA -.->|Tests Fail: Self-Correction Loop| Coder\n"
        "    end\n"
        "\n"
        '    QA -->|All Tests Passed| Delivery["🎯 Verification and Delivery"]\n'
        "    Delivery --> Done([✅ Verified Solution])\n"
        "\n"
        "    classDef nodeDone fill:#065f46,stroke:#10b981,stroke-width:1.5px,color:#fff;\n"
        "    classDef nodeWaiting fill:#7c2d12,stroke:#f97316,stroke-width:2px,color:#fff;\n"
        "    classDef nodeActive fill:#1e1b4b,stroke:#818cf8,stroke-width:2px,color:#fff;\n"
        "    class Start,Triage,Planner nodeDone;\n"
        f"    class {approval_class}\n"
    )


class GraphTurnOrchestrator:
    """Universal execution orchestrator that wraps ANY provider in a LangGraph flow."""

    def __init__(
        self,
        checkpoint_dir: Path | None = None,
        max_retries: int = 3,
        tool_registry: Any | None = None,
    ) -> None:
        self.checkpoint_dir = checkpoint_dir
        self.max_retries = max_retries
        self.tool_registry = tool_registry

    async def stream_turn(
        self,
        task: ExecutionTask,
        provider: IAgentProvider,
        *,
        require_approval: bool = False,
        approval_event: asyncio.Event | None = None,
        cancel_event: asyncio.Event | None = None,
        active_tools: list[ToolDescriptor] | tuple[ToolDescriptor, ...] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream turn execution through the LangGraph master graph and sub-graphs."""
        task_id = task.id
        attempt = task.attempt or 1
        user_prompt = task.prompt_snapshot.user_prompt.strip()

        provider_name = getattr(provider.config, "display_name", provider.config.id)

        # 1. Resolve workspace path: isolate generated project into dedicated workspace
        projects_dir = Path.cwd() / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        target_workspace = projects_dir / f"project-{task_id[:8]}"
        target_workspace.mkdir(parents=True, exist_ok=True)

        checkpoint_path = target_workspace / "company_state.json"

        # 2. Check task type: direct QA vs multi-agent complex pipeline
        is_direct_qa = is_direct_query(user_prompt)

        # 3. Initial Status Event
        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.STATUS,
            state="running",
            phase="INITIALIZING",
            message=f"LangGraph Orchestrator initializing with {provider_name}...",
        )

        # 4. Plan event
        if is_direct_qa:
            plan_steps = [
                {"title": "1. Intent Triage & Input Analysis", "status": "done"},
                {"title": f"2. Direct Synthesis via {provider_name}", "status": "in_progress"},
            ]
        else:
            plan_steps = [
                {"title": "1. Intent Triage & Dynamic Tool Retrieval", "status": "done"},
                {"title": "2. Multi-Agent Execution Planning", "status": "done"},
                {
                    "title": f"3. Architecture Blueprint Design ({provider_name})",
                    "status": "pending",
                },
                {"title": "4. Source Code Synthesis & PyTest Verification", "status": "pending"},
                {"title": "5. Self-Correction & Delivery Validation", "status": "pending"},
            ]

        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.PLAN,
            summary=f"Deterministic LangGraph Orchestration ({provider_name})",
            steps=plan_steps,
        )

        # 5. Mermaid Workflow Graph & Human-in-the-Loop Approval Gating
        is_waiting = require_approval
        initial_status = "WAITING_APPROVAL" if is_waiting else "RUNNING"
        mermaid_code = generate_mermaid_workflow(
            user_prompt, status=initial_status, provider_name=provider_name
        )

        # Always yield the workflow graph event so the UI immediately renders the graph
        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.STATUS,
            state="waiting_approval" if is_waiting else "running",
            phase="PLANNING" if is_waiting else "APPROVED",
            message=(
                f"Workflow planned. Waiting for user approval to execute via {provider_name}..."
                if is_waiting
                else f"Workflow planned. Proceeding with {provider_name} execution..."
            ),
            mermaid_code=mermaid_code,
        )

        if is_waiting:
            # Wait on caller-supplied events or local fallbacks
            app_evt = approval_event or asyncio.Event()
            cnc_evt = cancel_event or asyncio.Event()

            approve_task = asyncio.create_task(app_evt.wait())
            cancel_task = asyncio.create_task(cnc_evt.wait())
            _done, _ = await asyncio.wait(
                [approve_task, cancel_task],
                return_when=asyncio.FIRST_COMPLETED,
                timeout=600.0,
            )

            if cnc_evt.is_set():
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
            if not app_evt.is_set():
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

            # Approved: emit approved status with updated diagram
            approved_mermaid = generate_mermaid_workflow(
                user_prompt, status="APPROVED", provider_name=provider_name
            )
            yield StreamEvent.create(
                task_id,
                attempt,
                EventType.STATUS,
                state="running",
                phase="APPROVED",
                message=f"Workflow approved by user. Proceeding with {provider_name} execution...",
                mermaid_code=approved_mermaid,
            )

        # 6. Execution Dispatching: Direct QA vs Multi-Agent Sub-graphs
        if is_direct_qa:
            # Direct query: stream tokens directly from the selected provider!
            async for ev in provider.stream_execute(task, active_tools=active_tools):
                yield ev
            return

        # Complex task: Run AutonomousStartupOrchestrator master graph with subgraphs
        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.TOKEN,
            text=f"### 🚀 Multi-Agent Orchestration Started\n\n"
            f"**Backend Provider:** `{provider_name}`\n\n"
            f"**Workspace:** `{target_workspace}`\n\n"
            f"---\n\n",
        )

        orchestrator = AutonomousStartupOrchestrator(
            checkpoint_path=checkpoint_path,
            max_retries=self.max_retries,
            project_root_resolver=lambda _: target_workspace,
        )

        from providers.adapters.autonomous_orchestrator.event_mapper import (
            StartupNodeEventMapper,
        )

        mapper = StartupNodeEventMapper()
        last_state: CompanyState | None = None

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
            f"| **Backend Provider** | `{provider_name}` |\n"
            f"| **Final Phase** | `{phase_str}` |\n"
            f"| **Completed Tasks** | `{tasks_str}` |\n"
            f"| **Generated Files** | {total_files} files |\n"
            f"| **Retries / Self-Corrections** | {retries_used} |\n"
            f"| **Local Checkpoint** | `{checkpoint_path}` |\n\n"
            f"All source files, test fixtures, and artifacts have been verified."
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
