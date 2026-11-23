"""Orchestration domain ports and value objects (pure stdlib + app.domain)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Protocol, runtime_checkable

from app.domain.graph_state import GraphState
from app.domain.models import ExecutionTask, StreamEvent
from app.domain.ports.agent_provider import IAgentProvider
from app.domain.tools import ToolDescriptor


@dataclass(frozen=True, slots=True)
class TestRunResult:
    """Structured outcome of an isolated workspace test suite run."""

    __test__: ClassVar[bool] = False

    success: bool
    combined_output: str
    clean_traceback: str | None = None

    @classmethod
    def passed(cls, combined_output: str = "All tests passed.") -> TestRunResult:
        """Construct a passing test run result."""
        return cls(success=True, combined_output=combined_output, clean_traceback=None)

    @classmethod
    def failed(cls, combined_output: str, clean_traceback: str | None = None) -> TestRunResult:
        """Construct a failing test run result with optional cleaned traceback."""
        return cls(
            success=False,
            combined_output=combined_output,
            clean_traceback=clean_traceback if clean_traceback is not None else combined_output,
        )

    @classmethod
    def timed_out(cls, timeout_s: float) -> TestRunResult:
        """Construct a timed-out test run result."""
        msg = f"Test execution timed out after {timeout_s}s"
        return cls(success=False, combined_output=msg, clean_traceback=msg)

    @property
    def summary_line(self) -> str:
        """Return the first non-empty output line or a canonical pass/fail marker."""
        for line in self.combined_output.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return "PASSED" if self.success else "FAILED"

    def to_dict(self) -> dict[str, Any]:
        """Serialize test run result into a JSON-compatible dictionary."""
        return {
            "success": self.success,
            "combined_output": self.combined_output,
            "clean_traceback": self.clean_traceback,
        }


@runtime_checkable
class ITurnOrchestrator(Protocol):
    """Port for streaming intent-driven orchestration turns over an IAgentProvider."""

    def stream_turn(
        self,
        task: ExecutionTask,
        provider: IAgentProvider,
        *,
        require_approval: bool = False,
        approval_event: asyncio.Event | None = None,
        cancel_event: asyncio.Event | None = None,
        active_tools: list[ToolDescriptor] | tuple[ToolDescriptor, ...] | None = None,
    ) -> AsyncIterator[StreamEvent]: ...


@runtime_checkable
class IOrchestratorEngine(Protocol):
    """Port for synchronous and streaming execution of the multi-agent master graph."""

    def run_master_workflow(
        self,
        user_requirement: str,
        project_id: str = "vibe-app",
        provider: IAgentProvider | None = None,
        tool_registry: Any | None = None,
        resume_if_possible: bool = True,
        reset_state: bool = False,
    ) -> GraphState: ...

    def astream_nodes(
        self,
        user_requirement: str,
        project_id: str = "vibe-app",
        resume_if_possible: bool = True,
        reset_state: bool = False,
        use_master_graph: bool = True,
        tool_registry: Any | None = None,
    ) -> AsyncIterator[tuple[str, GraphState]]: ...


@runtime_checkable
class IOrchestratorCheckpointer(Protocol):
    """Port for persisting, loading, and hydrating resumable orchestration state."""

    def save(self, state: GraphState, filepath: Path | str | None = None) -> Path: ...

    def load(self, filepath: Path | str | None = None) -> GraphState | None: ...

    def can_resume(self, state: GraphState | None) -> bool: ...

    def hydrate(self, state: GraphState) -> GraphState: ...


@runtime_checkable
class ICodeWorkspaceRunner(Protocol):
    """Port isolating local workspace filesystem mutations and test runner execution."""

    def resolve_project_dir(self, project_id: str) -> Path: ...

    def write_files(self, target_dir: Path, files: Mapping[str, str]) -> list[str]: ...

    def read_files(
        self,
        target_dir: Path,
        extensions: tuple[str, ...] = (".py", ".json", ".yaml", ".yml", ".md", ".txt"),
    ) -> dict[str, str]: ...

    def summarize_code(
        self,
        files: Mapping[str, str],
        max_lines_per_file: int = 50,
        strip_comments: bool = True,
    ) -> str: ...

    def run_tests(self, target_dir: Path, timeout_s: float = 30.0) -> TestRunResult: ...


@runtime_checkable
class IProfileClassifier(Protocol):
    """Port for classifying prompts into execution profiles and looking up profile configs."""

    def classify_intent_with_decision(
        self,
        requirement: str,
        *,
        is_lightweight: bool = False,
        triage_service: Any | None = None,
    ) -> tuple[Any, Any]: ...

    def get_profile(self, intent_or_task_type: str) -> Any: ...


__all__ = [
    "ICodeWorkspaceRunner",
    "IOrchestratorCheckpointer",
    "IOrchestratorEngine",
    "IProfileClassifier",
    "ITurnOrchestrator",
    "TestRunResult",
]
