"""Unit & integration tests for deterministic LangGraph Orchestrator Graph & Sub-Graphs."""

from __future__ import annotations

from pathlib import Path

from app.application.tools.registry import DynamicToolRegistry
from app.domain.graph_state import (
    STATUS_COMPLETED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_WAITING_APPROVAL,
    create_initial_graph_state,
)
from app.domain.tools import ToolDescriptor
from company_core.orchestrator import AutonomousStartupOrchestrator
from company_core.orchestrator_graph import (
    compile_orchestrator_workflow,
    make_executor_node,
    make_planner_node,
    make_subgraph_router_node,
    make_triage_node,
    route_after_router,
)
from company_core.state import create_initial_state
from company_core.subgraphs.architect_subgraph import (
    compile_architect_subgraph,
)
from company_core.subgraphs.coder_qa_subgraph import (
    compile_coder_qa_subgraph,
    make_coder_qa_route,
)
from providers.adapters.fake import FakeProvider

from tests.factories import make_config, make_deps


# --------------------------------------------------------------------------- #
# 1. State Model Tests
# --------------------------------------------------------------------------- #
def test_graph_state_initialization() -> None:
    state = create_initial_graph_state("Build an arithmetic calculator", project_id="test-p1")
    assert state["project_id"] == "test-p1"
    assert state["session_id"] == "sess-test-p1"
    assert state["user_requirement"] == "Build an arithmetic calculator"
    assert state["status"] == STATUS_PENDING
    assert state["current_node"] == "triage_node"
    assert state["messages"][0]["content"] == "Build an arithmetic calculator"
    assert state["active_tools"] == []
    assert state["subgraph_results"] == {}
    assert len(state["execution_logs"]) == 1


def test_company_state_extends_graph_state() -> None:
    c_state = create_initial_state("Evaluate math", project_id="p2")
    assert c_state["session_id"] == "sess-p2"
    assert c_state["status"] == STATUS_PENDING
    assert c_state["current_phase"] == "ARCHITECT"
    assert c_state["code_files"] == {}


# --------------------------------------------------------------------------- #
# 2. Master Graph Nodes: Triage, Planner, Router, Executor
# --------------------------------------------------------------------------- #
def test_triage_node_heuristics_and_jit_tools() -> None:
    tool_reg = DynamicToolRegistry(
        [
            ToolDescriptor("view_file", "filesystem", "View file contents"),
            ToolDescriptor("run_command", "terminal", "Run terminal shell command"),
        ]
    )
    triage = make_triage_node(tool_registry=tool_reg)

    # 1. Lightweight Direct QA query (e.g. "hi")
    state_hi = create_initial_graph_state("hi")
    out_hi = triage(state_hi)
    assert out_hi["task_type"] == "DIRECT_QA"
    assert out_hi["status"] == STATUS_RUNNING
    assert out_hi["active_tools"] == []  # 0 tools injected for direct QA

    # 2. Architecture-specific query
    state_arch = create_initial_graph_state("Design a modular system architecture blueprint")
    out_arch = triage(state_arch)
    assert out_arch["task_type"] == "ARCHITECTURE"
    assert len(out_arch["active_tools"]) > 0

    # 3. Code fix query
    state_code = create_initial_graph_state("Fix bug and test unit assertions in core engine")
    out_code = triage(state_code)
    assert out_code["task_type"] == "CODE_TEST_FIX"

    # 4. Monolithic full pipeline query
    state_full = create_initial_graph_state(
        "Implement a deterministic arithmetic calculator engine"
    )
    out_full = triage(state_full)
    assert out_full["task_type"] == "FULL_PIPELINE"


def test_planner_node_approval_gating() -> None:
    planner = make_planner_node()

    # Auto-approve mode -> RUNNING
    s_auto = create_initial_graph_state("build app", auto_approve=True)
    s_auto["task_type"] = "FULL_PIPELINE"
    p_auto = planner(s_auto)
    assert p_auto["status"] == STATUS_RUNNING
    assert len(p_auto["plan"]) == 3

    # Non-auto-approve mode -> WAITING_APPROVAL
    s_gate = create_initial_graph_state("build app", auto_approve=False)
    s_gate["task_type"] = "FULL_PIPELINE"
    p_gate = planner(s_gate)
    assert p_gate["status"] == STATUS_WAITING_APPROVAL


def test_subgraph_router_routing_decisions() -> None:
    router_node = make_subgraph_router_node()
    state = create_initial_graph_state("test")

    # 1. Direct QA routes to executor_node
    state["task_type"] = "DIRECT_QA"
    state = router_node(state)
    assert route_after_router(state) == "executor_node"

    # 2. Architecture routes to architect_subgraph
    state["task_type"] = "ARCHITECTURE"
    assert route_after_router(state) == "architect_subgraph"

    # 3. Code-test-fix routes to coder_qa_subgraph
    state["task_type"] = "CODE_TEST_FIX"
    assert route_after_router(state) == "coder_qa_subgraph"

    # 4. Gated status returns END
    state["status"] = STATUS_WAITING_APPROVAL
    assert route_after_router(state) == "__end__"


