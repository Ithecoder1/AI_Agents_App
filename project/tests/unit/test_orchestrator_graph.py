"""Unit & integration tests for deterministic LangGraph Orchestrator Graph & Sub-Graphs."""

from __future__ import annotations

from pathlib import Path

from app.application.tools.registry import DynamicToolRegistry
from app.domain.enums import EventType
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
from app.domain.models import ExecutionParams, ExecutionTask, Prompt
from app.domain.tools import ToolDescriptor
from company_core.config import ConfigManager
from company_core.orchestrator import AutonomousStartupOrchestrator
from company_core.orchestrator_graph import (
    compile_orchestrator_workflow,
    make_executor_node,
    make_planner_node,
    make_subgraph_router_node,
    make_triage_node,
    route_after_architect,
    route_after_planner,
    route_after_router,
    route_after_triage,
)
from company_core.persistence import JsonStateCheckpointer
from company_core.skills import get_default_skill_registry
from company_core.state import create_initial_state
from company_core.subgraphs.architect_subgraph import (
    compile_architect_subgraph,
)
from company_core.subgraphs.coder_qa_subgraph import (
    compile_coder_qa_subgraph,
    make_coder_qa_route,
)
from company_core.turn_orchestrator import GraphTurnOrchestrator, generate_mermaid_workflow
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


# --------------------------------------------------------------------------- #
# 6. Dynamic, Intent-Driven Orchestration, YAML Profiles & Modular Skills
# --------------------------------------------------------------------------- #
def test_config_profiles_and_skills_loading() -> None:
    cfg = ConfigManager()
    skills = get_default_skill_registry()

    assert INTENT_DIRECT_QA in cfg.profiles
    assert INTENT_FOCUSED_TOOL_TASK in cfg.profiles
    assert INTENT_ARCHITECTURE_DESIGN in cfg.profiles
    assert INTENT_CODE_TEST_FIX in cfg.profiles
    assert INTENT_COMPLEX_PIPELINE in cfg.profiles

    # Intent classifications from orchestrator_profiles.yaml
    p_qa = cfg.classify_intent("explain what hexagonal architecture is")
    assert p_qa.intent == INTENT_DIRECT_QA
    assert p_qa.fast_path is True
    assert p_qa.tool_policy.is_tool_less is True

    p_focused = cfg.classify_intent("Analyze and inspect workspace code structure")
    assert p_focused.intent == INTENT_FOCUSED_TOOL_TASK
    assert p_focused.subgraphs == ()
    assert "code_analysis" in p_focused.skills

    p_arch = cfg.classify_intent("Design a modular system architecture blueprint")
    assert p_arch.intent == INTENT_ARCHITECTURE_DESIGN
    assert p_arch.subgraphs == ("architect_subgraph",)

    p_fix = cfg.classify_intent("Fix bug and test unit assertions for 1+1")
    assert p_fix.intent == INTENT_CODE_TEST_FIX
    assert p_fix.subgraphs == ("coder_qa_subgraph",)

    p_full = cfg.classify_intent(
        "Build a complete FastAPI user authentication service with JWT and automated pytest suite"
    )
    assert p_full.intent == INTENT_COMPLEX_PIPELINE
    assert p_full.subgraphs == ("architect_subgraph", "coder_qa_subgraph")

    # Modular SkillRegistry resolution
    resolved = skills.resolve_for_profile(p_full.skills)
    assert [s.name for s in resolved] == [
        "architecture_planning",
        "code_analysis",
        "code_verification",
    ]
    assert "view_file" in resolved[1].spec.recommended_tools
    assert "pytest" in skills.build_tool_query(p_fix.skills)


