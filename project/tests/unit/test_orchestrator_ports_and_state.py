"""OOP unit and contract tests for orchestration domain ports and state services."""

from __future__ import annotations

import asyncio
import datetime
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast

import pytest
from app.application.triage import (
    DEFAULT_RESOURCE_ALLOCATION,
    VALID_TRIAGE_INTENTS,
    CallableMicroLLMClient,
    CascadingTriageService,
    Tier1HeuristicFilter,
    Tier3MicroLLMClassifier,
    TriageDecision,
    TriageIntent,
)
from app.domain.graph_state import (
    ALL_PHASES,
    INTENT_ARCHITECTURE_DESIGN,
    INTENT_CODE_TEST_FIX,
    INTENT_COMPLEX_PIPELINE,
    INTENT_DIRECT_QA,
    INTENT_FOCUSED_TOOL_TASK,
    PHASE_ARCHITECT,
    PHASE_CODER,
    PHASE_COMPLETED,
    PHASE_DECOMPOSE,
    PHASE_DEVOPS,
    PHASE_FAILED,
    PHASE_QA,
    STATUS_RUNNING,
    GraphState,
    create_initial_graph_state,
)
from app.domain.models import ExecutionTask, StreamEvent
from app.domain.ports import (
    ICodeWorkspaceRunner,
    IOrchestratorCheckpointer,
    IOrchestratorEngine,
    IProfileClassifier,
    ITurnOrchestrator,
    TestRunResult,
)
from app.domain.ports.agent_provider import IAgentProvider
from app.domain.tools import ToolDescriptor
from app.infrastructure.orchestration import (
    BaseOrchestratorSkill,
    CompanyStateFactory,
    ConfigManager,
    ExecutionLogEntry,
    ExecutionLogManager,
    OrchestratorConfigLoader,
    OrchestratorProfileConfig,
    OrchestratorSkill,
    PlanStep,
    SkillRegistry,
    SkillSpec,
    TriageProfileResolver,
    TriageSnapshot,
)
from company_core import config as legacy_config
from company_core import skills as legacy_skills
from company_core import state as legacy_state


class _StubSemanticRouter:
    def __init__(self, mapping: dict[str, TriageIntent]) -> None:
        self._mapping = mapping
        self._warmed = True

    @property
    def is_warmed(self) -> bool:
        return self._warmed

    def warmup(self) -> None:
        self._warmed = True

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
            matched_route=intent,
        )

    def _match(self, user_prompt: str) -> TriageIntent | None:
        lower = user_prompt.lower()
        for keyword, intent in self._mapping.items():
            if keyword in lower:
                return intent
        return None


def _stub_triage_service() -> CascadingTriageService:
    router = _StubSemanticRouter(
        {
            "explain": "DIRECT_QA",
            "analyze": "CODE_TEST_FIX",
            "architect": "ARCHITECT_PIPELINE",
            "fix": "CODE_TEST_FIX",
            "build": "ARCHITECT_PIPELINE",
        }
    )
    fallback = json.dumps(
        {
            "intent": "ARCHITECT_PIPELINE",
            "confidence": 0.9,
            "requires_workspace_manifest": True,
            "target_model_tier": "reasoning",
            "token_budget": 4096,
        }
    )
    return CascadingTriageService(
        tier1_filter=Tier1HeuristicFilter(),
        semantic_router=cast(Any, router),
        micro_llm=Tier3MicroLLMClassifier(client=CallableMicroLLMClient(lambda _s, _u: fallback)),
        auto_warmup=True,
    )


