"""OOP unit and contract tests for orchestration persistence and local workspace adapters."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from app.domain.graph_state import (
    INTENT_ARCHITECT_PIPELINE,
    PHASE_ARCHITECT,
    PHASE_CODER,
    PHASE_COMPLETED,
    PHASE_DEVOPS,
    PHASE_FAILED,
    PHASE_QA,
    STATUS_COMPLETED,
    STATUS_RUNNING,
    GraphState,
    create_initial_graph_state,
)
from app.domain.ports import ICodeWorkspaceRunner, IOrchestratorCheckpointer, TestRunResult
from app.infrastructure.orchestration import (
    GraphStateHydrator,
    JsonStateCheckpointer,
    LocalCodeWorkspaceRunner,
    PytestSubprocessRunner,
    WorkspaceFileManager,
)
from company_core import persistence as legacy_persistence
from company_core import tools as legacy_tools


def _build_rich_state(project_id: str = "step2-oop") -> GraphState:
    state = create_initial_graph_state("Build REST API", project_id=project_id)
    state["intent"] = INTENT_ARCHITECT_PIPELINE
    state["status"] = STATUS_RUNNING
    state["current_phase"] = PHASE_CODER
    state["architecture_spec"] = "Hexagonal service layout"
    state["completed_tasks"] = ["architecture"]
    state["execution_profile"] = {"tier": "Tier3", "max_tokens": 4096}
    state["active_skills"] = ["architecture_planning", "code_verification"]
    state["plan"] = [{"step": 1, "owner": "Coder", "task": "Implement endpoint"}]
    state["code_files"] = {"src/app.py": "def run() -> int:\n    return 42\n"}
    return state


def test_graph_state_hydrator_roundtrip_and_invalid_inputs(tmp_path: Path) -> None:
    hydrator = GraphStateHydrator(default_workspace_dir=tmp_path)
    assert hydrator.default_log_path() == tmp_path / "company_state.json"

    state = _build_rich_state()
    saved_path = hydrator.save(state)
    assert saved_path == tmp_path / "company_state.json"
    assert saved_path.is_file()

    loaded = hydrator.load(saved_path)
    assert loaded is not None
    assert loaded["project_id"] == "step2-oop"
    assert loaded["intent"] == INTENT_ARCHITECT_PIPELINE
    assert loaded["status"] == STATUS_RUNNING
    assert loaded["execution_profile"] == {"tier": "Tier3", "max_tokens": 4096}
    assert loaded["active_skills"] == ["architecture_planning", "code_verification"]
    assert loaded["plan"] == [{"step": 1, "owner": "Coder", "task": "Implement endpoint"}]
    assert loaded["code_files"]["src/app.py"] == "def run() -> int:\n    return 42\n"

    assert hydrator.load(tmp_path / "missing.json") is None

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not-valid-json", encoding="utf-8")
    assert hydrator.load(corrupt) is None

    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(json.dumps({"project_id": "only-id"}), encoding="utf-8")
    assert hydrator.load(incomplete) is None


def test_graph_state_hydrator_phase_determination_and_resumption(tmp_path: Path) -> None:
    hydrator = GraphStateHydrator(default_workspace_dir=tmp_path)

    fresh = create_initial_graph_state("Resume me", project_id="res-1")
    assert hydrator.can_resume(None) is False
    assert hydrator.can_resume(fresh) is True
    assert hydrator.determine_next_incomplete_phase(fresh) == PHASE_ARCHITECT

    fresh["architecture_spec"] = "Spec ready"
    fresh["completed_tasks"] = ["architecture"]
    assert hydrator.determine_next_incomplete_phase(fresh) == PHASE_CODER

    hydrated = hydrator.hydrate(fresh)
    assert hydrated["current_phase"] == PHASE_CODER
    assert hydrated["status"] == STATUS_RUNNING
    assert any("Resuming execution" in entry["message"] for entry in hydrated["execution_logs"])

    fresh["completed_tasks"] = ["architecture", "code"]
    assert hydrator.determine_next_incomplete_phase(fresh) == PHASE_DEVOPS

    fresh["completed_tasks"] = ["architecture", "code", "devops"]
    assert hydrator.determine_next_incomplete_phase(fresh) == PHASE_QA

    fresh["completed_tasks"] = ["architecture", "code", "devops", "qa"]
    assert hydrator.determine_next_incomplete_phase(fresh) == PHASE_COMPLETED

    done_state = create_initial_graph_state("Done", project_id="done-1")
    done_state["current_phase"] = PHASE_COMPLETED
    done_state["status"] = STATUS_COMPLETED
    assert hydrator.can_resume(done_state) is False

    failed_state = create_initial_graph_state("Failed", project_id="fail-1")
    failed_state["current_phase"] = PHASE_FAILED
    assert hydrator.can_resume(failed_state) is False


@pytest.mark.asyncio
async def test_json_state_checkpointer_contract_and_async_offloading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hydrator = GraphStateHydrator(default_workspace_dir=tmp_path)
    checkpoint_file = tmp_path / "checkpoints" / "state.json"
    checkpointer = JsonStateCheckpointer(log_path=checkpoint_file, hydrator=hydrator)

    assert isinstance(checkpointer, IOrchestratorCheckpointer)
    assert checkpointer.hydrator is hydrator

    state = _build_rich_state("async-chk")
    checkpointer.save(state)
    loaded = checkpointer.load()
    assert loaded is not None
    assert loaded["project_id"] == "async-chk"
    assert checkpointer.can_resume(loaded) is True
    assert checkpointer.hydrate(loaded)["current_phase"] == PHASE_CODER

    to_thread_calls: list[str] = []
    real_to_thread = asyncio.to_thread

    async def _recording_to_thread(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        to_thread_calls.append(getattr(func, "__name__", repr(func)))
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _recording_to_thread)

    config: Any = {"configurable": {"thread_id": "t-1", "checkpoint_ns": ""}}
    checkpoint: Any = {
        "v": 1,
        "id": "ckpt-1",
        "ts": "2026-11-24T00:00:00Z",
        "channel_values": dict(state),
        "channel_versions": {},
        "versions_seen": {},
        "pending_sends": [],
    }
    metadata: Any = {"source": "input", "step": 1, "writes": {}}
    await checkpointer.aput(config, checkpoint, metadata, {})

    assert "_write_snapshot" in to_thread_calls
    reloaded = hydrator.load(checkpoint_file)
    assert reloaded is not None
    assert reloaded["intent"] == INTENT_ARCHITECT_PIPELINE


def test_workspace_file_manager_containment_and_summarization(tmp_path: Path) -> None:
    manager = WorkspaceFileManager(base_workspace_dir=tmp_path)
    project_dir = manager.resolve_project_dir("my app/v1")
    assert project_dir.parent == tmp_path.resolve()

    with pytest.raises(ValueError, match="Unsafe project_id"):
        manager.resolve_project_dir("../escape")

    with pytest.raises(ValueError, match="Path traversal blocked"):
        manager.write_files(project_dir, {"../../outside.py": "bad"})

    with pytest.raises(ValueError, match="Absolute or empty path"):
        manager.write_files(project_dir, {str(tmp_path / "abs.py"): "bad"})

    written = manager.write_files(
        project_dir,
        {
            "src/main.py": "# comment\ndef add(a: int, b: int) -> int:\n    return a + b\n",
            "README.md": "# Notes\n",
            "ignored.bin": "binary",
        },
    )
    assert set(written) == {"src/main.py", "README.md", "ignored.bin"}

    files = manager.read_files(project_dir, extensions=(".py", ".md"))
    assert set(files.keys()) == {"src/main.py", "README.md"}

    large_code = "\n".join(f"def f_{idx}():\n    return {idx}" for idx in range(40))
    summary = manager.summarize_code({"big.py": large_code}, max_lines_per_file=20)
    assert "File: big.py (80 lines)" in summary
    assert "collapsed" in summary
    assert manager.summarize_code({}) == "(no files)"


def test_pytest_subprocess_runner_execution_timeout_and_error_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = PytestSubprocessRunner(python_executable=sys.executable, default_timeout_s=15.0)
    file_manager = WorkspaceFileManager(base_workspace_dir=tmp_path)
    proj = file_manager.resolve_project_dir("calc")

    missing_res = runner.run(tmp_path / "nonexistent")
    assert missing_res.success is False
    assert "does not exist" in missing_res.combined_output

    file_manager.write_files(
        proj,
        {
            "calc.py": "def mul(a: int, b: int) -> int:\n    return a * b\n",
            "test_calc.py": "from calc import mul\n\ndef test_mul():\n    assert mul(3, 4) == 12\n",
        },
    )
    ok_res = runner.run(proj)
    assert isinstance(ok_res, TestRunResult)
    assert ok_res.success is True
    assert ok_res.clean_traceback is None

    file_manager.write_files(
        proj,
        {"test_calc.py": "from calc import mul\n\ndef test_mul():\n    assert mul(3, 4) == 999\n"},
    )
    fail_res = runner.run(proj)
    assert fail_res.success is False
    assert fail_res.clean_traceback is not None
    assert "999" in fail_res.clean_traceback

    def _raise_timeout(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=1.5)

    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    timeout_res = runner.run(proj, timeout_s=1.5)
    assert timeout_res.success is False
    assert "timed out" in timeout_res.combined_output.lower()
    assert timeout_res.clean_traceback is not None

    def _raise_os_error(*args: Any, **kwargs: Any) -> Any:
        raise OSError("spawn blocked")

    monkeypatch.setattr(subprocess, "run", _raise_os_error)
    err_res = runner.run(proj)
    assert err_res.success is False
    assert "spawn blocked" in err_res.combined_output


def test_local_code_workspace_runner_di_composition(tmp_path: Path) -> None:
    file_manager = WorkspaceFileManager(base_workspace_dir=tmp_path)
    pytest_runner = PytestSubprocessRunner(python_executable=sys.executable)
    runner = LocalCodeWorkspaceRunner(
        file_manager=file_manager,
        pytest_runner=pytest_runner,
    )

    assert isinstance(runner, ICodeWorkspaceRunner)
    assert runner.file_manager is file_manager
    assert runner.pytest_runner is pytest_runner
    assert runner.base_workspace_dir == tmp_path

    proj = runner.resolve_project_dir("di-proj")
    runner.write_files(
        proj,
        {"test_ok.py": "def test_truth():\n    assert True\n"},
    )
    read_back = runner.read_files(proj)
    assert "test_ok.py" in read_back
    assert "File: test_ok.py" in runner.summarize_code(read_back)
    test_result = runner.run_tests(proj)
    assert test_result.success is True


def test_legacy_company_core_shims_delegate_to_oop_services(tmp_path: Path) -> None:
    state = _build_rich_state("legacy-parity")
    checkpoint_file = tmp_path / "legacy_state.json"

    saved = legacy_persistence.save_company_state(state, checkpoint_file)
    assert saved == checkpoint_file
    assert legacy_persistence.load_company_state(checkpoint_file) is not None
    assert legacy_persistence.load_graph_state(checkpoint_file) is not None
    assert legacy_persistence.can_resume(state) is True
    assert legacy_persistence.determine_next_incomplete_phase(state) == PHASE_CODER

    resumed = legacy_persistence.hydrate_resumed_state(cast("GraphState", dict(state)))
    assert resumed["current_phase"] == PHASE_CODER

    workspace = tmp_path / "legacy_ws"
    written = legacy_tools.write_project_files(
        workspace,
        {"test_sample.py": "def test_sample():\n    assert 2 + 2 == 4\n"},
    )
    assert written == ["test_sample.py"]
    loaded_files = legacy_tools.read_project_files(workspace)
    assert "test_sample.py" in loaded_files
    assert "test_sample.py" in legacy_tools.summarize_code_for_context(loaded_files)

    passed, combined, clean_tb = legacy_tools.run_pytest_suite(workspace)
    assert passed is True
    assert "1 passed" in combined
    assert clean_tb == ""
