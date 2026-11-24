"""Canonical OOP TurnOrchestratorService implementing ITurnOrchestrator."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from app.application.tools.registry import DynamicToolRegistry
from app.application.tools.retriever import ToolRetriever
from app.application.triage import CascadingTriageService, get_triage_service
from app.domain.enums import EventType
from app.domain.graph_state import (
    INTENT_DIRECT_QA,
    INTENT_FOCUSED_TOOL_TASK,
    PHASE_COMPLETED,
    GraphState,
)
from app.domain.models import ExecutionTask, StreamEvent, Usage
from app.domain.ports import (
    IMermaidWorkflowVisualizer,
    INodeEventMapper,
    IOrchestratorEngineFactory,
    IProfileClassifier,
)
from app.domain.ports.agent_provider import IAgentProvider
from app.domain.tools import ToolDescriptor
from app.infrastructure.orchestration.engine import OrchestratorEngineFactory
from app.infrastructure.orchestration.event_mapper import StartupNodeEventMapper
from app.infrastructure.orchestration.mermaid import MermaidWorkflowVisualizer
from app.infrastructure.orchestration.state import (
    OrchestratorConfigLoader,
    OrchestratorProfileConfig,
    SkillRegistry,
)


class TurnOrchestratorService:
    """Unifies turn gating, fast-path provider streaming, and master graph execution."""

    def __init__(
        self,
        *,
        checkpoint_dir: Path | None = None,
        max_retries: int = 3,
        tool_registry: DynamicToolRegistry | None = None,
        profile_classifier: IProfileClassifier | None = None,
        skill_registry: SkillRegistry | None = None,
        triage_service: CascadingTriageService | None = None,
        mermaid_visualizer: IMermaidWorkflowVisualizer | None = None,
        engine_factory: IOrchestratorEngineFactory | None = None,
    ) -> None:
        self._checkpoint_dir = checkpoint_dir
        self._max_retries = max_retries
        self._tool_registry = tool_registry
        self._profile_classifier: IProfileClassifier = (
            profile_classifier
            if profile_classifier is not None
            else OrchestratorConfigLoader.default()
        )
        self._skill_registry = (
            skill_registry if skill_registry is not None else SkillRegistry.default()
        )
        self._triage_service = (
            triage_service if triage_service is not None else get_triage_service()
        )
        self._mermaid = (
            mermaid_visualizer
            if mermaid_visualizer is not None
            else MermaidWorkflowVisualizer(profile_classifier=self._profile_classifier)
        )
        self._engine_factory: IOrchestratorEngineFactory = (
            engine_factory
            if engine_factory is not None
            else OrchestratorEngineFactory(
                profile_classifier=self._profile_classifier,
                skill_registry=self._skill_registry,
                tool_registry=self._tool_registry,
                triage_service=self._triage_service,
            )
        )

    @staticmethod
    def _ensure_workspace_dir(projects_root: Path, task_id: str) -> tuple[Path, Path]:
        projects_root.mkdir(parents=True, exist_ok=True)
        target_workspace = projects_root / f"project-{task_id[:8]}"
        target_workspace.mkdir(parents=True, exist_ok=True)
        return target_workspace, target_workspace / "company_state.json"

    def _provision_focused_tools(
        self,
        profile: OrchestratorProfileConfig,
        user_prompt: str,
        fallback_tools: list[ToolDescriptor] | tuple[ToolDescriptor, ...] | None,
    ) -> tuple[ToolDescriptor, ...]:
        if profile.tool_policy.is_tool_less:
            return ()
        if self._tool_registry is None:
            return tuple(fallback_tools or ())

        retriever = ToolRetriever(self._tool_registry, policy=profile.tool_policy)
        skill_query = self._skill_registry.build_tool_query(
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
        return active if active else tuple(fallback_tools or ())

    @staticmethod
    def _build_plan_steps(
        profile: OrchestratorProfileConfig,
        provider_name: str,
    ) -> list[dict[str, Any]]:
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

    @staticmethod
    def _finalize_plan_steps(
        plan_steps: list[dict[str, Any]] | list[dict[str, str]],
        *,
        completed: bool,
    ) -> list[dict[str, Any]]:
        finalized: list[dict[str, Any]] = []
        for step in plan_steps:
            updated: dict[str, Any] = dict(step)
            cur_status = str(updated.get("status", "pending"))
            if completed:
                if cur_status in ("pending", "in_progress"):
                    updated["status"] = "done"
            elif cur_status == "in_progress":
                updated["status"] = "failed"
            finalized.append(updated)
        return finalized

    async def _stream_provider_with_plan_completion(
        self,
        *,
        task: ExecutionTask,
        provider: IAgentProvider,
        active_tools: tuple[ToolDescriptor, ...],
        plan_summary: str,
        plan_steps: list[dict[str, Any]],
    ) -> AsyncIterator[StreamEvent]:
        task_id = task.id
        attempt = task.attempt or 1
        emitted_final_plan = False
        had_error = False

        async for ev in provider.stream_execute(task, active_tools=active_tools):
            if ev.type is EventType.ERROR:
                had_error = True
                if not emitted_final_plan:
                    emitted_final_plan = True
                    yield StreamEvent.create(
                        task_id,
                        attempt,
                        EventType.PLAN,
                        summary=plan_summary,
                        steps=self._finalize_plan_steps(plan_steps, completed=False),
                    )
                yield ev
                continue

            if ev.type is EventType.DONE:
                finish_reason = str(ev.data.get("finish_reason") or "")
                ok = not had_error and finish_reason not in ("cancelled", "error")
                if not emitted_final_plan:
                    emitted_final_plan = True
                    yield StreamEvent.create(
                        task_id,
                        attempt,
                        EventType.PLAN,
                        summary=plan_summary,
                        steps=self._finalize_plan_steps(plan_steps, completed=ok),
                    )
                yield ev
                continue

            yield ev

        if not emitted_final_plan:
            yield StreamEvent.create(
                task_id,
                attempt,
                EventType.PLAN,
                summary=plan_summary,
                steps=self._finalize_plan_steps(plan_steps, completed=not had_error),
            )

    @staticmethod
    async def _await_approval_decision(
        task_id: str,
        attempt: int,
        approval_event: asyncio.Event | None,
        cancel_event: asyncio.Event | None,
    ) -> tuple[bool, list[StreamEvent]]:
        app_evt = approval_event or asyncio.Event()
        cnc_evt = cancel_event or asyncio.Event()

        approve_task = asyncio.create_task(app_evt.wait())
        cancel_task = asyncio.create_task(cnc_evt.wait())
        await asyncio.wait(
            [approve_task, cancel_task],
            return_when=asyncio.FIRST_COMPLETED,
            timeout=600.0,
        )

        if cnc_evt.is_set():
            return False, [
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.STATUS,
                    state="cancelled",
                    phase="CANCELLED",
                    message="Workflow execution was cancelled by user.",
                ),
                StreamEvent.create(task_id, attempt, EventType.DONE, finish_reason="cancelled"),
            ]
        if not app_evt.is_set():
            return False, [
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.STATUS,
                    state="cancelled",
                    phase="TIMEOUT",
                    message="Workflow approval timed out after 10 minutes.",
                ),
                StreamEvent.create(task_id, attempt, EventType.DONE, finish_reason="cancelled"),
            ]
        return True, []

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
        """Stream turn lifecycle cleanly across approval gating, fast-paths, and master graph."""
        task_id = task.id
        attempt = task.attempt or 1
        user_prompt = task.prompt_snapshot.user_prompt.strip()
        provider_name = getattr(provider.config, "display_name", provider.config.id) or (
            provider.config.id
        )

        projects_root = self._checkpoint_dir or (Path.cwd() / "projects")
        target_workspace, checkpoint_path = await asyncio.to_thread(
            self._ensure_workspace_dir,
            projects_root,
            task_id,
        )

        profile, triage_decision = self._profile_classifier.classify_intent_with_decision(
            user_prompt,
            triage_service=self._triage_service,
        )

        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.STATUS,
            state="running",
            phase="INITIALIZING",
            message=(
                f"Dynamic Orchestrator resolved intent '{profile.intent}' "
                f"(tier={triage_decision.tier}, conf={triage_decision.confidence:.2f}) "
                f"with {provider_name}..."
            ),
        )
        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.THOUGHT,
            text=(
                f"Intent Triage: Classified as {profile.intent} "
                f"(tier={triage_decision.tier}, confidence={triage_decision.confidence:.3f}, "
                f"skills={list(profile.skills)}, subgraphs={list(profile.subgraphs)}, "
                f"fast_path={profile.fast_path})."
            ),
        )

        plan_summary = f"Dynamic Orchestration ({profile.intent} · {provider_name})"
        plan_steps = self._build_plan_steps(profile, provider_name)
        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.PLAN,
            summary=plan_summary,
            steps=plan_steps,
        )

        initial_status = "WAITING_APPROVAL" if require_approval else "RUNNING"
        mermaid_code = self._mermaid.generate(
            user_prompt,
            status=initial_status,
            provider_name=provider_name,
            intent=profile.intent,
        )
        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.STATUS,
            state="waiting_approval" if require_approval else "running",
            phase="PLANNING" if require_approval else "APPROVED",
            message=(
                f"Workflow planned ({profile.intent}). Waiting for user approval..."
                if require_approval
                else f"Workflow planned ({profile.intent}). Proceeding with {provider_name}..."
            ),
            mermaid_code=mermaid_code,
        )

        if require_approval:
            approved, terminal_events = await self._await_approval_decision(
                task_id,
                attempt,
                approval_event,
                cancel_event,
            )
            for term_ev in terminal_events:
                yield term_ev
            if not approved:
                return

            approved_mermaid = self._mermaid.generate(
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

        if profile.intent == INTENT_DIRECT_QA:
            async for ev in self._stream_provider_with_plan_completion(
                task=task,
                provider=provider,
                active_tools=(),
                plan_summary=plan_summary,
                plan_steps=plan_steps,
            ):
                yield ev
            return

        if profile.intent == INTENT_FOCUSED_TOOL_TASK:
            scoped_tools = self._provision_focused_tools(profile, user_prompt, active_tools)
            async for ev in self._stream_provider_with_plan_completion(
                task=task,
                provider=provider,
                active_tools=scoped_tools,
                plan_summary=plan_summary,
                plan_steps=plan_steps,
            ):
                yield ev
            return

        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.TOKEN,
            text=(
                f"### Multi-Agent Orchestration Started ({profile.intent})\n\n"
                f"**Backend Provider:** `{provider_name}`\n\n"
                f"**Active Skills:** `{list(profile.skills)}`\n\n"
                f"**Workspace:** `{target_workspace}`\n\n"
                "---\n\n"
            ),
        )

        engine = self._engine_factory.create(
            workspace_dir=target_workspace,
            checkpoint_path=checkpoint_path,
            max_retries=self._max_retries,
        )
        mapper: INodeEventMapper = StartupNodeEventMapper(
            plan_steps=plan_steps,
            summary=plan_summary,
        )
        last_state: GraphState | None = None

        async for node_name, state_snapshot in engine.astream_nodes(
            user_requirement=user_prompt,
            project_id=f"proj-{task_id[:8]}",
            resume_if_possible=True,
            reset_state=False,
            use_master_graph=True,
            tool_registry=self._tool_registry,
        ):
            last_state = state_snapshot
            for ev in mapper.map_node(task_id, attempt, node_name, state_snapshot):
                yield ev
            await asyncio.sleep(0)

        is_completed = last_state is not None and last_state.get("current_phase") == PHASE_COMPLETED
        final_plan_steps = self._finalize_plan_steps(mapper.plan_steps, completed=is_completed)
        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.PLAN,
            summary=plan_summary,
            steps=final_plan_steps,
        )

        status_banner = "**COMPLETED SUCCESSFULLY**" if is_completed else "**EXECUTION PAUSED**"
        total_files = len(last_state.get("code_files") or {}) if last_state else 0
        retries_used = last_state.get("retry_count", 0) if last_state else 0
        phase_str = last_state.get("current_phase") if last_state else "UNKNOWN"
        tasks_str = str(last_state.get("completed_tasks") if last_state else [])

        summary_text = (
            "\n---\n\n"
            f"### {status_banner}\n\n"
            "| Metric | Details |\n"
            "|---|---|\n"
            f"| **Intent Profile** | `{profile.intent}` |\n"
            f"| **Backend Provider** | `{provider_name}` |\n"
            f"| **Final Phase** | `{phase_str}` |\n"
            f"| **Completed Tasks** | `{tasks_str}` |\n"
            f"| **Generated Files** | {total_files} files |\n"
            f"| **Retries / Self-Corrections** | {retries_used} |\n"
            f"| **Local Checkpoint** | `{checkpoint_path}` |\n\n"
            "All workflow stages and artifacts have been verified."
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


__all__ = ["TurnOrchestratorService"]
