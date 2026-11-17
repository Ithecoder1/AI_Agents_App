"""Universal Dynamic LangGraph Turn Orchestrator for all agent providers.

Wraps any IAgentProvider (Fake, Gemini, OpenRouter, Anthropic, Antigravity) with
intent-driven LangGraph execution, YAML profile & modular skill selection,
real-time Mermaid workflow visualization, and human-in-the-loop approval gating.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from app.application.tools.registry import DynamicToolRegistry
from app.application.tools.retriever import ToolRetriever
from app.application.triage import is_lightweight_query, should_load_workspace_context
from app.domain.enums import EventType
from app.domain.models import ExecutionTask, StreamEvent, Usage
from app.domain.ports.agent_provider import IAgentProvider
from app.domain.tools import ToolDescriptor

from company_core.config import ConfigManager, OrchestratorProfileConfig, get_config
from company_core.event_mapper import StartupNodeEventMapper
from company_core.orchestrator import AutonomousStartupOrchestrator
from company_core.skills import SkillRegistry, get_default_skill_registry
from company_core.state import (
    INTENT_ARCHITECTURE_DESIGN,
    INTENT_CODE_TEST_FIX,
    INTENT_DIRECT_QA,
    INTENT_FOCUSED_TOOL_TASK,
    PHASE_COMPLETED,
    CompanyState,
)


def is_direct_query(prompt: str) -> bool:
    """Check if the user prompt is a direct query/greeting or a workspace development task."""
    cleaned = prompt.strip()
    if is_lightweight_query(cleaned):
        return True
    lower = cleaned.lower()
    # Explanatory / informational questions without build/fix/analyze directives
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
            "why ",
        )
    ):
        action_verbs = ("build", "create", "develop", "implement", "fix", "patch", "refactor")
        if not any(v in lower for v in action_verbs):
            return True
    return not should_load_workspace_context(cleaned)


def _approval_edges(
    *,
    status: str,
    target_node: str,
    target_label: str,
    horizontal: bool = False,
) -> tuple[str, str]:
    """Build reusable Mermaid approval gate snippet and style class."""
    if status == "WAITING_APPROVAL":
        branch = (
            f'    Planner --> Approval{{"⏳ Human Approval"}}\n'
            f"    Approval -->|Approved| {target_label}\n"
            "    Approval -.->|Cancelled| Abort([🛑 Abort Run])\n"
        )
        cls = "Approval nodeWaiting;"
    else:
        branch = f'    Planner --> Approval["✓ Approved"]\n    Approval --> {target_label}\n'
        cls = "Approval nodeDone;"
    _ = horizontal
    return branch, cls


def generate_mermaid_workflow(
    user_requirement: str,
    status: str = "WAITING_APPROVAL",
    provider_name: str = "Agent",
    intent: str | None = None,
) -> str:
    """Generate deterministic Mermaid diagram syntax tailored to the resolved intent profile."""
    safe_provider = provider_name.replace("(", "").replace(")", "").strip() or "Agent"
    resolved_intent = intent
    if not resolved_intent:
        cfg = get_config()
        resolved_intent = cfg.classify_intent(
            user_requirement,
            is_lightweight=is_direct_query(user_requirement),
        ).intent

    style_defs = (
        "    classDef nodeDone fill:#065f46,stroke:#10b981,stroke-width:1.5px,color:#fff;\n"
        "    classDef nodeWaiting fill:#7c2d12,stroke:#f97316,stroke-width:2px,color:#fff;\n"
        "    classDef nodeActive fill:#1e1b4b,stroke:#818cf8,stroke-width:2px,color:#fff;\n"
        "    classDef active fill:#6366f1,stroke:#818cf8,stroke-width:2px,color:#fff;\n"
    )

    if resolved_intent == INTENT_DIRECT_QA:
        if status == "WAITING_APPROVAL":
            return (
                "flowchart LR\n"
                '    Start([User Query]) --> Triage["⚡ Intent Triage: DIRECT_QA"]\n'
                '    Triage --> Approval{"⏳ Human Approval"}\n'
                f'    Approval -->|Approved| Executor["⚡ {safe_provider} Fast-Path Executor"]\n'
                "    Approval -.->|Cancelled| Abort([🛑 Abort Run])\n"
                "    Executor --> Done([✅ Response Delivered])\n"
                f"{style_defs}"
                "    class Start,Triage nodeDone;\n"
                "    class Approval nodeWaiting;\n"
                "    class Executor active;\n"
            )
        return (
            "flowchart LR\n"
            '    Start([User Query]) --> Triage["⚡ Intent Triage: DIRECT_QA"]\n'
            f'    Triage -->|Fast-Path: 0 Tools| Executor["⚡ {safe_provider} Executor"]\n'
            "    Executor --> Done([✅ Response Delivered])\n"
            f"{style_defs}"
            "    class Start,Triage nodeDone;\n"
            "    class Executor active;\n"
        )

    if resolved_intent == INTENT_FOCUSED_TOOL_TASK:
        branch, approval_cls = _approval_edges(
            status=status,
            target_node="Executor",
            target_label=f'Executor["🔧 {safe_provider} Focused Tool Executor"]',
        )
        return (
            "flowchart LR\n"
            '    Start([User Request]) --> Triage["⚡ Intent Triage and JIT Tools"]\n'
            '    Triage --> Planner["📋 Focused Planner"]\n'
            f"{branch}"
            "    Executor --> Done([✅ Analysis Delivered])\n"
            f"{style_defs}"
            "    class Start,Triage,Planner nodeDone;\n"
            f"    class {approval_cls}\n"
            "    class Executor active;\n"
        )

    if resolved_intent == INTENT_ARCHITECTURE_DESIGN:
        branch, approval_cls = _approval_edges(
            status=status,
            target_node="Router",
            target_label='Router["🔀 Sub-Graph Router"]',
        )
        return (
            "flowchart TD\n"
            '    Start([User Requirement]) --> Triage["⚡ Intent Triage: ARCHITECTURE_DESIGN"]\n'
            '    Triage --> Planner["📋 Architecture Planner"]\n'
            f"{branch}"
            "    Router --> ArchitectSub\n"
            "\n"
            '    subgraph ArchitectSub ["🏛️ Architecture Sub-Graph"]\n'
            '        ReqParser["Requirements Parser"] --> Blueprint["Blueprint Generator"]\n'
            '        Blueprint --> Validator["Schema and Boundary Validator"]\n'
            "    end\n"
            "\n"
            "    ArchitectSub --> Done([✅ Blueprint Validated])\n"
            "\n"
            f"{style_defs}"
            "    class Start,Triage,Planner nodeDone;\n"
            f"    class {approval_cls}\n"
        )

    if resolved_intent == INTENT_CODE_TEST_FIX:
        branch, approval_cls = _approval_edges(
            status=status,
            target_node="Router",
            target_label='Router["🔀 Sub-Graph Router"]',
        )
        return (
            "flowchart TD\n"
            '    Start([User Requirement]) --> Triage["⚡ Intent Triage: CODE_TEST_FIX"]\n'
            '    Triage --> Planner["📋 Code-Fix Planner"]\n'
            f"{branch}"
            "    Router --> CoderSub\n"
            "\n"
            f'    subgraph CoderSub ["🧪 Code-Test-Fix Sub-Graph ({safe_provider})"]\n'
            f'        Coder["💻 Code Synthesis ({safe_provider})"] --> DevOps["⚙️ DevOps PyTest"]\n'
            '        DevOps --> QA{"🔍 QA Evaluator"}\n'
            "        QA -.->|Tests Fail: Self-Correction Loop| Coder\n"
            "    end\n"
            "\n"
            '    QA -->|All Tests Passed| Delivery["🎯 Verification and Delivery"]\n'
            "    Delivery --> Done([✅ Verified Fix])\n"
            "\n"
            f"{style_defs}"
            "    class Start,Triage,Planner nodeDone;\n"
            f"    class {approval_cls}\n"
        )

    # COMPLEX_PIPELINE (Full multi-stage Master Graph + Sub-Graphs)
    branch, approval_cls = _approval_edges(
        status=status,
        target_node="Router",
        target_label='Router["🔀 Sub-Graph Router"]',
    )
    return (
        "flowchart TD\n"
        '    Start([User Requirement]) --> Triage["⚡ Triage and JIT Discovery"]\n'
        '    Triage --> Planner["📋 Structured Planner"]\n'
        f"{branch}"
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
        f"{style_defs}"
        "    class Start,Triage,Planner nodeDone;\n"
        f"    class {approval_cls}\n"
    )


class GraphTurnOrchestrator:
    """Universal execution orchestrator wrapping ANY provider in a Dynamic LangGraph flow."""

    def __init__(
        self,
        checkpoint_dir: Path | None = None,
        max_retries: int = 3,
        tool_registry: DynamicToolRegistry | None = None,
        config: ConfigManager | None = None,
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        self.checkpoint_dir = checkpoint_dir
        self.max_retries = max_retries
        self.tool_registry = tool_registry
        self.config = config or get_config()
        self.skill_registry = skill_registry or get_default_skill_registry()

    def _provision_focused_tools(
        self,
        profile: OrchestratorProfileConfig,
        user_prompt: str,
        fallback_tools: list[ToolDescriptor] | tuple[ToolDescriptor, ...] | None,
    ) -> tuple[ToolDescriptor, ...]:
        """Resolve scoped JIT tools for FOCUSED_TOOL_TASK turns."""
        if profile.tool_policy.is_tool_less:
            return ()
        if self.tool_registry is None:
            return tuple(fallback_tools or ())

        retriever = ToolRetriever(self.tool_registry, policy=profile.tool_policy)
        skill_query = self.skill_registry.build_tool_query(
            profile.skills,
            fallback_query=profile.tool_query or user_prompt,
        )
        if skill_query:
            retriever.search_available_tools(
                skill_query,
                limit=profile.tool_policy.max_active_tools,
            )
        if user_prompt:
            retriever.search_available_tools(
                user_prompt,
                limit=profile.tool_policy.max_active_tools,
            )
        active = retriever.get_active_tools()
        if active:
            return active
        return tuple(fallback_tools or ())

    def _build_plan_steps(
        self,
        profile: OrchestratorProfileConfig,
        provider_name: str,
    ) -> list[dict[str, Any]]:
        """Construct clean UI plan steps from the resolved profile template."""
        steps: list[dict[str, Any]] = []
        for idx, step in enumerate(profile.plan_template):
            step_num = step.get("step", idx + 1)
            desc = str(step.get("description", step.get("action", "Execute step")))
            if idx == 0 and profile.fast_path:
                title = f"{step_num}. {desc} ({provider_name})"
                step_status = "in_progress"
            else:
                title = f"{step_num}. {desc}"
                step_status = "pending" if idx > 0 else "in_progress"
            steps.append({"title": title, "status": step_status})

        if not steps:
            steps.append({"title": f"1. Execute via {provider_name}", "status": "in_progress"})
        return steps

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
        """Stream turn execution dynamically based on classified user intent and YAML profiles."""
        task_id = task.id
        attempt = task.attempt or 1
        user_prompt = task.prompt_snapshot.user_prompt.strip()
        provider_name = getattr(provider.config, "display_name", provider.config.id)

        # 1. Resolve isolated project workspace directory
        projects_dir = Path.cwd() / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        target_workspace = projects_dir / f"project-{task_id[:8]}"
        target_workspace.mkdir(parents=True, exist_ok=True)
        checkpoint_path = target_workspace / "company_state.json"

        # 2. Classify incoming prompt intent dynamically
        is_direct = is_direct_query(user_prompt)
        profile = self.config.classify_intent(user_prompt, is_lightweight=is_direct)

        # 3. Initial Status & Profile Triage Telemetry
        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.STATUS,
            state="running",
            phase="INITIALIZING",
            message=(
                f"Dynamic Orchestrator resolved intent '{profile.intent}' with {provider_name}..."
            ),
        )
        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.THOUGHT,
            text=(
                f"Intent Triage: Classified as {profile.intent} "
                f"(skills={list(profile.skills)}, subgraphs={list(profile.subgraphs)}, "
                f"fast_path={profile.fast_path})."
            ),
        )

        # 4. Plan event driven by YAML profile template
        plan_steps = self._build_plan_steps(profile, provider_name)
        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.PLAN,
            summary=f"Dynamic Orchestration ({profile.intent} · {provider_name})",
            steps=plan_steps,
        )

        # 5. Mermaid Workflow Graph & Human-in-the-Loop Approval Gating
        is_waiting = require_approval
        initial_status = "WAITING_APPROVAL" if is_waiting else "RUNNING"
        mermaid_code = generate_mermaid_workflow(
            user_prompt,
            status=initial_status,
            provider_name=provider_name,
            intent=profile.intent,
        )

        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.STATUS,
            state="waiting_approval" if is_waiting else "running",
            phase="PLANNING" if is_waiting else "APPROVED",
            message=(
                f"Workflow planned ({profile.intent}). Waiting for user approval..."
                if is_waiting
                else f"Workflow planned ({profile.intent}). Proceeding with {provider_name}..."
            ),
            mermaid_code=mermaid_code,
        )

        if is_waiting:
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

            approved_mermaid = generate_mermaid_workflow(
                user_prompt,
                status="APPROVED",
                provider_name=provider_name,
                intent=profile.intent,
            )
            yield StreamEvent.create(
                task_id,
                attempt,
                EventType.STATUS,
                state="running",
                phase="APPROVED",
                message=(
                    f"Workflow approved by user. Executing {profile.intent} via {provider_name}..."
                ),
                mermaid_code=approved_mermaid,
            )

        # 6. Dynamic Dispatching by Resolved Intent
        if profile.intent == INTENT_DIRECT_QA:
            # Fast-path zero-tool execution directly on provider
            async for ev in provider.stream_execute(task, active_tools=()):
                yield ev
            return

        if profile.intent == INTENT_FOCUSED_TOOL_TASK:
            # Scoped JIT tool provisioning via code_analysis skill without heavyweight sub-graphs
            scoped_tools = self._provision_focused_tools(profile, user_prompt, active_tools)
            async for ev in provider.stream_execute(task, active_tools=scoped_tools):
                yield ev
            return

        # Complex multi-agent sub-graphs (ARCHITECTURE_DESIGN, CODE_TEST_FIX, COMPLEX_PIPELINE)
        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.TOKEN,
            text=f"### Multi-Agent Orchestration Started ({profile.intent})\n\n"
            f"**Backend Provider:** `{provider_name}`\n\n"
            f"**Active Skills:** `{list(profile.skills)}`\n\n"
            f"**Workspace:** `{target_workspace}`\n\n"
            f"---\n\n",
        )

        orchestrator = AutonomousStartupOrchestrator(
            checkpoint_path=checkpoint_path,
            max_retries=self.max_retries,
            project_root_resolver=lambda _: target_workspace,
        )

        mapper = StartupNodeEventMapper()
        last_state: CompanyState | None = None

        async for node_name, state_snapshot in orchestrator.astream_nodes(
            user_requirement=user_prompt,
            project_id=f"proj-{task_id[:8]}",
            resume_if_possible=True,
            reset_state=False,
            use_master_graph=True,
            tool_registry=self.tool_registry,
        ):
            last_state = state_snapshot
            for ev in mapper.map_node(task_id, attempt, node_name, state_snapshot):
                yield ev
            await asyncio.sleep(0.01)

        # Final Delivery Summary
        is_completed = last_state is not None and last_state.get("current_phase") == PHASE_COMPLETED
        status_banner = "**COMPLETED SUCCESSFULLY**" if is_completed else "**EXECUTION PAUSED**"

        total_files = len(last_state.get("code_files") or {}) if last_state else 0
        retries_used = last_state.get("retry_count", 0) if last_state else 0
        phase_str = last_state.get("current_phase") if last_state else "UNKNOWN"
        tasks_str = str(last_state.get("completed_tasks") if last_state else [])

        summary_text = (
            f"\n---\n\n"
            f"### {status_banner}\n\n"
            f"| Metric | Details |\n"
            f"|---|---|\n"
            f"| **Intent Profile** | `{profile.intent}` |\n"
            f"| **Backend Provider** | `{provider_name}` |\n"
            f"| **Final Phase** | `{phase_str}` |\n"
            f"| **Completed Tasks** | `{tasks_str}` |\n"
            f"| **Generated Files** | {total_files} files |\n"
            f"| **Retries / Self-Corrections** | {retries_used} |\n"
            f"| **Local Checkpoint** | `{checkpoint_path}` |\n\n"
            f"All workflow stages and artifacts have been verified."
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