def test_dynamic_fast_path_direct_qa_bypasses_subgraphs(tmp_path: Path) -> None:
    tool_reg = DynamicToolRegistry(
        [
            ToolDescriptor("view_file", "filesystem", "View file contents"),
            ToolDescriptor("run_command", "terminal", "Run terminal shell command"),
        ]
    )

    for idx, prompt_text in enumerate(("hi", "explain what hexagonal architecture is")):
        target_dir = tmp_path / f"fast_path_{idx}"
        checkpointer = JsonStateCheckpointer(log_path=tmp_path / f"chk_fast_{idx}.json")
        compiled = compile_orchestrator_workflow(
            checkpointer=checkpointer,
            project_root_resolver=lambda _, d=target_dir: d,
            tool_registry=tool_reg,
        )

        initial = create_initial_graph_state(prompt_text, project_id=f"fast-{idx}")
        final = compiled.invoke(initial, config={"configurable": {"thread_id": f"fast-{idx}"}})

        assert final["intent"] == INTENT_DIRECT_QA
        assert final["task_type"] == "DIRECT_QA"
        assert final["status"] == STATUS_COMPLETED
        assert final["active_tools"] == []
        assert final["active_skills"] == ["direct_qa"]
        assert final["completed_tasks"] == []
        assert final["architecture_spec"] is None
        assert final["code_files"] == {}

        agents_executed = [entry["agent"] for entry in final["execution_logs"]]
        assert "ceo_triage" in agents_executed
        assert "executor" in agents_executed
        assert "ceo_planner" not in agents_executed
        assert "subgraph_router" not in agents_executed
        assert "architect" not in agents_executed
        assert "coder" not in agents_executed
        assert "qa" not in agents_executed


def test_dynamic_focused_tool_task_bypasses_heavy_subgraphs(tmp_path: Path) -> None:
    tool_reg = DynamicToolRegistry(
        [
            ToolDescriptor("view_file", "filesystem", "View file contents"),
            ToolDescriptor("grep_search", "search", "Search pattern across files"),
            ToolDescriptor("list_dir", "filesystem", "List directory contents"),
            ToolDescriptor("write_to_file", "filesystem", "Write contents to a file"),
        ]
    )
    checkpointer = JsonStateCheckpointer(log_path=tmp_path / "chk_focused.json")
    compiled = compile_orchestrator_workflow(
        checkpointer=checkpointer,
        project_root_resolver=lambda _: tmp_path / "focused",
        tool_registry=tool_reg,
    )

    initial = create_initial_graph_state(
        "Analyze and inspect project workspace files",
        project_id="focused-1",
    )
    final = compiled.invoke(initial, config={"configurable": {"thread_id": "focused-1"}})

    assert final["intent"] == INTENT_FOCUSED_TOOL_TASK
    assert final["status"] == STATUS_COMPLETED
    assert final["active_skills"] == ["code_analysis"]
    assert len(final["active_tools"]) > 0
    assert final["completed_tasks"] == []
    agents_executed = [entry["agent"] for entry in final["execution_logs"]]
    assert "executor" in agents_executed
    assert "architect" not in agents_executed
    assert "coder" not in agents_executed


def test_dynamic_architecture_only_bypasses_coder_qa(tmp_path: Path) -> None:
    target_dir = tmp_path / "arch_only"
    checkpointer = JsonStateCheckpointer(log_path=tmp_path / "chk_arch.json")
    compiled = compile_orchestrator_workflow(
        checkpointer=checkpointer,
        project_root_resolver=lambda _: target_dir,
    )

    initial = create_initial_graph_state(
        "Design a modular system architecture blueprint",
        project_id="arch-only-1",
    )
    final = compiled.invoke(initial, config={"configurable": {"thread_id": "arch-only-1"}})

    assert final["intent"] == INTENT_ARCHITECTURE_DESIGN
    assert final["task_type"] == "ARCHITECTURE"
    assert final["status"] == STATUS_COMPLETED
    assert final["architecture_spec"] is not None
    assert "architecture" in final["completed_tasks"]
    assert "code" not in final["completed_tasks"]
    assert "qa" not in final["completed_tasks"]
    assert final["code_files"] == {}
    assert route_after_architect(final) == "__end__"