def test_executor_node_direct_and_provider_dispatch() -> None:
    # 1. Deterministic default execution
    executor = make_executor_node(provider=None)
    state = create_initial_graph_state("1+1")
    res = executor(state)
    assert res["status"] == STATUS_COMPLETED
    assert "2" in res["messages"][-1]["content"]

    # 2. Dispatch via FakeProvider (standard IAgentProvider port)
    fake_cfg = make_config("fake-exec", "fake", options={"chunks": ["Echo: ", "OK"]})
    fake_prov = FakeProvider(fake_cfg, make_deps())
    executor_prov = make_executor_node(provider=fake_prov)
    state_prov = create_initial_graph_state("Test prompt")
    res_prov = executor_prov(state_prov)
    assert res_prov["status"] == STATUS_COMPLETED
    assert "Echo: OK" in res_prov["messages"][-1]["content"]


# --------------------------------------------------------------------------- #
# 3. Architecture Sub-Graph Isolated Execution
# --------------------------------------------------------------------------- #
def test_architect_subgraph_isolated_run() -> None:
    subgraph = compile_architect_subgraph()
    initial = create_initial_graph_state("Design a hexagonal calculator system")

    final = subgraph.invoke(initial)
    assert final["architecture_spec"] is not None
    assert "architecture" in final["completed_tasks"]
    assert final["subgraph_results"]["architecture_spec_valid"] is True
    assert final["current_node"] == "schema_validator_node"


# --------------------------------------------------------------------------- #
# 4. Code-Test-Fix Sub-Graph & Self-Correction Loop
# --------------------------------------------------------------------------- #
def test_coder_qa_subgraph_self_correction_loop(tmp_path: Path) -> None:
    target_dir = tmp_path / "coder_qa_test"

    # Simulate an intentional bug on attempt 0 to verify self-correction loop
    subgraph = compile_coder_qa_subgraph(
        project_root_resolver=lambda _: target_dir,
        simulate_initial_bug=True,
        max_retries=3,
    )

    initial = create_initial_graph_state("solve: 1+1 arithmetic evaluator")
    initial["project_id"] = "test-cq"

    final = subgraph.invoke(initial)
    assert (target_dir / "app" / "engine.py").is_file()
    assert (target_dir / "tests" / "test_engine.py").is_file()
    assert "code" in final["completed_tasks"]
    assert "devops" in final["completed_tasks"]
    assert "qa" in final["completed_tasks"]
    assert final["retry_count"] == 1
    assert "PASSED" in str(final["test_results"])


def test_coder_qa_route_decision_logic() -> None:
    route_fn = make_coder_qa_route(max_retries=2)

    # 1. Tests pass -> END
    pass_state = create_initial_graph_state("pass")
    pass_state["test_results"] = "PASSED: 2 passed in 0.05s"
    assert route_fn(pass_state) == "__end__"

    # 2. Tests fail and retry_count < max -> coder_node (self-correction)
    fail_state = create_initial_graph_state("fail")
    fail_state["test_results"] = "FAILED: AssertionError"
    fail_state["retry_count"] = 1
    fail_state["max_retries"] = 2
    assert route_fn(fail_state) == "coder_node"

    # 3. Tests fail and retries exhausted -> END
    exhausted_state = create_initial_graph_state("fail")
    exhausted_state["test_results"] = "FAILED: AssertionError"
    exhausted_state["retry_count"] = 2
    exhausted_state["max_retries"] = 2
    assert route_fn(exhausted_state) == "__end__"


# --------------------------------------------------------------------------- #
# 5. Master Orchestrator Graph End-to-End Execution
# --------------------------------------------------------------------------- #
def test_master_orchestrator_graph_full_pipeline(tmp_path: Path) -> None:
    target_dir = tmp_path / "master_pipeline"

    compiled_master = compile_orchestrator_workflow(
        project_root_resolver=lambda _: target_dir,
        simulate_initial_bug=False,
    )

    initial = create_initial_graph_state("solve: 1+1 arithmetic evaluator", project_id="master-e2e")
    final = compiled_master.invoke(initial, config={"configurable": {"thread_id": "master-e2e"}})

    assert "architecture" in final["completed_tasks"]
    assert "code" in final["completed_tasks"]
    assert "devops" in final["completed_tasks"]
    assert "qa" in final["completed_tasks"]
    assert (target_dir / "app" / "engine.py").is_file()
    assert (target_dir / "tests" / "test_engine.py").is_file()
    assert "PASSED" in str(final["test_results"])


def test_autonomous_orchestrator_master_workflow_method(tmp_path: Path) -> None:
    target_dir = tmp_path / "orch_master"
    state_file = tmp_path / "logs" / "master_state.json"

    orchestrator = AutonomousStartupOrchestrator(
        checkpoint_path=state_file,
        project_root_resolver=lambda _: target_dir,
    )

    final_state = orchestrator.run_master_workflow(
        user_requirement="solve: 1+1 arithmetic evaluator",
        project_id="orch-master-test",
        reset_state=True,
    )

    assert "architecture" in final_state["completed_tasks"]
    assert "code" in final_state["completed_tasks"]
    assert "devops" in final_state["completed_tasks"]
    assert "qa" in final_state["completed_tasks"]
    assert (target_dir / "app" / "engine.py").is_file()
    assert state_file.is_file()