def test_domain_ports_and_rich_test_run_result_contract() -> None:
    assert ALL_PHASES == (
        PHASE_ARCHITECT,
        PHASE_CODER,
        PHASE_DEVOPS,
        PHASE_QA,
        PHASE_DECOMPOSE,
        PHASE_COMPLETED,
        PHASE_FAILED,
    )

    passed = TestRunResult.passed("2 passed in 0.01s\n")
    assert passed.success is True
    assert passed.clean_traceback is None
    assert passed.summary_line == "2 passed in 0.01s"
    assert passed.to_dict()["success"] is True

    failed = TestRunResult.failed("FAILED tests/test_x.py", "AssertionError: 1 != 2")
    assert failed.success is False
    assert failed.clean_traceback == "AssertionError: 1 != 2"

    timed_out = TestRunResult.timed_out(5.0)
    assert timed_out.success is False
    assert "5.0s" in (timed_out.clean_traceback or "")

    with pytest.raises(FrozenInstanceError):
        passed.success = False  # type: ignore[misc]

    class _TurnStub:
        async def stream_turn(
            self,
            task: ExecutionTask,
            provider: IAgentProvider,
            *,
            require_approval: bool = False,
            approval_event: asyncio.Event | None = None,
            cancel_event: asyncio.Event | None = None,
            active_tools: list[ToolDescriptor] | tuple[ToolDescriptor, ...] | None = None,
        ) -> AsyncIterator[StreamEvent]:
            _ = (task, provider, require_approval, approval_event, cancel_event, active_tools)
            if False:  # pragma: no cover
                yield cast(StreamEvent, None)

    class _EngineStub:
        def run_master_workflow(
            self,
            user_requirement: str,
            project_id: str = "vibe-app",
            provider: IAgentProvider | None = None,
            tool_registry: Any | None = None,
            resume_if_possible: bool = True,
            reset_state: bool = False,
        ) -> GraphState:
            _ = (provider, tool_registry, resume_if_possible, reset_state)
            return create_initial_graph_state(user_requirement, project_id=project_id)

        async def astream_nodes(
            self,
            user_requirement: str,
            project_id: str = "vibe-app",
            resume_if_possible: bool = True,
            reset_state: bool = False,
            use_master_graph: bool = True,
            tool_registry: Any | None = None,
        ) -> AsyncIterator[tuple[str, GraphState]]:
            _ = (
                user_requirement,
                project_id,
                resume_if_possible,
                reset_state,
                use_master_graph,
                tool_registry,
            )
            if False:  # pragma: no cover
                yield ("", create_initial_graph_state(""))

    class _CheckpointerStub:
        def save(self, state: GraphState, filepath: Path | str | None = None) -> Path:
            _ = (state, filepath)
            return Path("state.json")

        def load(self, filepath: Path | str | None = None) -> GraphState | None:
            _ = filepath
            return None

        def can_resume(self, state: GraphState | None) -> bool:
            _ = state
            return False

        def hydrate(self, state: GraphState) -> GraphState:
            return state

    class _WorkspaceStub:
        def resolve_project_dir(self, project_id: str) -> Path:
            return Path(project_id)

        def write_files(self, target_dir: Path, files: Mapping[str, str]) -> list[str]:
            _ = target_dir
            return list(files.keys())

        def read_files(
            self,
            target_dir: Path,
            extensions: tuple[str, ...] = (".py", ".json", ".yaml", ".yml", ".md", ".txt"),
        ) -> dict[str, str]:
            _ = (target_dir, extensions)
            return {}

        def summarize_code(
            self,
            files: Mapping[str, str],
            max_lines_per_file: int = 50,
            strip_comments: bool = True,
        ) -> str:
            _ = (files, max_lines_per_file, strip_comments)
            return ""

        def run_tests(self, target_dir: Path, timeout_s: float = 30.0) -> TestRunResult:
            _ = (target_dir, timeout_s)
            return TestRunResult.passed("ok")

    assert isinstance(_TurnStub(), ITurnOrchestrator)
    assert isinstance(_EngineStub(), IOrchestratorEngine)
    assert isinstance(_CheckpointerStub(), IOrchestratorCheckpointer)
    assert isinstance(_WorkspaceStub(), ICodeWorkspaceRunner)
    assert isinstance(OrchestratorConfigLoader(), IProfileClassifier)


