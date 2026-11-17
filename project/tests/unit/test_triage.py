"""Unit tests for pre-flight triage and prompt heuristics."""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.application.triage import (
    LIGHTWEIGHT_SYSTEM_INSTRUCTIONS,
    is_lightweight_query,
    should_load_workspace_context,
)
from app.application.workspace_manager import WorkspaceManager


def test_is_lightweight_query_greetings() -> None:
    assert is_lightweight_query("hi")
    assert is_lightweight_query("Hi!")
    assert is_lightweight_query("hello")
    assert is_lightweight_query("HELLO THERE")
    assert is_lightweight_query("hey")
    assert is_lightweight_query("hey there!")
    assert is_lightweight_query("good morning")
    assert is_lightweight_query("good evening!")
    assert is_lightweight_query("howdy")
    assert is_lightweight_query("sup")
    assert is_lightweight_query("how are you?")
    assert is_lightweight_query("who are you")


def test_is_lightweight_query_math_and_short() -> None:
    assert is_lightweight_query("1+1")
    assert is_lightweight_query("1+1?")
    assert is_lightweight_query("1 + 1")
    assert is_lightweight_query("solve 1+1")
    assert is_lightweight_query("solve: 2 + 2")
    assert is_lightweight_query("2 * 3")
    assert not is_lightweight_query("solve: 1+1 arithmetic evaluator")
    assert LIGHTWEIGHT_SYSTEM_INSTRUCTIONS == ""
    assert is_lightweight_query("ping")
    assert is_lightweight_query("pong")
    assert is_lightweight_query("ok")
    assert is_lightweight_query("thanks!")
    assert is_lightweight_query("thank you")


def test_is_lightweight_query_rejects_code_and_files() -> None:
    assert not is_lightweight_query("fix bug")
    assert not is_lightweight_query("run pytest")
    assert not is_lightweight_query("cat a.py")
    assert not is_lightweight_query("src/main.ts")
    assert not is_lightweight_query("git diff")
    assert not is_lightweight_query("make test")
    assert not is_lightweight_query("refactor")
    assert not is_lightweight_query("what is this project?")
    assert not is_lightweight_query("explain system architecture")


def test_should_load_workspace_context() -> None:
    # Query is None -> backward compatibility returns True
    assert should_load_workspace_context(None)

    # Lightweight queries return False
    assert not should_load_workspace_context("hi")
    assert not should_load_workspace_context("1+1?")

    # Unrelated queries return False
    assert not should_load_workspace_context("Write a haiku about summer rain")

    # Architecture, code changes, and project queries return True
    assert should_load_workspace_context("Explain the system architecture")
    assert should_load_workspace_context("Refactor database connection pool")
    assert should_load_workspace_context("What is this project?")
    assert should_load_workspace_context("Show me the directory tree and files")

    # Explicit context file mentions return True
    assert should_load_workspace_context(
        "Can you check what is in README.md?", context_files=["README.md", "architecture.md"]
    )
    assert should_load_workspace_context(
        "Summarize architecture.md", context_files=["README.md", "architecture.md"]
    )


def test_workspace_manager_lazy_context_with_triage() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "README.md").write_text("# Project Guide\nGuide content.")
        (root / "architecture.md").write_text("# Architecture\nHexagonal design.")
        (root / "main.py").write_text("print('hello')")

        manager = WorkspaceManager(root)
        ws = manager.get_workspace("root")

        # 1. Query is greeting: no context files, no tree
        items_hi = manager.collect_context_items(ws, query="hi")
        assert len(items_hi) == 0

        summaries_hi = manager.load_context_summaries(ws, query="hi")
        assert len(summaries_hi) == 0

        enriched_hi = manager.enrich_system_instructions(ws, query="hi")
        assert enriched_hi == LIGHTWEIGHT_SYSTEM_INSTRUCTIONS

        # 2. Query is architectural / project query: loads context files and tree
        items_arch = manager.collect_context_items(ws, query="Explain system architecture")
        assert len(items_arch) > 0
        assert any(item.label == "architecture.md" for item in items_arch)
        assert any(item.kind == "directory_tree" for item in items_arch)

        # 3. Query is generic unrelated query: context files are suppressed
        items_unrelated = manager.collect_context_items(
            ws, query="Tell me a bedtime story about the stars"
        )
        assert len(items_unrelated) == 0
