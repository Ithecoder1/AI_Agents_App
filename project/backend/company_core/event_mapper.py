"""Node event mapping for Autonomous Startup & Dynamic LangGraph Orchestrator."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.domain.enums import ArtifactKind, EventType
from app.domain.models import Artifact, StreamEvent

from company_core.state import CompanyState


class StartupNodeEventMapper:
    """Maps LangGraph node transitions to user-facing domain StreamEvents."""

    def __init__(
        self,
        plan_steps: Sequence[dict[str, Any]] | None = None,
        summary: str = "Autonomous Startup Multi-Agent Lifecycle",
    ) -> None:
        self.summary = summary
        if plan_steps:
            self.plan_steps: list[dict[str, str]] = [
                {
                    "title": str(step.get("title", f"Step {idx + 1}")),
                    "status": str(step.get("status", "pending")),
                }
                for idx, step in enumerate(plan_steps)
            ]
        else:
            self.plan_steps = [
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

    def _advance_plan_step(self, done_index: int) -> None:
        """Mark steps 0..done_index as done and set the next step in bounds to in_progress."""
        total = len(self.plan_steps)
        if total == 0:
            return
        clamped_done = min(done_index, total - 1)
        for idx in range(clamped_done + 1):
            self.plan_steps[idx]["status"] = "done"
        next_idx = clamped_done + 1
        if next_idx < total and self.plan_steps[next_idx]["status"] == "pending":
            self.plan_steps[next_idx]["status"] = "in_progress"

    def _mark_all_steps_done(self) -> None:
        """Mark every step in the active plan as done."""
        for step in self.plan_steps:
            step["status"] = "done"

    def map_node(
        self,
        task_id: str,
        attempt: int,
        node_name: str,
        state_snapshot: CompanyState,
    ) -> list[StreamEvent]:
        """Convert a single node execution snapshot into corresponding domain events."""
        events: list[StreamEvent] = []

        if node_name in ("ceo_router", "triage_node"):
            curr_phase = state_snapshot.get("current_phase") or state_snapshot.get("status")
            intent = state_snapshot.get("intent")
            active_skills = state_snapshot.get("active_skills") or []
            intent_suffix = f" Intent: {intent}." if intent else ""
            skills_suffix = f" Active skills: {', '.join(active_skills)}." if active_skills else ""
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.THOUGHT,
                    text=(
                        f"CEO Orchestrator: Evaluated requirement. "
                        f"Roadmap phase/status: {curr_phase}.{intent_suffix}{skills_suffix}"
                    ),
                )
            )
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.STATUS,
                    state="running",
                    phase=curr_phase,
                )
            )

        elif node_name == "planner_node":
            plan = state_snapshot.get("plan") or []
            if plan:
                self.plan_steps = [
                    {
                        "title": (
                            f"{s.get('step', i + 1)}. "
                            f"{s.get('description', s.get('action', 'Step'))}"
                        ),
                        "status": "in_progress" if i == 0 else "pending",
                    }
                    for i, s in enumerate(plan)
                ]
                events.append(
                    StreamEvent.create(
                        task_id,
                        attempt,
                        EventType.PLAN,
                        summary=self.summary,
                        steps=[dict(s) for s in self.plan_steps],
                    )
                )
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.THOUGHT,
                    text=f"Planner: Synthesized deterministic plan with {len(plan)} steps.",
                )
            )

        elif node_name == "subgraph_router_node":
            task_type = state_snapshot.get("task_type", "FULL_PIPELINE")
            intent = state_snapshot.get("intent") or task_type
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.THOUGHT,
                    text=(
                        f"Subgraph Router: Delegating control for intent '{intent}' ({task_type})."
                    ),
                )
            )

        elif node_name in ("architect", "architect_subgraph", "schema_validator_node"):
            self._advance_plan_step(0)
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.PLAN,
                    summary=self.summary,
                    steps=[dict(s) for s in self.plan_steps],
                )
            )
            spec = state_snapshot.get("architecture_spec") or "No spec generated"
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.THOUGHT,
                    text="Software Architect: Synthesized modular architecture blueprint.",
                )
            )
            events.append(
                StreamEvent.create(
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
            )
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.TOKEN,
                    text="#### Architecture Phase: Blueprint Synthesized\n"
                    "- Blueprint documented and saved to `architecture_spec.md`.\n\n",
                )
            )

        elif node_name in ("coder", "coder_node", "coder_qa_subgraph"):
            self._advance_plan_step(1 if len(self.plan_steps) >= 3 else 0)
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.PLAN,
                    summary=self.summary,
                    steps=[dict(s) for s in self.plan_steps],
                )
            )
            code_files = state_snapshot.get("code_files") or {}
            retries = state_snapshot.get("retry_count", 0)

            thought_text = (
                f"Software Engineer: Applying self-correction (Attempt #{retries})."
                if retries > 0
                else f"Software Engineer: Implemented {len(code_files)} files & tests."
            )
            events.append(
                StreamEvent.create(task_id, attempt, EventType.THOUGHT, text=thought_text)
            )

            for filepath, content in code_files.items():
                events.append(
                    StreamEvent.create(
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
                )

            file_list = ", ".join(f"`{f}`" for f in code_files)
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.TOKEN,
                    text=(
                        f"#### Implementation Phase: Source Code Implemented\n"
                        f"- Generated files: {file_list}\n\n"
                    ),
                )
            )

        elif node_name in ("devops", "devops_test_node"):
            if len(self.plan_steps) >= 4:
                self._advance_plan_step(2)
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.PLAN,
                    summary=self.summary,
                    steps=[dict(s) for s in self.plan_steps],
                )
            )
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.THOUGHT,
                    text="DevOps Engineer: Configured requirements.txt and pytest runner harness.",
                )
            )
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.TOKEN,
                    text="#### DevOps Phase: Environment Configured\n"
                    "- Test harness and dependencies configured.\n\n",
                )
            )

        elif node_name in ("qa", "evaluator_node"):
            test_results = state_snapshot.get("test_results") or ""
            passed = "PASSED" in test_results
            if passed:
                self._mark_all_steps_done()
                qa_text = "Quality Assurance: Automated pytest suite PASSED all assertions!"
            else:
                qa_text = f"Quality Assurance: Tests failed: {test_results[:50]}..."

            events.append(StreamEvent.create(task_id, attempt, EventType.THOUGHT, text=qa_text))
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.PLAN,
                    summary=self.summary,
                    steps=[dict(s) for s in self.plan_steps],
                )
            )
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.LOG,
                    text=test_results,
                    stream="stdout" if passed else "stderr",
                )
            )

        elif node_name == "executor_node":
            self._mark_all_steps_done()
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.PLAN,
                    summary=self.summary,
                    steps=[dict(s) for s in self.plan_steps],
                )
            )
            messages = state_snapshot.get("messages") or []
            last_text = messages[-1].get("content", "") if messages else ""
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.THOUGHT,
                    text="Executor: Delivered direct response.",
                )
            )
            if last_text:
                events.append(
                    StreamEvent.create(
                        task_id,
                        attempt,
                        EventType.TOKEN,
                        text=last_text,
                    )
                )

        elif node_name == "task_decomposer":
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.THOUGHT,
                    text="Technical Lead: Decomposed failing component into atomic subtasks.",
                )
            )
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.TOKEN,
                    text=(
                        "> **Dynamic Task Decomposition Triggered**: "
                        "Monolithic component broken into sub-tasks.\n\n"
                    ),
                )
            )

        return events


__all__ = ["StartupNodeEventMapper"]
