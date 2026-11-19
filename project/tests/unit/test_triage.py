"""Unit tests for pre-flight triage, heuristics, and 3-tier cascading intent triage."""

from __future__ import annotations

import builtins
import json
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest
from app.application.triage import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_MODEL_NAME,
    DEFAULT_SEMANTIC_ROUTES,
    LOCKED_MICRO_LLM_SYSTEM_PROMPT,
    CallableMicroLLMClient,
    CascadingTriageService,
    HuggingFaceEncoder,
    LocalCpuSemanticRouter,
    Route,
    RouteLayer,
    Tier1HeuristicFilter,
    Tier3MicroLLMClassifier,
    TriageDecision,
    is_lightweight_query,
    should_load_workspace_context,
)
from app.application.triage.heuristics import LIGHTWEIGHT_SYSTEM_INSTRUCTIONS
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


# --------------------------------------------------------------------------- #
# Cascading 3-Tier Intent Triage Pipeline Tests
# --------------------------------------------------------------------------- #
def test_tier1_zero_token_heuristic_edge_cases() -> None:
    tier1 = Tier1HeuristicFilter()
    service = CascadingTriageService(
        tier1_filter=tier1,
        micro_llm_client=CallableMicroLLMClient(
            lambda _sys, _usr: pytest.fail("Tier 3 must not run for Tier 1 matches")
        ),
    )

    for empty_prompt in ("", "   ", "\n\t "):
        d = service.triage(empty_prompt)
        assert d.tier == 1
        assert d.intent == "DIRECT_QA"
        assert d.confidence == 1.0
        assert d.requires_workspace_manifest is False
        assert d.target_model_tier == "fast"

    for cmd in ("/help", "/clear", "/HELP", " /reset ", "/status"):
        d = service.triage(cmd)
        assert d.tier == 1
        assert d.intent == "DIRECT_QA"
        assert d.confidence == 1.0
        assert d.requires_workspace_manifest is False
        assert d.target_model_tier == "fast"
        assert d.matched_route is not None
        assert d.matched_route.startswith("tier1_command:")

    for greeting in ("hi", "Hello!", "Good morning!", "thanks!", "ping"):
        d = service.triage(greeting)
        assert d.tier == 1
        assert d.intent == "DIRECT_QA"
        assert d.confidence == 1.0

    for math_expr in ("1+1", "2 * 3", "solve: 2 + 2"):
        d = service.triage(math_expr)
        assert d.tier == 1
        assert d.intent == "DIRECT_QA"

    # Substantive prompts and ambiguous short non-greeting prompts must bypass Tier 1
    assert tier1.evaluate("How do I reverse a list in Python?") is None
    assert tier1.evaluate("clean up my codebase and run tests") is None
    assert tier1.evaluate("Build a new payment gateway module from scratch.") is None
    assert tier1.evaluate("do the thing") is None


def test_tier2_semantic_routing_accuracy_on_canonical_and_rephrased_queries() -> None:
    router = LocalCpuSemanticRouter(
        routes=DEFAULT_SEMANTIC_ROUTES,
        confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD,
    )
    service = CascadingTriageService(
        semantic_router=router,
        micro_llm_client=CallableMicroLLMClient(
            lambda _sys, _usr: pytest.fail("Tier 3 must not run for confident Tier 2 matches")
        ),
    )

    assert all(isinstance(r, Route) for r in DEFAULT_SEMANTIC_ROUTES)
    assert isinstance(router.route_layer, RouteLayer)
    assert isinstance(router.encoder, HuggingFaceEncoder)
    assert router.encoder.name == DEFAULT_MODEL_NAME
    assert router.encoder.device == "cpu"

    # 1. DIRECT_QA canonical & rephrased
    for qa_prompt in (
        "How do I reverse a list in Python?",
        "What is the difference between synchronous and asynchronous code?",
        "Explain what this function does.",
        "Explain how async await works under the hood",
        "How do I sort a Python dictionary?",
    ):
        d = service.triage(qa_prompt)
        assert d.tier == 2, f"Expected Tier 2 for {qa_prompt!r}, got tier={d.tier}"
        assert d.intent == "DIRECT_QA"
        assert d.confidence >= DEFAULT_CONFIDENCE_THRESHOLD
        assert d.requires_workspace_manifest is False
        assert d.target_model_tier == "fast"

    # 2. CODE_TEST_FIX canonical & rephrased (including required deliverable phrase)
    for fix_prompt in (
        "Inspect my code and refactor it.",
        "Fix the failing pytest in the auth module.",
        "Optimize this function and make the tests pass.",
        "clean up my codebase and run tests",
        "refactor my code and make unit tests pass",
    ):
        d = service.triage(fix_prompt)
        assert d.tier == 2, f"Expected Tier 2 for {fix_prompt!r}, got tier={d.tier}"
        assert d.intent == "CODE_TEST_FIX"
        assert d.confidence >= DEFAULT_CONFIDENCE_THRESHOLD
        assert d.requires_workspace_manifest is True
        assert d.target_model_tier == "reasoning"

    # 3. ARCHITECT_PIPELINE canonical & rephrased
    for arch_prompt in (
        "Build a new payment gateway module from scratch.",
        "Create a new database migration and REST endpoint for user profiles.",
        "Design and scaffold the service layer for notifications.",
        "Scaffold a new microservice with database migrations and REST API",
        "Build a new billing service from scratch",
    ):
        d = service.triage(arch_prompt)
        assert d.tier == 2, f"Expected Tier 2 for {arch_prompt!r}, got tier={d.tier}"
        assert d.intent == "ARCHITECT_PIPELINE"
        assert d.confidence >= DEFAULT_CONFIDENCE_THRESHOLD
        assert d.requires_workspace_manifest is True
        assert d.target_model_tier == "reasoning"