def test_rich_payload_models_log_manager_and_state_factory() -> None:
    fixed_dt = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    log_manager = ExecutionLogManager(clock=lambda: fixed_dt)
    factory = CompanyStateFactory(log_manager=log_manager)

    entry = log_manager.create_entry(
        agent="triage",
        phase=PHASE_ARCHITECT,
        message="Classified",
        details={"tier": 1},
    )
    assert entry.to_dict() == {
        "timestamp": "2026-01-01T12:00:00+00:00",
        "agent": "triage",
        "phase": PHASE_ARCHITECT,
        "message": "Classified",
        "details": {"tier": 1},
    }
    hydrated_entry = ExecutionLogEntry.from_dict(entry.to_dict())
    assert hydrated_entry == entry

    with pytest.raises(ValueError, match="agent"):
        ExecutionLogEntry.create(agent="  ", phase=PHASE_ARCHITECT, message="Invalid")
    with pytest.raises(ValueError, match="phase"):
        ExecutionLogEntry.create(agent="triage", phase="", message="Invalid")

    step = PlanStep.from_dict({"step": 1, "action": "architect_subgraph", "description": "Design"})
    assert step.to_dict() == {
        "step": 1,
        "action": "architect_subgraph",
        "description": "Design",
    }
    with pytest.raises(ValueError, match="step"):
        PlanStep(step=0, action="x", description="y")

    loader = OrchestratorConfigLoader()
    decision = _stub_triage_service().triage("hi")
    snap = TriageSnapshot.from_classification(
        profile=loader.get_profile(INTENT_DIRECT_QA),
        decision=decision,
        active_skills=["direct_qa"],
        tools_count=0,
    )
    snap_dict = snap.to_dict()
    assert snap_dict["is_lightweight"] is True
    assert snap_dict["active_skills"] == ["direct_qa"]
    with pytest.raises(ValueError, match="confidence"):
        TriageSnapshot(
            intent=INTENT_DIRECT_QA,
            triage_intent="DIRECT_QA",
            confidence=1.5,
            tier=1,
            requires_workspace_manifest=False,
            target_model_tier="fast",
            token_budget=512,
            latency_ms=0.1,
            task_type="DIRECT_QA",
            is_lightweight=True,
            fast_path=True,
            active_skills=("direct_qa",),
            subgraphs=(),
            tools_count=0,
        )

    state = factory.create_initial_state("  Build service  ", project_id="p1")
    assert state["user_requirement"] == "Build service"
    assert state["current_phase"] == PHASE_ARCHITECT
    assert state["execution_logs"][0]["timestamp"] == "2026-01-01T12:00:00+00:00"

    original_len = len(state["execution_logs"])
    updated_logs = log_manager.append(
        state,
        node_name="triage_node",
        status=STATUS_RUNNING,
        details={"tier": 2},
    )
    assert len(state["execution_logs"]) == original_len
    assert len(updated_logs) == original_len + 1
    assert updated_logs[-1]["agent"] == "triage_node"
    assert updated_logs[-1]["details"] == {"status": STATUS_RUNNING, "tier": 2}


def test_config_loader_discovers_repo_yaml_from_any_cwd_and_custom_rules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    OrchestratorConfigLoader.reset_default()
    loader = OrchestratorConfigLoader.default()
    assert loader.workflow.max_retries >= 1
    assert loader.get_agent("coder").role == "Senior Software Engineer"
    assert loader.get_profile("ARCHITECTURE").intent == INTENT_ARCHITECTURE_DESIGN
    assert loader.get_profile("FULL_PIPELINE").intent == INTENT_COMPLEX_PIPELINE
    assert loader.get_profile("UNKNOWN_INTENT").intent == INTENT_COMPLEX_PIPELINE
    OrchestratorConfigLoader.reset_default()

    custom_profiles = tmp_path / "orchestrator_profiles.yaml"
    custom_profiles.write_text(
        "\n".join(
            [
                'default_intent: "COMPLEX_PIPELINE"',
                "routing_rules:",
                "  DIRECT_QA:",
                '    task_type: "DIRECT_QA"',
                '    prefix_keywords: ["explain"]',
                '    keywords: ["magic-qa-token"]',
                "profiles:",
                "  DIRECT_QA:",
                '    task_type: "DIRECT_QA"',
                "    fast_path: true",
                "    requires_planner: false",
                "    requires_approval: false",
                "    subgraphs: []",
                '    skills: ["direct_qa"]',
                "  COMPLEX_PIPELINE:",
                '    task_type: "FULL_PIPELINE"',
                "    fast_path: false",
            ]
        ),
        encoding="utf-8",
    )
    custom_loader = OrchestratorConfigLoader(profiles_path=custom_profiles)
    profile, _decision = custom_loader.classify_intent_with_decision(
        "Please trigger magic-qa-token now",
        triage_service=_stub_triage_service(),
    )
    assert profile.intent == INTENT_DIRECT_QA


