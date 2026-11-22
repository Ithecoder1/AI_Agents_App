"""Flow-level characterization tests for the LangGraph Master Orchestrator Graph.

Locks in end-to-end state transitions, triage-driven branching, JIT tool provisioning,
provider delegation, checkpoint persistence/resumption, and failure/retry recovery loops
while keeping all external LLM, HuggingFace/embedding, subprocess, and disk boundaries isolated.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pytest
from app.application.tools.registry import DynamicToolRegistry
from app.application.triage import (
    DEFAULT_RESOURCE_ALLOCATION,
    LOCKED_MICRO_LLM_SYSTEM_PROMPT,
    VALID_TRIAGE_INTENTS,
    CallableMicroLLMClient,
    CascadingTriageService,
    Tier1HeuristicFilter,
    Tier3MicroLLMClassifier,
    TriageDecision,
    TriageIntent,
)
from app.domain.enums import ArtifactKind, EventType
from app.domain.errors import ProviderError
from app.domain.graph_state import (
    INTENT_ARCHITECTURE_DESIGN,
    INTENT_CODE_TEST_FIX,
    INTENT_COMPLEX_PIPELINE,
    INTENT_DIRECT_QA,
    INTENT_FOCUSED_TOOL_TASK,
    STATUS_COMPLETED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_WAITING_APPROVAL,
    create_initial_graph_state,
)
from app.domain.models import (
    AgentProviderConfig,
    Artifact,
    ExecutionParams,
    ExecutionTask,
    HealthStatus,
    Prompt,
    ProviderCapabilities,
    StreamEvent,
    Usage,
)
from app.domain.tools import ToolDescriptor, ToolRetrievalPolicy
from company_core.config import ConfigManager
from company_core.orchestrator import AutonomousStartupOrchestrator
from company_core.orchestrator_graph import compile_orchestrator_workflow
from company_core.persistence import (
    JsonStateCheckpointer,
    can_resume,
    load_company_state,
    save_company_state,
)
from company_core.state import (
    PHASE_ARCHITECT,
    PHASE_CODER,
    PHASE_COMPLETED,
    PHASE_FAILED,
    CompanyState,
)
from company_core.turn_orchestrator import GraphTurnOrchestrator
from providers.kit import BaseAgentRunner

from tests.factories import REPO_CONFIG, make_config, make_deps

# --------------------------------------------------------------------------- #
# Deterministic Boundary Doubles
# --------------------------------------------------------------------------- #
_DEFAULT_SEMANTIC_KEYWORDS: tuple[tuple[str, TriageIntent], ...] = (
    ("build a complete", "ARCHITECT_PIPELINE"),
    ("payment service from scratch", "ARCHITECT_PIPELINE"),
    ("calculator service from scratch", "ARCHITECT_PIPELINE"),
    ("design a modular system architecture", "ARCHITECT_PIPELINE"),
    ("fix failing unit tests", "CODE_TEST_FIX"),
    ("analyze and inspect", "CODE_TEST_FIX"),
)


class _StubSemanticRouter:
    """Deterministic Tier 2 router avoiding HuggingFace/torch downloads."""

    def __init__(
        self,
        route_rules: Sequence[tuple[str, TriageIntent]] = _DEFAULT_SEMANTIC_KEYWORDS,
    ) -> None:
        self._route_rules = tuple((kw.lower(), intent) for kw, intent in route_rules)
        self._is_warmed = False

    @property
    def is_warmed(self) -> bool:
        return self._is_warmed

    def warmup(self) -> None:
        self._is_warmed = True

    def score_routes(self, user_prompt: str) -> dict[TriageIntent, float]:
        matched = self._match(user_prompt)
        return {intent: (0.95 if intent == matched else 0.1) for intent in VALID_TRIAGE_INTENTS}

    def evaluate(self, user_prompt: str) -> TriageDecision | None:
        intent = self._match(user_prompt)
        if intent is None:
            return None
        alloc = DEFAULT_RESOURCE_ALLOCATION[intent]
        return TriageDecision(
            intent=intent,
            confidence=0.95,
            requires_workspace_manifest=alloc.requires_workspace_manifest,
            target_model_tier=alloc.target_model_tier,
            token_budget=alloc.token_budget,
            tier=2,
            latency_ms=0.25,
            matched_route=intent,
            reason=f"Stub Tier 2 matched {intent}",
        )

    def _match(self, user_prompt: str) -> TriageIntent | None:
        lower = user_prompt.lower()
        for keyword, intent in self._route_rules:
            if keyword in lower:
                return intent
        return None


def _make_triage_service(
    *,
    semantic_rules: Sequence[tuple[str, TriageIntent]] = _DEFAULT_SEMANTIC_KEYWORDS,
    tier3_fn: Callable[[str, str], str] | None = None,
) -> CascadingTriageService:
    """Build a zero-network CascadingTriageService with stubbed Tier 2 and Tier 3."""
    default_tier3 = json.dumps(
        {
            "intent": "ARCHITECT_PIPELINE",
            "confidence": 0.91,
            "requires_workspace_manifest": True,
            "target_model_tier": "reasoning",
            "token_budget": 4096,
        }
    )
    client = CallableMicroLLMClient(tier3_fn or (lambda _sys, _usr: default_tier3))
    return CascadingTriageService(
        tier1_filter=Tier1HeuristicFilter(),
        semantic_router=cast(Any, _StubSemanticRouter(semantic_rules)),
        micro_llm=Tier3MicroLLMClassifier(client=client),
        auto_warmup=True,
    )


class _MockAgentProvider(BaseAgentRunner):
    """Deterministic IAgentProvider recording calls and yielding scripted StreamEvents."""

    default_capabilities = ProviderCapabilities(
        streaming=True,
        reports_usage=True,
        models=("mock-v1",),
    )

    def __init__(
        self,
        *,
        provider_id: str = "mock-llm",
        chunks: Sequence[str] = ("Mock ", "LLM response"),
        artifact: Artifact | None = None,
        usage: Usage | None = None,
        error: Exception | None = None,
    ) -> None:
        cfg: AgentProviderConfig = make_config(provider_id, "mock")
        super().__init__(cfg, make_deps())
        self._chunks = tuple(chunks)
        self._artifact = artifact
        self._usage = usage or Usage(prompt_tokens=12, completion_tokens=8, reasoning_tokens=2)
        self._error = error
        self.calls: list[tuple[ExecutionTask, tuple[ToolDescriptor, ...]]] = []

    async def health_check(self) -> HealthStatus:
        return HealthStatus.up()

    async def stream_execute(
        self,
        task: ExecutionTask,
        *,
        active_tools: list[ToolDescriptor] | tuple[ToolDescriptor, ...] | None = None,
        retrieval_policy: ToolRetrievalPolicy | None = None,
    ) -> AsyncIterator[StreamEvent]:
        _ = retrieval_policy
        recorded_tools = tuple(active_tools) if active_tools is not None else ()
        self.calls.append((task, recorded_tools))

        if self._error is not None:
            raise self._error

        yield self.event(task, EventType.STATUS, state="running", phase="EXECUTING")
        yield self.event(task, EventType.THOUGHT, text="Mock reasoning trace")
        for chunk in self._chunks:
            yield self.event(task, EventType.TOKEN, text=chunk)
        if self._artifact is not None:
            yield self.event(task, EventType.ARTIFACT, **self._artifact.to_dict())
        yield self.event(task, EventType.USAGE, **self._usage.to_dict())
        yield self.event(task, EventType.DONE, finish_reason="stop")


def _make_tool_registry() -> DynamicToolRegistry:
    """Create a deterministic in-memory tool registry for JIT discovery."""
    return DynamicToolRegistry(
        [
            ToolDescriptor("view_file", "filesystem", "View file contents"),
            ToolDescriptor("grep_search", "search", "Search text patterns in files"),
            ToolDescriptor("list_dir", "filesystem", "List directory contents"),
            ToolDescriptor("write_to_file", "filesystem", "Write content to a file"),
            ToolDescriptor("replace_file_content", "filesystem", "Edit a region in a file"),
            ToolDescriptor("run_command", "terminal", "Run a shell command or pytest"),
        ]
    )


def _make_config() -> ConfigManager:
    """Load an isolated ConfigManager directly from repository YAML configs."""
    return ConfigManager(
        agents_path=REPO_CONFIG / "agents.yaml",
        tasks_path=REPO_CONFIG / "tasks.yaml",
        profiles_path=REPO_CONFIG / "orchestrator_profiles.yaml",
    )


def _stream_master(
    compiled: Any,
    initial_state: Mapping[str, Any],
    thread_id: str,
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    """Run compiled workflow via stream_mode='updates' and collect per-node snapshots."""
    transitions: list[tuple[str, dict[str, Any]]] = []
    config = {"configurable": {"thread_id": thread_id}}
    for chunk in compiled.stream(initial_state, config=config, stream_mode="updates"):
        assert isinstance(chunk, dict)
        for node_name, state_update in chunk.items():
            transitions.append((str(node_name), dict(state_update)))
    final_state = dict(compiled.get_state(config).values)
    return transitions, final_state


# --------------------------------------------------------------------------- #
# Characterization Tests: End-to-End Master Orchestrator Graph Flows
# --------------------------------------------------------------------------- #
def test_complex_pipeline_happy_path_transitions_artifacts_and_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "workspace_full"
    checkpoint_file = tmp_path / "logs" / "company_state.json"
    pytest_calls: list[Path] = []

    def _mock_run_pytest(target_dir: Path, **_kwargs: Any) -> tuple[bool, str, str | None]:
        pytest_calls.append(target_dir)
        return True, "2 passed in 0.01s", None

    monkeypatch.setattr("company_core.agents.quality_assurance.run_pytest_suite", _mock_run_pytest)

    compiled = compile_orchestrator_workflow(
        checkpointer=JsonStateCheckpointer(log_path=checkpoint_file),
        project_root_resolver=lambda _: project_dir,
        tool_registry=_make_tool_registry(),
        config=_make_config(),
        triage_service=_make_triage_service(),
    )

    initial = create_initial_graph_state(
        "Build a complete deterministic arithmetic service with pytest suite",
        project_id="char-full",
        auto_approve=True,
    )
    assert initial["status"] == STATUS_PENDING
    assert initial["current_node"] == "triage_node"
    assert initial["completed_tasks"] == []

    transitions, final = _stream_master(compiled, initial, thread_id="char-full")

    node_sequence = [name for name, _ in transitions]
    assert node_sequence == [
        "triage_node",
        "planner_node",
        "subgraph_router_node",
        "architect_subgraph",
        "coder_qa_subgraph",
    ]
    assert [snap["status"] for _, snap in transitions] == [
        STATUS_RUNNING,
        STATUS_RUNNING,
        STATUS_RUNNING,
        STATUS_RUNNING,
        STATUS_COMPLETED,
    ]

    # Triage snapshot checks
    triage_snap = transitions[0][1]
    assert triage_snap["intent"] == INTENT_COMPLEX_PIPELINE
    assert triage_snap["task_type"] == "FULL_PIPELINE"
    assert triage_snap["active_skills"] == [
        "architecture_planning",
        "code_analysis",
        "code_verification",
    ]
    assert 1 <= len(triage_snap["active_tools"]) <= 5
    assert triage_snap["subgraph_results"]["triage"]["tier"] == 2

    # Planner & architect intermediate checks
    assert len(transitions[1][1]["plan"]) == 3
    arch_snap = transitions[3][1]
    assert arch_snap["architecture_spec"] is not None
    assert arch_snap["completed_tasks"] == ["architecture"]
    assert arch_snap["subgraph_results"]["architecture_spec_valid"] is True

    # Final state assertions
    assert final["status"] == STATUS_COMPLETED
    assert final["current_phase"] == PHASE_COMPLETED
    assert final["current_node"] == "evaluator_node"
    assert final["completed_tasks"] == ["architecture", "code", "devops", "qa"]
    assert final["failed_tasks"] == []
    assert final["retry_count"] == 0
    assert final["error_message"] is None
    assert final["test_results"] == "PASSED: All tests succeeded."

    expected_artifacts = {
        "app/__init__.py",
        "app/engine.py",
        "tests/__init__.py",
        "tests/test_engine.py",
        "requirements.txt",
        "README.md",
    }
    assert set(final["code_files"].keys()) == expected_artifacts
    for rel_path in expected_artifacts:
        assert (project_dir / rel_path).is_file()

    assert pytest_calls == [project_dir]

    # Checkpoint persistence check
    assert checkpoint_file.is_file()
    raw_checkpoint = json.loads(checkpoint_file.read_text(encoding="utf-8"))
    assert raw_checkpoint["project_id"] == "char-full"
    assert raw_checkpoint["current_phase"] == PHASE_COMPLETED
    loaded = load_company_state(checkpoint_file)
    assert loaded is not None
    assert can_resume(loaded) is False


def test_direct_qa_fast_path_delegates_to_mocked_provider(tmp_path: Path) -> None:
    checkpoint_file = tmp_path / "qa_state.json"
    mock_provider = _MockAgentProvider(chunks=("Hello ", "from mock LLM!"))

    compiled = compile_orchestrator_workflow(
        checkpointer=JsonStateCheckpointer(log_path=checkpoint_file),
        project_root_resolver=lambda _: tmp_path / "unused_ws",
        tool_registry=_make_tool_registry(),
        provider=mock_provider,
        config=_make_config(),
        triage_service=_make_triage_service(),
    )

    initial = create_initial_graph_state("hi", project_id="char-qa", auto_approve=True)
    assert initial["status"] == STATUS_PENDING

    transitions, final = _stream_master(compiled, initial, thread_id="char-qa")

    assert [name for name, _ in transitions] == ["triage_node", "executor_node"]
    assert [snap["status"] for _, snap in transitions] == [STATUS_RUNNING, STATUS_COMPLETED]

    assert final["status"] == STATUS_COMPLETED
    assert final["current_phase"] == PHASE_COMPLETED
    assert final["current_node"] == "executor_node"
    assert final["intent"] == INTENT_DIRECT_QA
    assert final["task_type"] == "DIRECT_QA"
    assert final["active_tools"] == []
    assert final["active_skills"] == ["direct_qa"]
    assert final["completed_tasks"] == []
    assert final["architecture_spec"] is None
    assert final["code_files"] == {}
    assert final["messages"][-1] == {"role": "assistant", "content": "Hello from mock LLM!"}
    assert final["subgraph_results"]["executor"]["output"] == "Hello from mock LLM!"

    assert len(mock_provider.calls) == 1
    dispatched_task, dispatched_tools = mock_provider.calls[0]
    assert dispatched_task.prompt_snapshot.user_prompt == "hi"
    assert dispatched_tools == ()
    assert checkpoint_file.is_file()


def test_focused_tool_task_provisions_jit_tools_and_bypasses_subgraphs(tmp_path: Path) -> None:
    checkpoint_file = tmp_path / "focused_state.json"
    mock_provider = _MockAgentProvider(chunks=("Inspected ", "3 files."))

    compiled = compile_orchestrator_workflow(
        checkpointer=JsonStateCheckpointer(log_path=checkpoint_file),
        project_root_resolver=lambda _: tmp_path / "focused_ws",
        tool_registry=_make_tool_registry(),
        provider=mock_provider,
        config=_make_config(),
        triage_service=_make_triage_service(),
    )

    initial = create_initial_graph_state(
        "Analyze and inspect workspace files",
        project_id="char-focused",
        auto_approve=True,
    )
    transitions, final = _stream_master(compiled, initial, thread_id="char-focused")

    assert [name for name, _ in transitions] == ["triage_node", "planner_node", "executor_node"]
    assert [snap["status"] for _, snap in transitions] == [
        STATUS_RUNNING,
        STATUS_RUNNING,
        STATUS_COMPLETED,
    ]
    assert final["intent"] == INTENT_FOCUSED_TOOL_TASK
    assert final["task_type"] == "FOCUSED_TOOL_TASK"
    assert final["active_skills"] == ["code_analysis"]
    assert 1 <= len(final["active_tools"]) <= 4
    assert final["completed_tasks"] == []
    assert final["messages"][-1] == {"role": "assistant", "content": "Inspected 3 files."}
    assert final["subgraph_results"]["executor"]["output"] == "Inspected 3 files."
    assert len(mock_provider.calls) == 1


def test_specialized_single_subgraph_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest_calls: list[Path] = []

    def _mock_run_pytest(target_dir: Path, **_kwargs: Any) -> tuple[bool, str, str | None]:
        pytest_calls.append(target_dir)
        return True, "2 passed in 0.01s", None

    monkeypatch.setattr("company_core.agents.quality_assurance.run_pytest_suite", _mock_run_pytest)

    # Sub-case A: ARCHITECTURE_DESIGN runs only architect_subgraph
    arch_dir = tmp_path / "arch_ws"
    compiled_arch = compile_orchestrator_workflow(
        checkpointer=JsonStateCheckpointer(log_path=tmp_path / "arch_state.json"),
        project_root_resolver=lambda _: arch_dir,
        tool_registry=_make_tool_registry(),
        config=_make_config(),
        triage_service=_make_triage_service(),
    )
    arch_init = create_initial_graph_state(
        "Design a modular system architecture blueprint",
        project_id="char-arch",
        auto_approve=True,
    )
    arch_transitions, arch_final = _stream_master(compiled_arch, arch_init, thread_id="char-arch")

    assert [name for name, _ in arch_transitions] == [
        "triage_node",
        "planner_node",
        "subgraph_router_node",
        "architect_subgraph",
    ]
    assert [snap["status"] for _, snap in arch_transitions] == [
        STATUS_RUNNING,
        STATUS_RUNNING,
        STATUS_RUNNING,
        STATUS_COMPLETED,
    ]
    assert arch_final["intent"] == INTENT_ARCHITECTURE_DESIGN
    assert arch_final["current_phase"] == PHASE_COMPLETED
    assert arch_final["completed_tasks"] == ["architecture"]
    assert arch_final["architecture_spec"] is not None
    assert arch_final["code_files"] == {}
    assert pytest_calls == []

    # Sub-case B: CODE_TEST_FIX runs only coder_qa_subgraph
    fix_dir = tmp_path / "fix_ws"
    compiled_fix = compile_orchestrator_workflow(
        checkpointer=JsonStateCheckpointer(log_path=tmp_path / "fix_state.json"),
        project_root_resolver=lambda _: fix_dir,
        tool_registry=_make_tool_registry(),
        config=_make_config(),
        triage_service=_make_triage_service(),
    )
    fix_init = create_initial_graph_state(
        "Fix failing unit tests in auth module",
        project_id="char-fix",
        auto_approve=True,
    )
    fix_transitions, fix_final = _stream_master(compiled_fix, fix_init, thread_id="char-fix")

    assert [name for name, _ in fix_transitions] == [
        "triage_node",
        "planner_node",
        "subgraph_router_node",
        "coder_qa_subgraph",
    ]
    assert [snap["status"] for _, snap in fix_transitions] == [
        STATUS_RUNNING,
        STATUS_RUNNING,
        STATUS_RUNNING,
        STATUS_COMPLETED,
    ]
    assert fix_final["intent"] == INTENT_CODE_TEST_FIX
    assert fix_final["current_phase"] == PHASE_COMPLETED
    assert fix_final["completed_tasks"] == ["code", "devops", "qa"]
    assert "architecture" not in fix_final["completed_tasks"]
    assert fix_final["architecture_spec"] is None
    assert (fix_dir / "app" / "engine.py").is_file()
    assert pytest_calls == [fix_dir]


def test_approval_gate_halts_at_planner_with_waiting_approval(tmp_path: Path) -> None:
    checkpoint_file = tmp_path / "gate_state.json"
    compiled = compile_orchestrator_workflow(
        checkpointer=JsonStateCheckpointer(log_path=checkpoint_file),
        project_root_resolver=lambda _: tmp_path / "gate_ws",
        tool_registry=_make_tool_registry(),
        config=_make_config(),
        triage_service=_make_triage_service(),
    )

    initial = create_initial_graph_state(
        "Build a payment service from scratch",
        project_id="char-gate",
        auto_approve=False,
    )
    transitions, final = _stream_master(compiled, initial, thread_id="char-gate")

    assert [name for name, _ in transitions] == ["triage_node", "planner_node"]
    assert [snap["status"] for _, snap in transitions] == [
        STATUS_RUNNING,
        STATUS_WAITING_APPROVAL,
    ]
    assert final["status"] == STATUS_WAITING_APPROVAL
    assert len(final["plan"]) == 3
    assert final["completed_tasks"] == []
    assert final["architecture_spec"] is None
    assert final["code_files"] == {}
    assert checkpoint_file.is_file()


def test_coder_qa_failure_and_self_correction_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "retry_ws"
    checkpoint_file = tmp_path / "retry_state.json"
    attempt_calls = 0

    def _flaky_pytest(target_dir: Path, **_kwargs: Any) -> tuple[bool, str, str | None]:
        nonlocal attempt_calls
        assert target_dir == project_dir
        attempt_calls += 1
        if attempt_calls == 1:
            return False, "1 failed", "AssertionError: assert 1001 == 2"
        return True, "2 passed", None

    monkeypatch.setattr("company_core.agents.quality_assurance.run_pytest_suite", _flaky_pytest)

    compiled = compile_orchestrator_workflow(
        checkpointer=JsonStateCheckpointer(log_path=checkpoint_file),
        project_root_resolver=lambda _: project_dir,
        simulate_initial_bug=True,
        max_retries=3,
        config=_make_config(),
        triage_service=_make_triage_service(),
    )

    initial = create_initial_graph_state(
        "Build a calculator service from scratch",
        project_id="char-retry",
        max_retries=3,
    )
    final = dict(compiled.invoke(initial, config={"configurable": {"thread_id": "char-retry"}}))

    assert attempt_calls == 2
    assert final["status"] == STATUS_COMPLETED
    assert final["current_phase"] == PHASE_COMPLETED
    assert final["retry_count"] == 1
    assert final["failed_tasks"] == ["qa_attempt_1"]
    assert final["completed_tasks"] == ["architecture", "devops", "code", "qa"]
    assert final["error_message"] is None
    assert final["test_results"] == "PASSED: All tests succeeded."

    log_messages = [entry["message"] for entry in final["execution_logs"]]
    assert any("Tests FAILED on attempt 1" in msg for msg in log_messages)
    assert any("self-correction" in msg.lower() for msg in log_messages)

    engine_source = (project_dir / "app" / "engine.py").read_text(encoding="utf-8")
    assert "return a + b" in engine_source
    assert "+ 999" not in engine_source


def test_persistent_qa_failure_triggers_task_decomposer_and_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "decompose_ws"
    checkpoint_file = tmp_path / "decompose_state.json"
    attempt_calls = 0

    def _fail_twice_then_pass(target_dir: Path, **_kwargs: Any) -> tuple[bool, str, str | None]:
        nonlocal attempt_calls
        assert target_dir == project_dir
        attempt_calls += 1
        if attempt_calls <= 2:
            return False, "1 failed", "AssertionError: persistent failure"
        return True, "2 passed", None

    monkeypatch.setattr(
        "company_core.agents.quality_assurance.run_pytest_suite",
        _fail_twice_then_pass,
    )

    compiled = compile_orchestrator_workflow(
        checkpointer=JsonStateCheckpointer(log_path=checkpoint_file),
        project_root_resolver=lambda _: project_dir,
        max_retries=2,
        config=_make_config(),
        triage_service=_make_triage_service(),
    )

    initial = create_initial_graph_state(
        "Build a calculator service from scratch",
        project_id="char-decompose",
        max_retries=2,
    )
    transitions, final = _stream_master(compiled, initial, thread_id="char-decompose")

    assert attempt_calls == 3
    assert [name for name, _ in transitions] == [
        "triage_node",
        "planner_node",
        "subgraph_router_node",
        "architect_subgraph",
        "coder_qa_subgraph",
        "task_decomposer",
        "coder_qa_subgraph",
    ]

    first_coder_qa = transitions[4][1]
    assert first_coder_qa["status"] == STATUS_RUNNING
    assert first_coder_qa["current_phase"] == PHASE_FAILED
    assert first_coder_qa["retry_count"] == 2
    assert first_coder_qa["failed_tasks"] == ["qa_attempt_1", "qa_attempt_2"]
    assert "code" not in first_coder_qa["completed_tasks"]
    assert first_coder_qa["error_message"] == "AssertionError: persistent failure"

    decomposer_snap = transitions[5][1]
    assert decomposer_snap["current_node"] == "task_decomposer_node"
    assert decomposer_snap["current_phase"] == PHASE_CODER
    assert decomposer_snap["retry_count"] == 0
    assert decomposer_snap["error_message"] is None
    assert "# Task Decomposition Plan" in str(decomposer_snap["architecture_spec"])

    assert final["status"] == STATUS_COMPLETED
    assert final["current_phase"] == PHASE_COMPLETED
    assert final["retry_count"] == 0
    assert final["completed_tasks"] == ["architecture", "devops", "code", "qa"]
    assert final["test_results"] == "PASSED: All tests succeeded."


def test_executor_node_records_provider_error_and_completes_gracefully(tmp_path: Path) -> None:
    failing_provider = _MockAgentProvider(
        provider_id="mock-llm",
        error=ProviderError("simulated upstream 503", provider_id="mock-llm"),
    )

    compiled = compile_orchestrator_workflow(
        checkpointer=JsonStateCheckpointer(log_path=tmp_path / "err_state.json"),
        project_root_resolver=lambda _: tmp_path / "err_ws",
        provider=failing_provider,
        config=_make_config(),
        triage_service=_make_triage_service(),
    )

    initial = create_initial_graph_state("hi", project_id="char-err")
    final = dict(compiled.invoke(initial, config={"configurable": {"thread_id": "char-err"}}))

    expected_msg = "Execution error via provider mock-llm: simulated upstream 503"
    assert final["status"] == STATUS_COMPLETED
    assert final["current_phase"] == PHASE_COMPLETED
    assert final["messages"][-1] == {"role": "assistant", "content": expected_msg}
    assert final["subgraph_results"]["executor"]["output"] == expected_msg


def test_tier3_micro_llm_cascade_and_checkpoint_resume_via_autonomous_orchestrator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tier3_calls: list[tuple[str, str]] = []

    def _mock_tier3(system_prompt: str, user_prompt: str) -> str:
        tier3_calls.append((system_prompt, user_prompt))
        return json.dumps(
            {
                "intent": "ARCHITECT_PIPELINE",
                "confidence": 0.91,
                "requires_workspace_manifest": True,
                "target_model_tier": "reasoning",
                "token_budget": 4096,
            }
        )

    stub_triage = _make_triage_service(semantic_rules=(), tier3_fn=_mock_tier3)
    cfg = _make_config()

    monkeypatch.setattr("company_core.orchestrator_graph.get_triage_service", lambda: stub_triage)
    monkeypatch.setattr("company_core.triage.get_triage_service", lambda: stub_triage)
    monkeypatch.setattr("company_core.orchestrator_graph.get_config", lambda: cfg)
    monkeypatch.setattr(
        "company_core.agents.quality_assurance.run_pytest_suite",
        lambda _target, **_kw: (True, "2 passed", None),
    )

    # Part A: Fresh run falling through Tier 1 & Tier 2 to Tier 3 Micro-LLM
    project_dir = tmp_path / "tier3_ws"
    state_file = tmp_path / "logs" / "tier3_state.json"
    orchestrator = AutonomousStartupOrchestrator(
        checkpoint_path=state_file,
        project_root_resolver=lambda _: project_dir,
        config=cfg,
    )

    final_fresh = orchestrator.run_master_workflow(
        user_requirement="Can you check on this thing?",
        project_id="char-t3",
        reset_state=True,
    )

    assert len(tier3_calls) == 1
    assert tier3_calls[0] == (LOCKED_MICRO_LLM_SYSTEM_PROMPT, "Can you check on this thing?")
    assert final_fresh["subgraph_results"]["triage"]["tier"] == 3
    assert final_fresh["status"] == STATUS_COMPLETED
    assert final_fresh["completed_tasks"] == ["architecture", "code", "devops", "qa"]
    assert state_file.is_file()

    # Part B: Resuming an interrupted checkpoint skips already-completed architect_subgraph
    resume_dir = tmp_path / "resume_ws"
    resume_file = tmp_path / "logs" / "resume_state.json"
    interrupted_state: CompanyState = {
        "project_id": "char-resume",
        "user_requirement": "Can you check on this thing?",
        "current_phase": PHASE_ARCHITECT,
        "architecture_spec": "# Preserved Spec",
        "code_files": {},
        "test_results": None,
        "execution_logs": [],
        "completed_tasks": ["architecture"],
        "failed_tasks": [],
        "error_message": None,
        "retry_count": 0,
    }
    save_company_state(interrupted_state, resume_file)

    resume_orchestrator = AutonomousStartupOrchestrator(
        checkpoint_path=resume_file,
        project_root_resolver=lambda _: resume_dir,
        config=cfg,
    )
    resumed_final = resume_orchestrator.run_master_workflow(
        user_requirement="Ignored when resuming",
        project_id="char-resume",
        resume_if_possible=True,
        reset_state=False,
    )

    assert resumed_final["architecture_spec"] == "# Preserved Spec"
    assert any(
        "Resuming execution" in entry["message"] for entry in resumed_final["execution_logs"]
    )
    assert resumed_final["completed_tasks"] == ["architecture", "code", "devops", "qa"]
    assert resumed_final["status"] == STATUS_COMPLETED


async def test_graph_turn_orchestrator_streams_mocked_events_and_handles_approval_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    triage_svc = _make_triage_service()
    cfg = _make_config()
    tool_reg = _make_tool_registry()

    # Sub-case A: DIRECT_QA fast-path streaming via mocked provider
    sample_artifact = Artifact(
        name="notes.txt",
        kind=ArtifactKind.FILE,
        mime="text/plain",
        size=2,
        inline_content="ok",
    )
    mock_provider = _MockAgentProvider(
        chunks=("Fast ", "streamed answer"),
        artifact=sample_artifact,
    )
    turn_orch = GraphTurnOrchestrator(
        tool_registry=tool_reg,
        config=cfg,
        triage_service=triage_svc,
    )
    qa_task = ExecutionTask.create(
        Prompt(user_prompt="hi", literal=True),
        ExecutionParams(provider_id="mock-llm"),
        workspace=str(tmp_path),
    ).start("mock-llm")

    qa_events: list[StreamEvent] = []
    async for ev in turn_orch.stream_turn(qa_task, mock_provider, require_approval=False):
        qa_events.append(ev)

    assert (tmp_path / "projects" / f"project-{qa_task.id[:8]}").is_dir()
    assert qa_events[0].type is EventType.STATUS
    assert qa_events[0].data["phase"] == "INITIALIZING"
    assert qa_events[1].type is EventType.THOUGHT
    assert qa_events[2].type is EventType.PLAN
    assert qa_events[2].data["steps"][0]["status"] == "in_progress"
    assert qa_events[3].type is EventType.STATUS
    assert "DIRECT_QA" in qa_events[3].data["mermaid_code"]

    streamed_text = "".join(e.data["text"] for e in qa_events if e.type is EventType.TOKEN)
    assert streamed_text == "Fast streamed answer"
    assert any(e.type is EventType.ARTIFACT and e.data["name"] == "notes.txt" for e in qa_events)
    assert any(e.type is EventType.USAGE for e in qa_events)

    plan_events = [e for e in qa_events if e.type is EventType.PLAN]
    assert all(step["status"] == "done" for step in plan_events[-1].data["steps"])
    assert qa_events[-1].type is EventType.DONE
    assert qa_events[-1].data["finish_reason"] == "stop"
    assert len(mock_provider.calls) == 1
    assert mock_provider.calls[0][1] == ()

    # Sub-case B: Approval gate cancellation halts before invoking provider or subgraphs
    cancel_provider = _MockAgentProvider()
    complex_task = ExecutionTask.create(
        Prompt(user_prompt="Build a complete REST service", literal=True),
        ExecutionParams(provider_id="mock-llm"),
        workspace=str(tmp_path),
    ).start("mock-llm")
    approval_evt = asyncio.Event()
    cancel_evt = asyncio.Event()

    stream_gen = turn_orch.stream_turn(
        complex_task,
        cancel_provider,
        require_approval=True,
        approval_event=approval_evt,
        cancel_event=cancel_evt,
    )
    try:
        waiting_ev: StreamEvent | None = None
        async for ev in stream_gen:
            if ev.type is EventType.STATUS and ev.data.get("state") == "waiting_approval":
                waiting_ev = ev
                break
        assert waiting_ev is not None
        assert "flowchart" in waiting_ev.data["mermaid_code"]

        cancel_evt.set()
        remaining = [ev async for ev in stream_gen]
    finally:
        await stream_gen.aclose()

    assert [e.type for e in remaining] == [EventType.STATUS, EventType.DONE]
    assert remaining[0].data["state"] == "cancelled"
    assert remaining[1].data["finish_reason"] == "cancelled"
    assert cancel_provider.calls == []