def test_semantic_router_enforces_cpu_device_and_eval_mode() -> None:
    with pytest.raises(ValueError, match="device='cpu'"):
        LocalCpuSemanticRouter(device="cuda")

    router = LocalCpuSemanticRouter(device="cpu")
    router.warmup()
    assert isinstance(router.encoder, HuggingFaceEncoder)
    assert router.encoder.device == "cpu"
    assert router.encoder._model.training is False


def test_tier3_micro_llm_fallback_context_hygiene_and_strict_json() -> None:
    captured_calls: list[tuple[str, str]] = []

    def mock_llm(system_prompt: str, user_prompt: str) -> str:
        captured_calls.append((system_prompt, user_prompt))
        return json.dumps(
            {
                "intent": "CODE_TEST_FIX",
                "confidence": 0.91,
                "requires_workspace_manifest": True,
                "target_model_tier": "reasoning",
                "token_budget": 1024,
            }
        )

    service = CascadingTriageService(
        micro_llm_client=CallableMicroLLMClient(mock_llm),
    )

    ambiguous_prompts = [
        "Can you check on this thing?",
        "What should we do next?",
        "Schedule a meeting for tomorrow at 3pm",
    ]
    for prompt_text in ambiguous_prompts:
        captured_calls.clear()
        scores = service.score_routes(prompt_text)
        assert max(scores.values()) < DEFAULT_CONFIDENCE_THRESHOLD

        decision = service.triage(prompt_text)
        assert decision.tier == 3
        assert decision.intent == "CODE_TEST_FIX"
        assert decision.confidence == 0.91
        assert decision.requires_workspace_manifest is True
        assert decision.target_model_tier == "reasoning"
        assert decision.token_budget == 1024

        # Context Hygiene: ONLY locked system prompt + raw user prompt (zero workspace context)
        assert len(captured_calls) == 1
        sent_system, sent_user = captured_calls[0]
        assert sent_system == LOCKED_MICRO_LLM_SYSTEM_PROMPT
        assert sent_user == prompt_text
        assert "Active Target Workspace Context" not in sent_user
        assert "README.md" not in sent_system

    # Strict JSON schema validation checks
    with pytest.raises(ValueError, match="Missing required"):
        TriageDecision.from_json('{"intent": "DIRECT_QA"}')
    with pytest.raises(ValueError, match="Unexpected extra"):
        TriageDecision.from_json(
            json.dumps(
                {
                    "intent": "DIRECT_QA",
                    "confidence": 0.9,
                    "requires_workspace_manifest": False,
                    "target_model_tier": "fast",
                    "token_budget": 512,
                    "extra_field": "disallowed",
                }
            )
        )
    with pytest.raises(ValueError, match="Invalid intent"):
        TriageDecision.from_json(
            json.dumps(
                {
                    "intent": "UNKNOWN_ROUTE",
                    "confidence": 0.9,
                    "requires_workspace_manifest": False,
                    "target_model_tier": "fast",
                    "token_budget": 512,
                }
            )
        )

    # Resilient fallback on malformed LLM output when fallback_on_error=True
    resilient = Tier3MicroLLMClassifier(
        client=CallableMicroLLMClient(lambda _s, _u: "not valid json {{"),
        fallback_on_error=True,
    )
    fallback_decision = resilient.classify("fix the auth timeout")
    assert fallback_decision.tier == 3
    assert fallback_decision.intent == "CODE_TEST_FIX"


def test_warm_bootstrapping_and_cpu_latency_benchmarks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = LocalCpuSemanticRouter(routes=DEFAULT_SEMANTIC_ROUTES)
    service = CascadingTriageService(semantic_router=router, auto_warmup=True)

    assert service.is_warmed is True
    assert router.encoder is not None

    encode_calls: list[int] = []
    orig_call = type(router.encoder).__call__

    def spy_call(self_enc: HuggingFaceEncoder, docs: list[str]) -> list[list[float]]:
        encode_calls.append(len(docs))
        return orig_call(self_enc, docs)

    monkeypatch.setattr(type(router.encoder), "__call__", spy_call)

    benchmark_prompts = [
        "/clear",
        "hello!",
        "How do I reverse a list in Python?",
        "clean up my codebase and run tests",
        "Build a new payment gateway module from scratch.",
    ]
    for prompt_text in benchmark_prompts:
        before_calls = len(encode_calls)
        t0 = time.perf_counter()
        decision = service.triage(prompt_text)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        assert decision.tier in (1, 2)
        assert elapsed_ms < 30.0, f"Latency {elapsed_ms:.2f}ms exceeded 30ms for {prompt_text!r}"
        assert decision.latency_ms < 30.0
        if decision.tier == 2:
            # Only the single incoming query was encoded (routes were pre-computed at warmup)
            assert encode_calls[before_calls:] == [1]


def test_zero_filesystem_io_during_triage(monkeypatch: pytest.MonkeyPatch) -> None:
    service = CascadingTriageService()

    def _forbid_io(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Filesystem I/O is forbidden during triage")

    monkeypatch.setattr(builtins, "open", _forbid_io)
    monkeypatch.setattr(Path, "read_text", _forbid_io)
    monkeypatch.setattr(Path, "read_bytes", _forbid_io)

    for prompt_text in (
        "/help",
        "clean up my codebase and run tests",
        "Design and scaffold the service layer for notifications.",
        "Can you check on this thing?",
    ):
        decision = service.triage(prompt_text)
        assert decision.intent in ("DIRECT_QA", "CODE_TEST_FIX", "ARCHITECT_PIPELINE")
