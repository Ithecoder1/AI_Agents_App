"""Unit tests for Dynamic Tool Retrieval & Just-in-Time Tool Provisioning."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.application.tools import (
    SEARCH_AVAILABLE_TOOLS_NAME,
    DynamicToolRegistry,
    ToolRetriever,
)
from app.domain.errors import ValidationError
from app.domain.tools import ToolDescriptor, ToolRetrievalPolicy
from company_core.orchestrator import AutonomousStartupOrchestrator


# --------------------------------------------------------------------------- #
# 1. Domain Model Contracts
# --------------------------------------------------------------------------- #
def test_tool_descriptor_contract() -> None:
    desc = ToolDescriptor(
        name="view_file",
        category="filesystem",
        summary="View text files with slice bounds.",
        schema={"properties": {"path": {"type": "string"}}},
        requires_approval=False,
    )
    assert desc.name == "view_file"
    assert desc.category == "filesystem"
    assert desc.summary == "View text files with slice bounds."
    assert "path" in desc.schema.get("properties", {})
    assert not desc.requires_approval

    compact = desc.to_compact_summary()
    assert "schema" not in compact  # Ensures zero schema bloat in discovery
    assert compact["name"] == "view_file"
    assert compact["category"] == "filesystem"

    with pytest.raises(ValidationError):
        ToolDescriptor(name="", category="filesystem", summary="test")

    with pytest.raises(ValidationError):
        ToolDescriptor(name="tool", category="filesystem", summary="")


def test_tool_retrieval_policy_contract() -> None:
    # 0-tool agent policy
    p_zero = ToolRetrievalPolicy.tool_less()
    assert p_zero.is_tool_less
    assert not p_zero.enabled
    assert p_zero.max_active_tools == 0
    assert len(p_zero.base_tools) == 0

    # Dynamic JIT agent policy
    p_dyn = ToolRetrievalPolicy.lean_dynamic(
        base_tools=("search_available_tools",),
        max_active_tools=5,
    )
    assert not p_dyn.is_tool_less
    assert p_dyn.enabled
    assert p_dyn.max_active_tools == 5
    assert p_dyn.base_tools == ("search_available_tools",)

    with pytest.raises(ValidationError):
        ToolRetrievalPolicy(max_active_tools=-1)


# --------------------------------------------------------------------------- #
# 2. Dynamic Tool Registry
# --------------------------------------------------------------------------- #
def test_registry_indexing_and_search() -> None:
    t1 = ToolDescriptor("view_file", "filesystem", "View file contents with line slices")
    t2 = ToolDescriptor("write_to_file", "filesystem", "Write code or text to file")
    t3 = ToolDescriptor("run_command", "terminal", "Execute terminal command")
    t4 = ToolDescriptor("open_browser_url", "browser", "Open web URL in browser")

    registry = DynamicToolRegistry([t1, t2, t3, t4])
    assert registry.has_tool("view_file")
    assert not registry.has_tool("unknown_tool")

    # Keyword search
    results = registry.search("file")
    result_names = [d.name for d in results]
    assert "view_file" in result_names
    assert "write_to_file" in result_names
    assert "run_command" not in result_names

    # Category filtered search
    term_results = registry.search("execute", category="terminal")
    assert len(term_results) == 1
    assert term_results[0].name == "run_command"

    # Empty query with category filter
    fs_results = registry.search("", category="filesystem")
    assert len(fs_results) == 2


def test_registry_from_catalog_json() -> None:
    catalog_path = Path("config/antigravity_tools_catalog.json")
    if not catalog_path.is_file():
        catalog_path = Path("project/config/antigravity_tools_catalog.json")

    assert catalog_path.is_file()
    with open(catalog_path, encoding="utf-8") as f:
        data = json.load(f)

    registry = DynamicToolRegistry.from_catalog_dict(data)
    assert len(registry.all_names()) == 60
    assert "view_file" in registry.all_names()
    assert "run_command" in registry.all_names()
    assert "open_browser_url" in registry.all_names()

    # Verify categories
    categories = registry.categories()
    assert "filesystem" in categories
    assert "terminal" in categories
    assert "browser" in categories
    assert "agent_control" in categories


# --------------------------------------------------------------------------- #
# 3. ToolRetriever & search_available_tools Meta-tool
# --------------------------------------------------------------------------- #
def test_tool_retriever_lean_baseline() -> None:
    t1 = ToolDescriptor("view_file", "filesystem", "View file contents")
    t2 = ToolDescriptor("write_to_file", "filesystem", "Write file contents")
    t3 = ToolDescriptor("run_command", "terminal", "Run terminal command")
    registry = DynamicToolRegistry([t1, t2, t3])

    policy = ToolRetrievalPolicy.lean_dynamic(
        base_tools=("search_available_tools",),
        max_active_tools=3,
    )
    retriever = ToolRetriever(registry=registry, policy=policy)

    # Initial state: only search_available_tools is active (1 tool schema instead of full catalog)
    assert retriever.get_active_tool_names() == ("search_available_tools",)
    active_schemas = retriever.get_active_schemas()
    assert len(active_schemas) == 1
    assert active_schemas[0]["name"] == SEARCH_AVAILABLE_TOOLS_NAME


def test_search_available_tools_activation() -> None:
    t1 = ToolDescriptor(
        "view_file", "filesystem", "View file contents", schema={"name": "view_file"}
    )
    t2 = ToolDescriptor(
        "write_to_file", "filesystem", "Write to file", schema={"name": "write_to_file"}
    )
    t3 = ToolDescriptor("run_command", "terminal", "Run terminal", schema={"name": "run_command"})
    registry = DynamicToolRegistry([t1, t2, t3])

    policy = ToolRetrievalPolicy.lean_dynamic(
        base_tools=("search_available_tools",),
        max_active_tools=3,
    )
    retriever = ToolRetriever(registry=registry, policy=policy)

    # Invoke meta-tool
    summaries = retriever.search_available_tools("view and read files")
    assert len(summaries) >= 1
    assert summaries[0]["name"] == "view_file"

    # Schema is now active for next turn
    active_names = retriever.get_active_tool_names()
    assert "view_file" in active_names
    assert "search_available_tools" in active_names
    assert len(retriever.get_active_schemas()) == 2


def test_active_tool_budget_and_lru_eviction() -> None:
    tools = [
        ToolDescriptor(f"tool_{i}", "category", f"Summary {i}", schema={"name": f"tool_{i}"})
        for i in range(5)
    ]
    registry = DynamicToolRegistry(tools)

    policy = ToolRetrievalPolicy.lean_dynamic(
        base_tools=("search_available_tools",),
        max_active_tools=3,  # Ceiling of 3 tools
    )
    retriever = ToolRetriever(registry=registry, policy=policy)

    # Activate tool_0 and tool_1
    retriever.activate_tool("tool_0")
    retriever.activate_tool("tool_1")
    assert len(retriever.get_active_tools()) == 3  # search_available_tools, tool_0, tool_1

    # Advancing turn, using tool_1
    retriever.step_turn(used_tool_names=["tool_1"])

    # Activating tool_2 must evict least-recently used tool_0, preserving search_available_tools
    retriever.activate_tool("tool_2")
    active = retriever.get_active_tool_names()
    assert len(active) == 3
    assert "search_available_tools" in active
    assert "tool_1" in active
    assert "tool_2" in active
    assert "tool_0" not in active


def test_tool_pruning_after_idle_turns() -> None:
    t1 = ToolDescriptor("view_file", "filesystem", "View file contents")
    registry = DynamicToolRegistry([t1])

    policy = ToolRetrievalPolicy.lean_dynamic(
        base_tools=("search_available_tools",),
        max_active_tools=5,
    )
    retriever = ToolRetriever(registry=registry, policy=policy)
    retriever.activate_tool("view_file")
    assert "view_file" in retriever.get_active_tool_names()

    pruned_in_turns: list[str] = []
    # Run 3 idle turns where view_file is never used
    for _ in range(3):
        pruned_in_turns.extend(retriever.step_turn(used_tool_names=[]))

    # view_file should be pruned from active set to maintain token hygiene
    assert "view_file" in pruned_in_turns
    assert "view_file" not in retriever.get_active_tool_names()
    assert "search_available_tools" in retriever.get_active_tool_names()


# --------------------------------------------------------------------------- #
# 4. Agent Capability Triage (0-Tool vs Multi-Tool)
# --------------------------------------------------------------------------- #
def test_agent_capability_triage() -> None:
    orchestrator = AutonomousStartupOrchestrator()

    # 1. 0-Tool Agents (CEO, Architect, Task Decomposer)
    ceo_triage = orchestrator.triage_agent_capabilities("ceo")
    assert ceo_triage["mode"] == "text_completion"
    assert ceo_triage["tools_injected"] == 0
    assert not ceo_triage["dynamic_retrieval_active"]

    arch_triage = orchestrator.triage_agent_capabilities("architect")
    assert arch_triage["mode"] == "text_completion"
    assert arch_triage["tools_injected"] == 0

    decomposer_triage = orchestrator.triage_agent_capabilities("task_decomposer")
    assert decomposer_triage["mode"] == "text_completion"
    assert decomposer_triage["tools_injected"] == 0

    # 2. Multi-Tool Agent (Coder) with JIT Retrieval
    coder_triage = orchestrator.triage_agent_capabilities("coder")
    assert coder_triage["mode"] == "tool_calling"
    assert coder_triage["dynamic_retrieval_active"]
    assert coder_triage["tools_injected"] == 1  # Injects only search_available_tools
    assert coder_triage["tool_names"] == ("search_available_tools",)

    # 3. Direct QA Task Override: Even for Coder, bypasses tool injection completely
    direct_qa_triage = orchestrator.triage_agent_capabilities("coder", task_type="DIRECT_QA")
    assert direct_qa_triage["mode"] == "text_completion"
    assert direct_qa_triage["tools_injected"] == 0
    assert not direct_qa_triage["dynamic_retrieval_active"]
