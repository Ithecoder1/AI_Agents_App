"""OOP unit and contract tests for orchestration sub-graph builders, nodes, and services."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from app.application.tools.registry import DynamicToolRegistry
from app.domain.graph_state import (
    INTENT_ARCHITECTURE_DESIGN,
    PHASE_COMPLETED,
    PHASE_DEVOPS,
    PHASE_FAILED,
    PHASE_QA,
    STATUS_COMPLETED,
    STATUS_RUNNING,
    GraphState,
    create_initial_graph_state,
)
from app.domain.ports import ICodeWorkspaceRunner, TestRunResult
from app.domain.tools import ToolDescriptor
from app.infrastructure.orchestration import (
    ArchitectSubGraphBuilder,
    BaseGraphNode,
    BaseSubGraphBuilder,
    BlueprintGeneratorNode,
    BlueprintGeneratorService,
    BlueprintSchemaValidator,
    CoderNode,
    CoderQARoutePolicy,
    CoderQASubGraphBuilder,
    CodeSynthesisService,
    DevOpsEnvironmentService,
    DevOpsTestNode,
    EvaluatorNode,
    ExecutionLogManager,
    LocalCodeWorkspaceRunner,
    RequirementsParserNode,
    RequirementsParserService,
    SchemaValidatorNode,
    TaskDecompositionStrategy,
    TestResultEvaluator,
)
from company_core.subgraphs import architect_subgraph as legacy_architect
from company_core.subgraphs import coder_qa_subgraph as legacy_coder_qa
from langgraph.graph import END


class _FakeWorkspaceRunner:
    """In-memory ``ICodeWorkspaceRunner`` fake verifying DI without disk I/O."""

    def __init__(self, *, results: list[TestRunResult] | None = None) -> None:
        self.written_batches: list[tuple[Path, dict[str, str]]] = []
        self.test_runs: list[tuple[Path, float]] = []
        self._results = list(results) if results is not None else [TestRunResult.passed("ok")]
        self._files: dict[str, str] = {}

    def resolve_project_dir(self, project_id: str) -> Path:
        return Path("/virtual-workspaces") / project_id

    def write_files(self, target_dir: Path, files: Mapping[str, str]) -> list[str]:
        copied = {str(k): str(v) for k, v in files.items()}
        self.written_batches.append((target_dir, copied))
        self._files.update(copied)
        return list(copied.keys())

    def read_files(
        self,
        target_dir: Path,
        extensions: tuple[str, ...] = (".py", ".json", ".yaml", ".yml", ".md", ".txt"),
    ) -> dict[str, str]:
        _ = (target_dir, extensions)
        return dict(self._files)

    def summarize_code(
        self,
        files: Mapping[str, str],
        max_lines_per_file: int = 50,
        strip_comments: bool = True,
    ) -> str:
        _ = (max_lines_per_file, strip_comments)
        return "\n".join(sorted(files))

    def run_tests(self, target_dir: Path, timeout_s: float = 30.0) -> TestRunResult:
        self.test_runs.append((target_dir, timeout_s))
        if len(self._results) > 1:
            return self._results.pop(0)
        return self._results[0]


def test_architect_services_and_callable_nodes() -> None:
    log_manager = ExecutionLogManager()
    parser = RequirementsParserService()
    generator = BlueprintGeneratorService()
    validator = BlueprintSchemaValidator()

    parser_node = RequirementsParserNode(parser=parser, log_manager=log_manager)
    blueprint_node = BlueprintGeneratorNode(generator=generator, log_manager=log_manager)
    validator_node = SchemaValidatorNode(validator=validator, log_manager=log_manager)

    assert isinstance(parser_node, BaseGraphNode)
    assert isinstance(blueprint_node, BaseGraphNode)
    assert isinstance(validator_node, BaseGraphNode)

    state = create_initial_graph_state("Build calculator 1+1", project_id="arch-oop")
    state["intent"] = INTENT_ARCHITECTURE_DESIGN

    after_parse = cast("GraphState", asyncio.run(parser_node(state)))
    assert after_parse["current_node"] == "requirements_parser_node"
    assert after_parse["subgraph_results"]["architecture_requirements"]["domain_type"] == (
        "arithmetic_evaluator"
    )

    after_gen = cast("GraphState", blueprint_node.execute(after_parse))
    assert after_gen["current_node"] == "blueprint_generator_node"
    assert validator.validate(after_gen["architecture_spec"] or "") is True

    after_val = cast("GraphState", asyncio.run(validator_node(after_gen)))
    assert "architecture" in after_val["completed_tasks"]
    assert after_val["subgraph_results"]["architecture_spec_valid"] is True
    assert after_val["status"] == STATUS_COMPLETED
    assert after_val["current_phase"] == PHASE_COMPLETED


@pytest.mark.asyncio
async def test_architect_builder_sync_and_async_execution() -> None:
    builder = ArchitectSubGraphBuilder()
    assert isinstance(builder, BaseSubGraphBuilder)
    compiled = builder.compile()

    initial = create_initial_graph_state("Design payment microservice", project_id="arch-b")
    sync_out = compiled.invoke(initial)
    assert "architecture" in sync_out["completed_tasks"]
    assert sync_out["subgraph_results"]["architecture_requirements"]["domain_type"] == (
        "business_engine"
    )

    async_initial = create_initial_graph_state("Architecture diagram only", project_id="arch-a")
    async_initial["intent"] = INTENT_ARCHITECTURE_DESIGN
    async_out = await compiled.ainvoke(async_initial)
    assert async_out["status"] == STATUS_COMPLETED
    assert async_out["current_phase"] == PHASE_COMPLETED


@pytest.mark.asyncio
async def test_coder_qa_nodes_with_di_fake_and_async_offload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_runner = _FakeWorkspaceRunner(
        results=[
            TestRunResult.failed("AssertionError: 1001 != 2", clean_traceback="assert 1001 == 2"),
            TestRunResult.passed("2 passed"),
        ]
    )
    assert isinstance(fake_runner, ICodeWorkspaceRunner)

    log_manager = ExecutionLogManager()
    tool_registry = DynamicToolRegistry(
        [
            ToolDescriptor("write_file", "filesystem", "Write and edit workspace files"),
            ToolDescriptor("view_file", "filesystem", "Read file contents"),
        ]
    )

    coder = CoderNode(
        workspace_runner=fake_runner,
        synthesis_service=CodeSynthesisService(),
        log_manager=log_manager,
        tool_registry=tool_registry,
        simulate_initial_bug=True,
    )
    devops = DevOpsTestNode(
        workspace_runner=fake_runner,
        devops_service=DevOpsEnvironmentService(),
        log_manager=log_manager,
    )
    evaluator = EvaluatorNode(
        workspace_runner=fake_runner,
        evaluator=TestResultEvaluator(),
        log_manager=log_manager,
        timeout_s=12.0,
    )

    offloaded: list[str] = []
    real_to_thread = asyncio.to_thread

    async def _track_to_thread(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        owner = getattr(getattr(func, "__self__", None), "name", getattr(func, "__name__", ""))
        offloaded.append(str(owner))
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _track_to_thread)

    state = create_initial_graph_state("Add 1+1", project_id="di-fake")
    after_coder = cast("GraphState", await coder(state))
    assert after_coder["current_phase"] == PHASE_DEVOPS
    assert len(after_coder["active_tools"]) >= 1
    assert "Simulated bug" in after_coder["code_files"]["app/engine.py"]

    after_devops = cast("GraphState", await devops(after_coder))
    assert after_devops["current_phase"] == PHASE_QA
    assert "requirements.txt" in after_devops["code_files"]

    after_fail = cast("GraphState", await evaluator(after_devops))
    assert after_fail["current_phase"] == PHASE_FAILED
    assert after_fail["retry_count"] == 1
    assert "code" not in after_fail["completed_tasks"]

    policy = CoderQARoutePolicy(default_max_retries=2)
    assert policy(after_fail) == "coder_node"

    after_retry_coder = cast("GraphState", await coder(after_fail))
    assert "Simulated bug" not in after_retry_coder["code_files"]["app/engine.py"]

    after_pass = cast("GraphState", await evaluator(after_retry_coder))
    assert after_pass["current_phase"] == PHASE_COMPLETED
    assert after_pass["status"] == STATUS_COMPLETED
    assert policy(after_pass) == str(END)

    assert offloaded == [
        "coder_node",
        "devops_test_node",
        "evaluator_node",
        "coder_node",
        "evaluator_node",
    ]
    assert fake_runner.test_runs[0][1] == 12.0

    plan = TaskDecompositionStrategy().decompose("Add 1+1", "Traceback error", 3)
    assert "Task Decomposition Plan" in plan


@pytest.mark.asyncio
async def test_coder_qa_builder_self_correction_and_retry_limit(tmp_path: Path) -> None:
    runner = LocalCodeWorkspaceRunner(base_workspace_dir=tmp_path)
    builder = CoderQASubGraphBuilder(
        workspace_runner=runner,
        simulate_initial_bug=True,
        max_retries=3,
    )
    assert isinstance(builder, BaseSubGraphBuilder)
    compiled = builder.compile()

    initial = create_initial_graph_state("Build 1+1 evaluator", project_id="loop-proj")
    initial["status"] = STATUS_RUNNING

    final = await compiled.ainvoke(initial)
    assert final["status"] == STATUS_COMPLETED
    assert final["retry_count"] == 1
    assert "PASSED" in (final["test_results"] or "")
    assert (tmp_path / "loop-proj" / "app" / "engine.py").is_file()

    always_fail_runner = _FakeWorkspaceRunner(
        results=[TestRunResult.failed("persistent failure", clean_traceback="persistent failure")]
    )
    exhausted_graph = CoderQASubGraphBuilder(
        workspace_runner=always_fail_runner,
        max_retries=2,
    ).compile()
    exhausted_out = exhausted_graph.invoke(
        create_initial_graph_state("Fail", project_id="fail-p", max_retries=2)
    )
    assert exhausted_out["retry_count"] == 2
    assert CoderQARoutePolicy(default_max_retries=2)(exhausted_out) == str(END)


def test_legacy_company_core_subgraph_shims(tmp_path: Path) -> None:
    arch_state = create_initial_graph_state("Add numbers", project_id="shim-arch")
    parsed = legacy_architect.requirements_parser_node(arch_state)
    built = legacy_architect.blueprint_generator_node(parsed)
    validated = legacy_architect.schema_validator_node(built)
    assert "architecture" in validated["completed_tasks"]

    compiled_arch = legacy_architect.compile_architect_subgraph()
    assert "architecture" in compiled_arch.invoke(arch_state)["completed_tasks"]

    def _resolver(pid: str) -> Path:
        p = tmp_path / pid
        p.mkdir(parents=True, exist_ok=True)
        return p

    compiled_cqa = legacy_coder_qa.compile_coder_qa_subgraph(
        project_root_resolver=_resolver,
        simulate_initial_bug=False,
    )
    cqa_out = compiled_cqa.invoke(create_initial_graph_state("Add numbers", project_id="shim-cqa"))
    assert cqa_out["status"] == STATUS_COMPLETED
    assert callable(legacy_coder_qa.make_coder_qa_route())
