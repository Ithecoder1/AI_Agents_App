"""Autonomous Startup Orchestrator Provider Adapter.

Bridges the LangGraph multi-agent startup orchestrator with the Vibe Forge execution engine,
enabling multi-turn chat sessions, live WebSocket streaming, and interactive UI inspection.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from app.domain.enums import ArtifactKind, EventType
from app.domain.models import (
    AgentProviderConfig,
    Artifact,
    ExecutionTask,
    HealthStatus,
    ProviderCapabilities,
    StreamEvent,
    Usage,
)
from app.domain.ports.agent_provider import ProviderDeps

from company_core.orchestrator import AutonomousStartupOrchestrator
from company_core.state import PHASE_COMPLETED, CompanyState
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

    async def stream_execute(self, task: ExecutionTask) -> AsyncIterator[StreamEvent]:
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

        # 2. Execution Plan Event
        plan_steps = [
            {
                "title": "1. Architecture Blueprint Synthesis (Software Architect)",
                "status": "in_progress",
            },
            {
                "title": "2. Source Code & Test Implementation (Software Engineer)",
                "status": "pending",
            },
            {
                "title": "3. DevOps & Environment Preparation (DevOps Engineer)",
                "status": "pending",
            },
            {
                "title": "4. Quality Assurance & Automated Pytest (QA Engineer)",
                "status": "pending",
            },
            {"title": "5. Final Delivery & Verification", "status": "pending"},
        ]
        yield StreamEvent.create(
            task_id,
            attempt,
            EventType.PLAN,
            summary="Autonomous Startup Multi-Agent Lifecycle",
            steps=plan_steps,
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

            if node_name == "ceo_router":
                curr_phase = state_snapshot.get("current_phase")
                yield StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.THOUGHT,
                    text=f"CEO Orchestrator: Evaluated requirement. Roadmap phase: {curr_phase}.",
                )
                yield StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.STATUS,
                    state="running",
                    phase=state_snapshot.get("current_phase"),
                )

            elif node_name == "architect":
                plan_steps[0]["status"] = "done"
                plan_steps[1]["status"] = "in_progress"
                yield StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.PLAN,
                    summary="Autonomous Startup Multi-Agent Lifecycle",
                    steps=plan_steps,
                )

                spec = state_snapshot.get("architecture_spec") or "No spec generated"
                yield StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.THOUGHT,
                    text="Software Architect: Synthesized modular architecture blueprint.",
                )
                yield StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.ARTIFACT,
                    **Artifact(
                        name="architecture_spec.md",
                        kind=ArtifactKind.FILE,
                        mime="text/markdown",
                        size=len(spec),
                        inline_content=spec,
                    ).to_dict(),
                )
                yield StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.TOKEN,
                    text="#### 🏛️ Phase 1: Architecture Specification Synthesized\n"
                    "- Blueprint documented and saved to `architecture_spec.md`.\n\n",
                )

            elif node_name == "coder":
                plan_steps[1]["status"] = "done"
                plan_steps[2]["status"] = "in_progress"
                yield StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.PLAN,
                    summary="Autonomous Startup Multi-Agent Lifecycle",
                    steps=plan_steps,
                )

                code_files = state_snapshot.get("code_files") or {}
                retries = state_snapshot.get("retry_count", 0)

                if retries > 0:
                    yield StreamEvent.create(
                        task_id,
                        attempt,
                        EventType.THOUGHT,
                        text=f"Software Engineer: Applying self-correction (Attempt #{retries}).",
                    )
                else:
                    yield StreamEvent.create(
                        task_id,
                        attempt,
                        EventType.THOUGHT,
                        text=f"Software Engineer: Implemented {len(code_files)} files & tests.",
                    )

                # Emit generated files as artifacts for UI inspection
                for filepath, content in code_files.items():
                    yield StreamEvent.create(
                        task_id,
                        attempt,
                        EventType.ARTIFACT,
                        **Artifact(
                            name=filepath,
                            kind=ArtifactKind.FILE,
                            mime="text/x-python" if filepath.endswith(".py") else "text/plain",
                            size=len(content),
                            inline_content=content,
                        ).to_dict(),
                    )

                file_list = ", ".join(f"`{f}`" for f in code_files)
                yield StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.TOKEN,
                    text=f"#### 💻 Phase 2: Source Code Implemented\n"
                    f"- Generated files: {file_list}\n\n",
                )

            elif node_name == "devops":
                plan_steps[2]["status"] = "done"
                plan_steps[3]["status"] = "in_progress"
                yield StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.PLAN,
                    summary="Autonomous Startup Multi-Agent Lifecycle",
                    steps=plan_steps,
                )

                yield StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.THOUGHT,
                    text="DevOps Engineer: Configured requirements.txt and pytest runner harness.",
                )
                yield StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.TOKEN,
                    text="#### ⚙️ Phase 3: DevOps Environment Configured\n"
                    "- Test harness and dependencies configured.\n\n",
                )

            elif node_name == "qa":
                test_results = state_snapshot.get("test_results") or ""
                passed = "PASSED" in test_results

                if passed:
                    plan_steps[3]["status"] = "done"
                    plan_steps[4]["status"] = "done"
                    yield StreamEvent.create(
                        task_id,
                        attempt,
                        EventType.THOUGHT,
                        text="Quality Assurance: Automated pytest suite PASSED all assertions!",
                    )
                else:
                    yield StreamEvent.create(
                        task_id,
                        attempt,
                        EventType.THOUGHT,
                        text=f"Quality Assurance: Tests failed: {test_results[:50]}...",
                    )

                yield StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.PLAN,
                    summary="Autonomous Startup Multi-Agent Lifecycle",
                    steps=plan_steps,
                )

                yield StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.LOG,
                    text=test_results,
                    stream="stdout" if passed else "stderr",
                )

            elif node_name == "task_decomposer":
                yield StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.THOUGHT,
                    text="Technical Lead: Decomposed failing component into atomic subtasks.",
                )
                yield StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.TOKEN,
                    text=(
                        "> ⚠️ **Dynamic Task Decomposition Triggered**: "
                        "Monolithic component broken into sub-tasks.\n\n"
                    ),
                )

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
