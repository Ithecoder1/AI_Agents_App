"""Unit & Integration Tests for the LangGraph Autonomous Startup Orchestrator."""

from __future__ import annotations

from pathlib import Path

from company_core.config import get_config
from company_core.graph import (
    ceo_router_node,
    compile_company_workflow,
    make_route_after_qa,
    route_after_ceo,
    task_decomposer_node,
)
from company_core.persistence import (
    JsonStateCheckpointer,
    can_resume,
    determine_next_incomplete_phase,
    hydrate_resumed_state,
    load_company_state,
    save_company_state,
)
from company_core.state import (
    PHASE_ARCHITECT,
    PHASE_CODER,
    PHASE_COMPLETED,
    PHASE_DEVOPS,
    PHASE_FAILED,
    PHASE_QA,
    append_execution_log,
    create_initial_state,
)
from company_core.tools import (
    extract_clean_traceback,
    summarize_code_for_context,
)


# --------------------------------------------------------------------------- #
# 1. State Schema Tests
# --------------------------------------------------------------------------- #
def test_company_state_initialization() -> None:
    state = create_initial_state("Build a REST service", project_id="test-proj")
    assert state["project_id"] == "test-proj"
    assert state["user_requirement"] == "Build a REST service"
    assert state["current_phase"] == PHASE_ARCHITECT
    assert state["architecture_spec"] is None
    assert state["code_files"] == {}
    assert state["completed_tasks"] == []
    assert state["failed_tasks"] == []
    assert state["error_message"] is None
    assert state["retry_count"] == 0
    assert len(state["execution_logs"]) == 1


def test_append_execution_log() -> None:
    logs = []
    updated = append_execution_log(
        logs, agent="ceo", phase=PHASE_ARCHITECT, message="Started", details={"step": 1}
    )
    assert len(updated) == 1
    assert updated[0]["agent"] == "ceo"
    assert updated[0]["phase"] == PHASE_ARCHITECT
    assert updated[0]["details"] == {"step": 1}


# --------------------------------------------------------------------------- #
# 2. Configuration & Decoupled Personas Tests
# --------------------------------------------------------------------------- #
def test_config_manager_loads_agents_and_tasks() -> None:
    cfg = get_config()
    ceo = cfg.get_agent("ceo")
    assert ceo.role == "Chief Executive Officer & Orchestration Router"
    assert "orchestration" in ceo.system_prompt.lower()
    assert ceo.context_policy.include_full_code is False

    coder = cfg.get_agent("coder")
    assert coder.role == "Senior Software Engineer"
    assert coder.context_policy.include_summarized_code is True

    decomposer = cfg.get_agent("task_decomposer")
    assert "decomposer" in decomposer.role.lower()

    assert cfg.workflow.max_retries >= 1
    assert cfg.workflow.token_economy.max_prompt_lines_per_file > 0


# --------------------------------------------------------------------------- #
# 3. Context Window Hygiene & Token Efficiency Tests
# --------------------------------------------------------------------------- #
def test_summarize_code_for_context_truncates_large_files() -> None:
    # Generate a large 100-line Python file
    large_code = "\n".join([f"def func_{i}():\n    return {i}" for i in range(100)])
    files = {"src/big_module.py": large_code}

    summary = summarize_code_for_context(files, max_lines_per_file=30)
    assert "File: src/big_module.py (200 lines)" in summary
    assert "collapsed" in summary
    # Verify summary is significantly shorter than full text
    assert len(summary) < len(large_code)


def test_extract_clean_traceback_isolates_failures() -> None:
    raw_pytest = """============================= test session starts =============================
platform win32 -- Python 3.10.9, pytest-8.3.4
collected 1 item

tests/test_math.py F                                                     [100%]

=================================== FAILURES ===================================
___________________________ test_solve_one_plus_one ___________________________
    def test_solve_one_plus_one():
>       assert solve_arithmetic("1+1") == 2
E       AssertionError: assert 1001 == 2
tests/test_math.py:4: AssertionError
=========================== short test summary info ===========================
FAILED tests/test_math.py::test_solve_one_plus_one - AssertionError: assert 1001 == 2
============================== 1 failed in 0.05s ===============================
"""
    clean = extract_clean_traceback(raw_pytest)
    assert "AssertionError: assert 1001 == 2" in clean
    assert "platform win32" not in clean  # stripped header noise