def test_dynamic_code_test_fix_bypasses_architect(tmp_path: Path) -> None:
    target_dir = tmp_path / "code_fix_only"
    checkpointer = JsonStateCheckpointer(log_path=tmp_path / "chk_fix.json")
    compiled = compile_orchestrator_workflow(
        checkpointer=checkpointer,
        project_root_resolver=lambda _: target_dir,
        simulate_initial_bug=False,
    )

    initial = create_initial_graph_state(
        "Fix bug and test unit assertions for 1+1",
        project_id="fix-only-1",
    )
    final = compiled.invoke(initial, config={"configurable": {"thread_id": "fix-only-1"}})

    assert final["intent"] == INTENT_CODE_TEST_FIX
    assert final["task_type"] == "CODE_TEST_FIX"
    assert final["status"] == STATUS_COMPLETED
    assert "code" in final["completed_tasks"]
    assert "devops" in final["completed_tasks"]
    assert "qa" in final["completed_tasks"]
    assert "architecture" not in final["completed_tasks"]
    assert final["architecture_spec"] is None
    assert "PASSED" in str(final["test_results"])


def test_conditional_routing_helpers() -> None:
    s_qa = create_initial_graph_state("hi")
    s_qa["intent"] = INTENT_DIRECT_QA
    s_qa["task_type"] = "DIRECT_QA"
    assert route_after_triage(s_qa) == "executor_node"

    s_qa_gated = create_initial_graph_state("hi", auto_approve=False)
    s_qa_gated["intent"] = INTENT_DIRECT_QA
    assert route_after_triage(s_qa_gated) == "planner_node"

    s_focused = create_initial_graph_state("inspect file")
    s_focused["intent"] = INTENT_FOCUSED_TOOL_TASK
    s_focused["status"] = STATUS_RUNNING
    assert route_after_planner(s_focused) == "executor_node"

    s_wait = create_initial_graph_state("build service")
    s_wait["status"] = STATUS_WAITING_APPROVAL
    assert route_after_planner(s_wait) == "__end__"


async def test_turn_orchestrator_dynamic_fast_path_and_mermaid() -> None:
    fake_cfg = make_config("fake-turn", "fake", options={"chunks": ["Fast ", "Answer"]})
    fake_prov = FakeProvider(fake_cfg, make_deps())
    tool_reg = DynamicToolRegistry(
        [ToolDescriptor("view_file", "filesystem", "View file contents")]
    )
    orchestrator = GraphTurnOrchestrator(tool_registry=tool_reg)

    prompt = Prompt(user_prompt="explain what hexagonal architecture is", literal=True)
    task = ExecutionTask.create(
        prompt, ExecutionParams(provider_id="fake-turn"), workspace="."
    ).start("fake-turn")

    events = []
    async for ev in orchestrator.stream_turn(
        task,
        fake_prov,
        require_approval=False,
        active_tools=list(tool_reg.all_descriptors()),
    ):
        events.append(ev)

    status_with_graph = [
        e for e in events if e.type is EventType.STATUS and "mermaid_code" in e.data
    ]
    assert len(status_with_graph) >= 1
    assert "DIRECT_QA" in status_with_graph[0].data["mermaid_code"]

    tokens = "".join(e.data["text"] for e in events if e.type is EventType.TOKEN)
    assert "Fast Answer" in tokens

    plan_events = [e for e in events if e.type is EventType.PLAN]
    assert len(plan_events) >= 2
    assert plan_events[0].data["steps"][0]["status"] == "in_progress"
    assert all(s["status"] == "done" for s in plan_events[-1].data["steps"])

    # Distinct intent-specific Mermaid diagrams generated cleanly
    assert "ArchitectSub" in generate_mermaid_workflow(
        "Design a modular system architecture blueprint",
        intent=INTENT_ARCHITECTURE_DESIGN,
    )
    assert "CoderSub" in generate_mermaid_workflow(
        "Fix bug and test unit assertions",
        intent=INTENT_CODE_TEST_FIX,
    )
    assert "ArchitectSub" not in generate_mermaid_workflow(
        "Fix bug and test unit assertions",
        intent=INTENT_CODE_TEST_FIX,
    )