def test_skill_registry_oop_contracts_and_triage_profile_resolver() -> None:
    SkillRegistry.reset_default()
    registry = SkillRegistry.default()
    assert len(registry.list_available()) == 4
    assert all(isinstance(skill, BaseOrchestratorSkill) for skill in registry.list_available())

    arch_matches = registry.find_for_intent(INTENT_ARCHITECTURE_DESIGN)
    assert [s.name for s in arch_matches] == ["architecture_planning"]
    assert "architecture" in registry.build_tool_query(["architecture_planning"])
    assert registry.build_tool_query(["unknown"], fallback_query=" grep ") == "grep"
    assert "Hexagonal/Clean Architecture" in registry.collect_directives(["architecture_planning"])

    custom_reg = SkillRegistry()
    custom_skill = OrchestratorSkill(
        SkillSpec(
            name="custom_skill",
            description="Custom capability",
            intent_tags=("DIRECT_QA",),
        )
    )
    assert custom_skill.can_handle("direct_qa") is True
    assert custom_skill.can_handle("CODE_TEST_FIX") is False

    custom_reg.register(custom_skill)
    assert custom_reg.get("custom_skill") is custom_skill
    assert custom_reg.list_available() == (custom_skill,)

    state = create_initial_graph_state("test")
    state_update = custom_skill.execute(state)
    assert state_update["active_skills"] == ["custom_skill"]
    assert "custom_skill" in state_update["subgraph_results"]["skills_applied"]

    loader = OrchestratorConfigLoader()
    svc = _stub_triage_service()
    resolver = TriageProfileResolver(loader, triage_service=svc, skill_registry=registry)

    p_qa, d_qa = resolver.classify_and_resolve("hi")
    assert p_qa.intent == INTENT_DIRECT_QA and d_qa.tier == 1
    assert resolver.refine_profile_skills(p_qa, INTENT_DIRECT_QA) == ("direct_qa",)

    p_focused, _ = resolver.classify_and_resolve("Analyze codebase structure")
    assert p_focused.intent == INTENT_FOCUSED_TOOL_TASK

    p_arch, _ = resolver.classify_and_resolve("Design system architecture blueprint")
    assert p_arch.intent == INTENT_ARCHITECTURE_DESIGN

    p_fix, _ = resolver.classify_and_resolve("Fix broken auth test")
    assert p_fix.intent == INTENT_CODE_TEST_FIX

    p_build, _ = resolver.classify_and_resolve("Build a microservice from scratch")
    assert p_build.intent == INTENT_COMPLEX_PIPELINE

    p_light, d_light = resolver.classify_and_resolve(
        "Build a microservice from scratch", is_lightweight=True
    )
    assert p_light.intent == INTENT_DIRECT_QA
    assert d_light.matched_route == "tier1_lightweight_override"

    # Unregistered skills on a synthetic profile fall back via refine_profile_skills
    synthetic = OrchestratorProfileConfig(
        intent=INTENT_CODE_TEST_FIX,
        task_type="CODE_TEST_FIX",
        skills=("nonexistent_skill",),
    )
    assert resolver.refine_profile_skills(synthetic, INTENT_CODE_TEST_FIX) == ("code_verification",)


def test_legacy_company_core_shims_delegate_to_oop_services() -> None:
    assert legacy_config.ConfigManager is ConfigManager
    assert legacy_config.OrchestratorProfileConfig is OrchestratorProfileConfig
    assert legacy_config.get_config == OrchestratorConfigLoader.default
    assert legacy_config.get_config() is OrchestratorConfigLoader.default()
    assert legacy_state.ExecutionLogManager is ExecutionLogManager
    assert legacy_state.CompanyStateFactory is CompanyStateFactory
    assert legacy_skills.SkillRegistry is SkillRegistry
    assert legacy_skills.BaseOrchestratorSkill is BaseOrchestratorSkill
    assert legacy_skills.get_default_skill_registry == SkillRegistry.default
    assert legacy_skills.get_default_skill_registry() is SkillRegistry.default()

    initial = legacy_state.create_initial_state("Test shim", project_id="shim-1")
    assert initial["project_id"] == "shim-1"
    logs = legacy_state.append_execution_log(
        initial["execution_logs"], agent="qa", phase=PHASE_QA, message="Verified"
    )
    assert len(logs) == 2
