"""Node event mapping for Autonomous Startup Orchestrator."""

from __future__ import annotations

from app.domain.enums import ArtifactKind, EventType
from app.domain.models import Artifact, StreamEvent
from company_core.state import CompanyState


class StartupNodeEventMapper:
    """Maps LangGraph node transitions to user-facing domain StreamEvents."""

    def __init__(self) -> None:
        self.plan_steps: list[dict[str, str]] = [
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

    def map_node(
        self,
        task_id: str,
        attempt: int,
        node_name: str,
        state_snapshot: CompanyState,
    ) -> list[StreamEvent]:
        """Convert a single node execution snapshot into corresponding domain events."""
        events: list[StreamEvent] = []

        if node_name == "ceo_router":
            curr_phase = state_snapshot.get("current_phase")
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.THOUGHT,
                    text=f"CEO Orchestrator: Evaluated requirement. Roadmap phase: {curr_phase}.",
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

        elif node_name == "architect":
            self.plan_steps[0]["status"] = "done"
            self.plan_steps[1]["status"] = "in_progress"
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.PLAN,
                    summary="Autonomous Startup Multi-Agent Lifecycle",
                    steps=self.plan_steps,
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
                    text="#### 🏛️ Phase 1: Architecture Specification Synthesized\n"
                    "- Blueprint documented and saved to `architecture_spec.md`.\n\n",
                )
            )

        elif node_name == "coder":
            self.plan_steps[1]["status"] = "done"
            self.plan_steps[2]["status"] = "in_progress"
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.PLAN,
                    summary="Autonomous Startup Multi-Agent Lifecycle",
                    steps=self.plan_steps,
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
                        f"#### 💻 Phase 2: Source Code Implemented\n"
                        f"- Generated files: {file_list}\n\n"
                    ),
                )
            )

        elif node_name == "devops":
            self.plan_steps[2]["status"] = "done"
            self.plan_steps[3]["status"] = "in_progress"
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.PLAN,
                    summary="Autonomous Startup Multi-Agent Lifecycle",
                    steps=self.plan_steps,
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
                    text="#### ⚙️ Phase 3: DevOps Environment Configured\n"
                    "- Test harness and dependencies configured.\n\n",
                )
            )

        elif node_name == "qa":
            test_results = state_snapshot.get("test_results") or ""
            passed = "PASSED" in test_results
            if passed:
                self.plan_steps[3]["status"] = "done"
                self.plan_steps[4]["status"] = "done"
                qa_text = "Quality Assurance: Automated pytest suite PASSED all assertions!"
            else:
                qa_text = f"Quality Assurance: Tests failed: {test_results[:50]}..."

            events.append(StreamEvent.create(task_id, attempt, EventType.THOUGHT, text=qa_text))
            events.append(
                StreamEvent.create(
                    task_id,
                    attempt,
                    EventType.PLAN,
                    summary="Autonomous Startup Multi-Agent Lifecycle",
                    steps=self.plan_steps,
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
                        "> ⚠️ **Dynamic Task Decomposition Triggered**: "
                        "Monolithic component broken into sub-tasks.\n\n"
                    ),
                )
            )

        return events
