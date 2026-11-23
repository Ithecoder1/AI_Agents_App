"""Contract and unit tests for orchestration persistence and local workspace adapters."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest
from app.domain.graph_state import (
    INTENT_COMPLEX_PIPELINE,
    PHASE_ARCHITECT,
    PHASE_CODER,
    PHASE_COMPLETED,
    STATUS_RUNNING,
)
from app.domain.ports import ICodeWorkspaceRunner, IOrchestratorCheckpointer, TestRunResult
from app.infrastructure.orchestration.adapters import (
    LocalCodeWorkspaceRunner,
    extract_clean_traceback,
    read_project_files,
    run_pytest_suite,
    summarize_code_for_context,
    write_project_files,
)
from app.infrastructure.orchestration.persistence import (
    JsonStateCheckpointer,
    can_resume,
    determine_next_incomplete_phase,
    hydrate_resumed_state,
    load_graph_state,
    save_graph_state,
)
from company_core import persistence as legacy_persistence
from company_core import tools as legacy_tools


def _rich_graph_state(project_id: str = "proj-rich") -> dict[str, Any]:
    return {
        "session_id": f"sess-{project_id}",
        "project_id": project_id,
        "user_requirement": "Build a calculator service",
        "current_node": "blueprint_generator_node",
        "messages": [
            {"role": "user", "content": "Build a calculator service"},
            {"role": "assistant", "content": "Working on architecture"},
        ],
        "active_tools": [{"name": "view_file", "category": "filesystem", "summary": "View file"}],
        "subgraph_results": {"triage": {"tier": 2, "intent": INTENT_COMPLEX_PIPELINE}},
        "execution_logs": [
            {
                "timestamp": "2026-11-23T10:00:00+00:00",
                "agent": "architect",
                "phase": PHASE_ARCHITECT,
                "message": "Spec generated",
            }
        ],
        "status": STATUS_RUNNING,
        "intent": INTENT_COMPLEX_PIPELINE,
        "execution_profile": {"intent": INTENT_COMPLEX_PIPELINE, "fast_path": False},
        "active_skills": ["architecture_planning", "code_verification"],
        "current_phase": PHASE_ARCHITECT,
        "architecture_spec": "# Architecture Spec",
        "code_files": {"app/engine.py": "def add(a: int, b: int) -> int:\n    return a + b\n"},
        "test_results": None,
        "completed_tasks": ["architecture"],
        "failed_tasks": [],
        "error_message": None,
        "retry_count": 0,
        "max_retries": 4,
        "auto_approve": False,
        "plan": [{"step": 1, "action": "architect_subgraph", "description": "Design"}],
        "task_type": "FULL_PIPELINE",
    }


def test_checkpointer_lossless_round_trip_and_hydration(tmp_path: Path) -> None:
    checkpoint_file = tmp_path / "logs" / "company_state.json"
    chk = JsonStateCheckpointer(log_path=checkpoint_file)
    assert isinstance(chk, IOrchestratorCheckpointer)

    original = _rich_graph_state("lossless-1")
    saved_path = chk.save(original)  # type: ignore[arg-type]
    assert saved_path == checkpoint_file
    assert saved_path.is_file()

    loaded = chk.load()
    assert loaded is not None
    assert loaded["session_id"] == "sess-lossless-1"
    assert loaded["current_node"] == "blueprint_generator_node"
    assert loaded["status"] == STATUS_RUNNING
    assert loaded["intent"] == INTENT_COMPLEX_PIPELINE
    assert loaded["execution_profile"] == {"intent": INTENT_COMPLEX_PIPELINE, "fast_path": False}
    assert loaded["active_skills"] == ["architecture_planning", "code_verification"]
    assert loaded["active_tools"] == [
        {"name": "view_file", "category": "filesystem", "summary": "View file"}
    ]
    assert loaded["subgraph_results"] == {"triage": {"tier": 2, "intent": INTENT_COMPLEX_PIPELINE}}
    assert loaded["plan"] == [{"step": 1, "action": "architect_subgraph", "description": "Design"}]
    assert loaded["max_retries"] == 4
    assert loaded["auto_approve"] is False
    assert loaded["task_type"] == "FULL_PIPELINE"
    assert loaded["messages"] == original["messages"]
    assert chk.can_resume(loaded) is True

    hydrated = chk.hydrate(loaded)
    assert hydrated["current_phase"] == PHASE_CODER
    assert hydrated["intent"] == INTENT_COMPLEX_PIPELINE
    assert hydrated["plan"] == loaded["plan"]
    assert hydrated["status"] == STATUS_RUNNING
    assert hydrated["execution_profile"] == loaded["execution_profile"]
    assert hydrated["active_skills"] == loaded["active_skills"]
    assert hydrated["max_retries"] == 4
    assert hydrated["auto_approve"] is False
    assert any("Resuming execution" in entry["message"] for entry in hydrated["execution_logs"])

    # Missing, malformed, and incomplete JSON files return None cleanly
    assert load_graph_state(tmp_path / "missing.json") is None
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not valid json", encoding="utf-8")
    assert load_graph_state(corrupt) is None
    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text('{"project_id": "x"}', encoding="utf-8")
    assert load_graph_state(incomplete) is None

    # Completed state is not resumable
    completed = {**loaded, "current_phase": PHASE_COMPLETED}
    assert can_resume(completed) is False  # type: ignore[arg-type]


async def test_json_checkpointer_put_and_nonblocking_aput(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint_file = tmp_path / "async_chk" / "company_state.json"
    chk = JsonStateCheckpointer(log_path=checkpoint_file)

    to_thread_calls: list[Any] = []
    real_to_thread = asyncio.to_thread

    async def _spy_to_thread(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        to_thread_calls.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr("asyncio.to_thread", _spy_to_thread)

    state = _rich_graph_state("async-1")
    config = {"configurable": {"thread_id": "async-1", "checkpoint_ns": ""}}
    checkpoint = {
        "v": 1,
        "id": "ckpt-1",
        "ts": "2026-11-23T10:00:00+00:00",
        "channel_values": state,
        "channel_versions": {"project_id": "1"},
        "versions_seen": {},
        "pending_sends": [],
    }
    await chk.aput(config, checkpoint, {"source": "loop", "step": 1, "parents": {}}, {})  # type: ignore[arg-type]

    assert len(to_thread_calls) == 1
    assert checkpoint_file.is_file()
    loaded = load_graph_state(checkpoint_file)
    assert loaded is not None
    assert loaded["project_id"] == "async-1"
    assert loaded["intent"] == INTENT_COMPLEX_PIPELINE

    # Verify synchronous put() also mirrors the snapshot
    sync_file = tmp_path / "sync_chk" / "company_state.json"
    sync_chk = JsonStateCheckpointer(log_path=sync_file)
    sync_chk.put(config, checkpoint, {"source": "loop", "step": 1, "parents": {}}, {})  # type: ignore[arg-type]
    assert sync_file.is_file()


def test_local_workspace_runner_containment_and_subprocess_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_dir = tmp_path / "workspaces"
    runner = LocalCodeWorkspaceRunner(base_dir=base_dir, default_timeout_s=12.0)
    assert isinstance(runner, ICodeWorkspaceRunner)
    assert runner.base_dir == base_dir.resolve()

    proj_dir = runner.resolve_project_dir("proj-alpha")
    assert proj_dir == (base_dir / "proj-alpha").resolve()
    assert proj_dir.is_dir()

    # Path traversal and absolute path guards
    for bad_id in ("", "   ", "../escape", "sub/../../escape", "/abs/path", "\\win\\abs"):
        with pytest.raises(ValueError):
            runner.resolve_project_dir(bad_id)

    for bad_rel in ("", "   ", "../outside.py", "a/../../outside.py", "/abs.py", "\\win\\abs.py"):
        with pytest.raises(ValueError):
            runner.write_files(proj_dir, {bad_rel: "evil = 1\n"})
    assert not (tmp_path / "outside.py").exists()
    assert not (base_dir / "outside.py").exists()

    # Contained write & read round-trip ignoring excluded caches
    written = runner.write_files(
        proj_dir,
        {
            "app/engine.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
            "tests/test_engine.py": "def test_ok():\n    assert True\n",
        },
    )
    assert len(written) == 2
    ignored_dir = proj_dir / "__pycache__"
    ignored_dir.mkdir(parents=True, exist_ok=True)
    (ignored_dir / "cached.py").write_text("x = 1\n", encoding="utf-8")

    files = runner.read_files(proj_dir)
    assert set(files.keys()) == {"app/engine.py", "tests/test_engine.py"}
    summary = runner.summarize_code(files)
    assert "app/engine.py" in summary

    # Subprocess passing, failing, timeout, and OSError handling
    def _pass_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="2 passed", stderr="")

    monkeypatch.setattr(subprocess, "run", _pass_run)
    pass_res = runner.run_tests(proj_dir)
    assert pass_res == TestRunResult(success=True, combined_output="2 passed", clean_traceback=None)

    def _fail_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="=== FAILURES ===\nAssertionError: 1 != 2",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fail_run)
    fail_res = runner.run_tests(proj_dir)
    assert fail_res.success is False
    assert fail_res.clean_traceback is not None
    assert "AssertionError" in fail_res.clean_traceback

    def _timeout_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["pytest"], timeout=5.0)

    monkeypatch.setattr(subprocess, "run", _timeout_run)
    timeout_res = runner.run_tests(proj_dir, timeout_s=5.0)
    assert timeout_res.success is False
    assert "timed out after 5.0s" in (timeout_res.clean_traceback or "")

    def _oserror_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise OSError("binary missing")

    monkeypatch.setattr(subprocess, "run", _oserror_run)
    err_res = runner.run_tests(proj_dir)
    assert err_res.success is False
    assert "Failed to run pytest: binary missing" in (err_res.clean_traceback or "")


def test_legacy_persistence_and_tools_shims_preserve_identity_parity() -> None:
    assert legacy_persistence.JsonStateCheckpointer is JsonStateCheckpointer
    assert legacy_persistence.save_company_state is save_graph_state
    assert legacy_persistence.load_company_state is load_graph_state
    assert legacy_persistence.can_resume is can_resume
    assert legacy_persistence.determine_next_incomplete_phase is determine_next_incomplete_phase
    assert legacy_persistence.hydrate_resumed_state is hydrate_resumed_state

    assert legacy_tools.LocalCodeWorkspaceRunner is LocalCodeWorkspaceRunner
    assert legacy_tools.write_project_files is write_project_files
    assert legacy_tools.read_project_files is read_project_files
    assert legacy_tools.summarize_code_for_context is summarize_code_for_context
    assert legacy_tools.extract_clean_traceback is extract_clean_traceback
    assert legacy_tools.run_pytest_suite is run_pytest_suite