# --------------------------------------------------------------------------- #
# 4. Persistence & Resumability Tests
# --------------------------------------------------------------------------- #
def test_persistence_atomic_save_and_load(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "company_state.json"
    state = create_initial_state("Test math", project_id="save-proj")
    state["code_files"] = {"test.py": "x = 1\n"}
    state["completed_tasks"] = ["architecture"]

    saved_path = save_company_state(state, log_file)
    assert saved_path.is_file()

    loaded = load_company_state(log_file)
    assert loaded is not None
    assert loaded["project_id"] == "save-proj"
    assert loaded["code_files"]["test.py"] == "x = 1\n"
    assert loaded["completed_tasks"] == ["architecture"]


def test_resumability_hydrates_and_skips_completed_tasks(tmp_path: Path) -> None:
    state = create_initial_state("Resume test", project_id="resume-proj")
    state["architecture_spec"] = "Specification..."
    state["completed_tasks"] = ["architecture"]
    state["current_phase"] = PHASE_ARCHITECT

    assert can_resume(state) is True
    assert determine_next_incomplete_phase(state) == PHASE_CODER

    hydrated = hydrate_resumed_state(state)
    # Resumes at CODER, skipping already validated ARCHITECT
    assert hydrated["current_phase"] == PHASE_CODER
    assert hydrated["completed_tasks"] == ["architecture"]
    assert any("Resuming execution" in entry["message"] for entry in hydrated["execution_logs"])


def test_completed_state_cannot_be_resumed() -> None:
    state = create_initial_state("Done test", project_id="done-proj")
    state["current_phase"] = PHASE_COMPLETED
    assert can_resume(state) is False


# --------------------------------------------------------------------------- #
# 5. Routing & Self-Correction Logic Tests
# --------------------------------------------------------------------------- #
def test_ceo_routing_transitions() -> None:
    state = create_initial_state("Route test")

    # 1. No architecture -> route to ARCHITECT
    s1 = ceo_router_node(state)
    assert s1["current_phase"] == PHASE_ARCHITECT
    assert route_after_ceo(s1) == "architect"

    # 2. Architecture complete -> route to CODER
    s1["architecture_spec"] = "Spec"
    s1["completed_tasks"] = ["architecture"]
    s2 = ceo_router_node(s1)
    assert s2["current_phase"] == PHASE_CODER
    assert route_after_ceo(s2) == "coder"

    # 3. Code complete -> route to DEVOPS
    s2["completed_tasks"] = ["architecture", "code"]
    s3 = ceo_router_node(s2)
    assert s3["current_phase"] == PHASE_DEVOPS
    assert route_after_ceo(s3) == "devops"

    # 4. DevOps complete -> route to QA
    s3["completed_tasks"] = ["architecture", "code", "devops"]
    s4 = ceo_router_node(s3)
    assert s4["current_phase"] == PHASE_QA
    assert route_after_ceo(s4) == "qa"

    # 5. All complete -> COMPLETED
    s4["completed_tasks"] = ["architecture", "code", "devops", "qa"]
    s5 = ceo_router_node(s4)
    assert s5["current_phase"] == PHASE_COMPLETED
    assert route_after_ceo(s5) == "__end__"


def test_qa_routing_self_correction_vs_decomposition() -> None:
    router = make_route_after_qa(max_retries=2)

    # Case 1: Tests pass -> END
    pass_state = create_initial_state("Pass")
    pass_state["current_phase"] = PHASE_COMPLETED
    pass_state["test_results"] = "PASSED: All tests succeeded."
    assert router(pass_state) == "__end__"

    # Case 2: Test fails, retry_count (1) < max_retries (2) ->
    # route back to CODER for self-correction
    fail_state = create_initial_state("Fail")
    fail_state["current_phase"] = PHASE_FAILED
    fail_state["test_results"] = "FAILED:\nAssertionError"
    fail_state["retry_count"] = 1
    assert router(fail_state) == "coder"

    # Case 3: Test fails, retry_count (2) >= max_retries (2) -> trigger task decomposer!
    exhausted_state = create_initial_state("Exhausted")
    exhausted_state["current_phase"] = PHASE_FAILED
    exhausted_state["test_results"] = "FAILED:\nAssertionError"
    exhausted_state["retry_count"] = 2
    assert router(exhausted_state) == "task_decomposer"


def test_task_decomposer_node_resets_retries_and_augments_spec() -> None:
    state = create_initial_state("Decompose me")
    state["architecture_spec"] = "Original Spec"
    state["retry_count"] = 3
    state["error_message"] = "Persistent complex error"

    decomposed = task_decomposer_node(state)
    assert decomposed["retry_count"] == 0
    assert decomposed["error_message"] is None
    assert decomposed["current_phase"] == PHASE_CODER
    assert "Task Decomposition Plan" in str(decomposed["architecture_spec"])


# --------------------------------------------------------------------------- #
# 6. End-to-End Workflow Execution & Self-Correction Tests
# --------------------------------------------------------------------------- #
def test_full_autonomous_workflow_execution(tmp_path: Path) -> None:
    project_dir = tmp_path / "test_project"
    checkpointer = JsonStateCheckpointer(log_path=tmp_path / "logs" / "state.json")

    compiled = compile_company_workflow(
        checkpointer=checkpointer,
        project_root_getter=lambda pid: project_dir,
    )

    initial = create_initial_state("solve: 1+1 arithmetic evaluator", project_id="e2e-test")
    final = compiled.invoke(initial, config={"configurable": {"thread_id": "e2e-test"}})

    assert final["current_phase"] == PHASE_COMPLETED
    assert "architecture" in final["completed_tasks"]
    assert "code" in final["completed_tasks"]
    assert "devops" in final["completed_tasks"]
    assert "qa" in final["completed_tasks"]
    assert "PASSED" in str(final["test_results"])
    assert (project_dir / "src" / "core_engine.py").is_file()
    assert (project_dir / "tests" / "test_core_engine.py").is_file()
    assert (tmp_path / "logs" / "state.json").is_file()


def test_autonomous_workflow_self_correction_on_initial_bug(tmp_path: Path) -> None:
    project_dir = tmp_path / "bug_project"
    checkpointer = JsonStateCheckpointer(log_path=tmp_path / "logs" / "bug_state.json")

    # Inject an intentional bug on attempt 0
    compiled = compile_company_workflow(
        checkpointer=checkpointer,
        project_root_getter=lambda pid: project_dir,
        simulate_initial_bug=True,
    )

    initial = create_initial_state("solve: 1+1 arithmetic evaluator", project_id="bug-test")
    final = compiled.invoke(initial, config={"configurable": {"thread_id": "bug-test"}})

    # Verifies self-correction: QA caught the bug, routed to coder, coder fixed it, and tests passed
    assert final["current_phase"] == PHASE_COMPLETED
    assert final["retry_count"] == 1
    assert any("self-correction" in entry["message"].lower() for entry in final["execution_logs"])
    assert "PASSED" in str(final["test_results"])


# --------------------------------------------------------------------------- #
# 7. OOP Refactored Orchestrator & Agent Worker Tests
# --------------------------------------------------------------------------- #
def test_oop_autonomous_startup_orchestrator(tmp_path: Path) -> None:
    from company_core.orchestrator import AutonomousStartupOrchestrator

    target_dir = tmp_path / "oop_project"
    state_file = tmp_path / "logs" / "oop_state.json"

    orchestrator = AutonomousStartupOrchestrator(
        checkpoint_path=state_file,
        project_root_resolver=lambda _: target_dir,
    )

    final_state = orchestrator.run(
        user_requirement="solve: 1+1 arithmetic evaluator",
        project_id="oop-test",
        reset_state=True,
    )

    assert final_state["current_phase"] == PHASE_COMPLETED
    assert "architecture" in final_state["completed_tasks"]
    assert "code" in final_state["completed_tasks"]
    assert "devops" in final_state["completed_tasks"]
    assert "qa" in final_state["completed_tasks"]
    assert (target_dir / "app" / "engine.py").is_file()
    assert (target_dir / "tests" / "test_engine.py").is_file()
    assert state_file.is_file()


def test_oop_agent_workers_individually(tmp_path: Path) -> None:
    from company_core.agents import (
        CeoOrchestratorAgent,
        DevOpsEngineerAgent,
        QualityAssuranceAgent,
        SoftwareArchitectAgent,
        SoftwareEngineerAgent,
        TaskDecomposerAgent,
    )

    target_dir = tmp_path / "worker_test"
    target_dir.mkdir(parents=True, exist_ok=True)

    state = create_initial_state("evaluate math")

    # 1. CEO Agent
    ceo = CeoOrchestratorAgent()
    state = ceo.execute(state)
    assert state["current_phase"] == PHASE_ARCHITECT

    # 2. Architect Agent
    architect = SoftwareArchitectAgent()
    state = architect.execute(state)
    assert state["architecture_spec"] is not None
    assert "architecture" in state["completed_tasks"]

    # 3. Engineer Agent
    engineer = SoftwareEngineerAgent(project_root_resolver=lambda _: target_dir)
    state = engineer.execute(state)
    assert "code" in state["completed_tasks"]
    assert len(state["code_files"]) > 0

    # 4. DevOps Agent
    devops = DevOpsEngineerAgent(project_root_resolver=lambda _: target_dir)
    state = devops.execute(state)
    assert "devops" in state["completed_tasks"]

    # 5. QA Agent
    qa = QualityAssuranceAgent(project_root_resolver=lambda _: target_dir)
    state = qa.execute(state)
    assert qa.evaluate_verdict(state) == "__end__"

    # 6. Task Decomposer Agent
    decomposer = TaskDecomposerAgent()
    fail_state = {**state, "retry_count": 3, "error_message": "Pytest AssertionError"}
    decomposed = decomposer.execute(fail_state)
    assert decomposed["retry_count"] == 0
    assert "Task Decomposition Plan" in str(decomposed["architecture_spec"])
