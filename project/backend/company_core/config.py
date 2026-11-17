"""Configuration loader for Agent personas, Task pipeline rules, and Orchestrator profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from app.domain.graph_state import (
    INTENT_ARCHITECTURE_DESIGN,
    INTENT_CODE_TEST_FIX,
    INTENT_COMPLEX_PIPELINE,
    INTENT_DIRECT_QA,
    INTENT_FOCUSED_TOOL_TASK,
)
from app.domain.tools import ToolRetrievalPolicy


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    include_full_code: bool = False
    include_summarized_code: bool = False
    include_architecture_summary: bool = True
    include_error_trace: bool = True


@dataclass(frozen=True, slots=True)
class AgentConfig:
    name: str
    role: str
    model: str
    temperature: float
    max_tokens: int
    system_prompt: str
    context_policy: ContextPolicy = field(default_factory=ContextPolicy)
    tools: tuple[str, ...] = ()
    tool_policy: ToolRetrievalPolicy = field(default_factory=ToolRetrievalPolicy.tool_less)

    @property
    def is_tool_less(self) -> bool:
        """Return True if agent has zero tools or tool retrieval is disabled."""
        return len(self.tools) == 0 or self.tool_policy.is_tool_less


AgentPersonaConfig = AgentConfig


@dataclass(frozen=True, slots=True)
class TokenEconomyConfig:
    max_prompt_lines_per_file: int = 50
    strip_comments_in_summary: bool = True
    exclude_test_files_from_coder_context: bool = False
    truncate_traceback_lines: int = 40


@dataclass(frozen=True, slots=True)
class WorkflowConfig:
    max_retries: int = 3
    checkpoint_path: str = "logs/company_state.json"
    project_root_template: str = "projects/{project_id}"
    token_economy: TokenEconomyConfig = field(default_factory=TokenEconomyConfig)


@dataclass(frozen=True, slots=True)
class IntentRoutingRule:
    """YAML-configured intent classification rule."""

    intent: str
    task_type: str
    match_lightweight: bool = False
    prefix_keywords: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OrchestratorProfileConfig:
    """Execution profile controlling sub-graphs, skills, and JIT tool provisioning."""

    intent: str
    task_type: str
    fast_path: bool = False
    requires_planner: bool = True
    requires_approval: bool = True
    subgraphs: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    tool_policy: ToolRetrievalPolicy = field(default_factory=ToolRetrievalPolicy.tool_less)
    tool_query: str = ""
    plan_template: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize execution profile into GraphState-friendly JSON structure."""
        return {
            "intent": self.intent,
            "task_type": self.task_type,
            "fast_path": self.fast_path,
            "requires_planner": self.requires_planner,
            "requires_approval": self.requires_approval,
            "subgraphs": list(self.subgraphs),
            "skills": list(self.skills),
            "tool_policy": {
                "enabled": self.tool_policy.enabled,
                "max_active_tools": self.tool_policy.max_active_tools,
                "tool_query": self.tool_query,
                "base_tools": list(self.tool_policy.base_tools),
            },
            "plan_template": [dict(step) for step in self.plan_template],
        }


_DEFAULT_ACTION_OVERRIDES = (
    "build",
    "create",
    "develop",
    "implement",
    "refactor",
    "patch",
    "fix",
    "solve",
    "generate",
    "write",
    "add feature",
)


def _default_routing_rules() -> dict[str, IntentRoutingRule]:
    return {
        INTENT_DIRECT_QA: IntentRoutingRule(
            intent=INTENT_DIRECT_QA,
            task_type="DIRECT_QA",
            match_lightweight=True,
            prefix_keywords=(
                "explain",
                "what is",
                "what are",
                "who is",
                "describe",
                "how does",
                "tell me",
                "show me",
                "overview of",
                "why ",
                "hi",
                "hello",
                "hey",
                "thanks",
                "thank you",
            ),
        ),
        INTENT_ARCHITECTURE_DESIGN: IntentRoutingRule(
            intent=INTENT_ARCHITECTURE_DESIGN,
            task_type="ARCHITECTURE",
            keywords=(
                "architect",
                "blueprint",
                "design spec",
                "system design",
                "schema design",
                "architecture diagram",
            ),
        ),
        INTENT_CODE_TEST_FIX: IntentRoutingRule(
            intent=INTENT_CODE_TEST_FIX,
            task_type="CODE_TEST_FIX",
            keywords=(
                "fix",
                "test",
                "patch",
                "refactor",
                "bug",
                "implement code",
                "unit test",
                "self-correction",
            ),
        ),
        INTENT_FOCUSED_TOOL_TASK: IntentRoutingRule(
            intent=INTENT_FOCUSED_TOOL_TASK,
            task_type="FOCUSED_TOOL_TASK",
            keywords=(
                "analyze",
                "inspect",
                "audit",
                "search",
                "lint",
                "find in files",
                "check file",
                "review code",
            ),
        ),
        INTENT_COMPLEX_PIPELINE: IntentRoutingRule(
            intent=INTENT_COMPLEX_PIPELINE,
            task_type="FULL_PIPELINE",
            keywords=(
                "build",
                "create",
                "develop",
                "solve",
                "application",
                "service",
                "pipeline",
                "full",
                "implement",
            ),
        ),
    }


def _default_orchestrator_profiles() -> dict[str, OrchestratorProfileConfig]:
    return {
        INTENT_DIRECT_QA: OrchestratorProfileConfig(
            intent=INTENT_DIRECT_QA,
            task_type="DIRECT_QA",
            fast_path=True,
            requires_planner=False,
            requires_approval=False,
            subgraphs=(),
            skills=("direct_qa",),
            tool_policy=ToolRetrievalPolicy.tool_less(),
            tool_query="",
            plan_template=(
                {
                    "step": 1,
                    "action": "direct_execution",
                    "description": "Answer user query directly via fast-path",
                },
            ),
        ),
        INTENT_FOCUSED_TOOL_TASK: OrchestratorProfileConfig(
            intent=INTENT_FOCUSED_TOOL_TASK,
            task_type="FOCUSED_TOOL_TASK",
            fast_path=False,
            requires_planner=True,
            requires_approval=False,
            subgraphs=(),
            skills=("code_analysis",),
            tool_policy=ToolRetrievalPolicy(
                enabled=True,
                max_active_tools=4,
                base_tools=("search_available_tools",),
            ),
            tool_query="file view search grep list",
            plan_template=(
                {
                    "step": 1,
                    "action": "focused_tool_execution",
                    "description": "Inspect workspace and execute focused tool analysis",
                },
            ),
        ),
        INTENT_ARCHITECTURE_DESIGN: OrchestratorProfileConfig(
            intent=INTENT_ARCHITECTURE_DESIGN,
            task_type="ARCHITECTURE",
            fast_path=False,
            requires_planner=True,
            requires_approval=True,
            subgraphs=("architect_subgraph",),
            skills=("architecture_planning",),
            tool_policy=ToolRetrievalPolicy(
                enabled=True,
                max_active_tools=3,
                base_tools=("search_available_tools",),
            ),
            tool_query="file view search architecture",
            plan_template=(
                {
                    "step": 1,
                    "action": "architect_subgraph",
                    "description": "Generate and validate architectural blueprint",
                },
            ),
        ),
        INTENT_CODE_TEST_FIX: OrchestratorProfileConfig(
            intent=INTENT_CODE_TEST_FIX,
            task_type="CODE_TEST_FIX",
            fast_path=False,
            requires_planner=True,
            requires_approval=True,
            subgraphs=("coder_qa_subgraph",),
            skills=("code_verification",),
            tool_policy=ToolRetrievalPolicy(
                enabled=True,
                max_active_tools=5,
                base_tools=("search_available_tools",),
            ),
            tool_query="file write view edit run command pytest",
            plan_template=(
                {
                    "step": 1,
                    "action": "coder_qa_subgraph",
                    "description": "Implement code and execute verification loop",
                },
            ),
        ),
        INTENT_COMPLEX_PIPELINE: OrchestratorProfileConfig(
            intent=INTENT_COMPLEX_PIPELINE,
            task_type="FULL_PIPELINE",
            fast_path=False,
            requires_planner=True,
            requires_approval=True,
            subgraphs=("architect_subgraph", "coder_qa_subgraph"),
            skills=("architecture_planning", "code_analysis", "code_verification"),
            tool_policy=ToolRetrievalPolicy(
                enabled=True,
                max_active_tools=5,
                base_tools=("search_available_tools",),
            ),
            tool_query="file write view edit search run command",
            plan_template=(
                {
                    "step": 1,
                    "action": "architect_subgraph",
                    "description": "Synthesize architecture blueprint",
                },
                {
                    "step": 2,
                    "action": "coder_qa_subgraph",
                    "description": "Implement source code and automated test harness",
                },
                {
                    "step": 3,
                    "action": "evaluator",
                    "description": "Verify test results and complete milestone",
                },
            ),
        ),
    }


def _locate_config_file(filename: str) -> Path:
    resolved_file = Path(__file__).resolve()
    candidates = [
        Path.cwd() / "config" / filename,
        Path.cwd() / "project" / "config" / filename,
        resolved_file.parents[1] / "config" / filename,
        resolved_file.parents[2] / "config" / filename,
        resolved_file.parents[3] / "config" / filename,
        resolved_file.parent / "config" / filename,
    ]
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0]


def _matches_prefix(text_lower: str, prefixes: tuple[str, ...]) -> bool:
    for prefix in prefixes:
        kw = prefix.strip().lower()
        if not kw:
            continue
        if text_lower == kw or text_lower.startswith((f"{kw} ", f"{kw},", f"{kw}!", f"{kw}?")):
            return True
    return False


def _matches_keywords(text_lower: str, keywords: tuple[str, ...]) -> bool:
    return any(kw.lower() in text_lower for kw in keywords if kw)


class ConfigManager:
    """Manages loaded agent definitions, workflow rules, and dynamic execution profiles."""

    def __init__(
        self,
        agents_path: Path | str | None = None,
        tasks_path: Path | str | None = None,
        profiles_path: Path | str | None = None,
    ) -> None:
        self._agents_path = Path(agents_path) if agents_path else _locate_config_file("agents.yaml")
        self._tasks_path = Path(tasks_path) if tasks_path else _locate_config_file("tasks.yaml")
        self._profiles_path = (
            Path(profiles_path)
            if profiles_path
            else _locate_config_file("orchestrator_profiles.yaml")
        )
        self._agents: dict[str, AgentConfig] = {}
        self._workflow: WorkflowConfig = WorkflowConfig()
        self._default_intent: str = INTENT_COMPLEX_PIPELINE
        self._routing_rules: dict[str, IntentRoutingRule] = _default_routing_rules()
        self._profiles: dict[str, OrchestratorProfileConfig] = _default_orchestrator_profiles()
        self.reload()

    def reload(self) -> None:
        if self._agents_path.is_file():
            with open(self._agents_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            raw_agents = data.get("agents", {})
            for key, val in raw_agents.items():
                policy_dict = val.get("context_policy", {})
                policy = ContextPolicy(
                    include_full_code=policy_dict.get("include_full_code", False),
                    include_summarized_code=policy_dict.get("include_summarized_code", False),
                    include_architecture_summary=policy_dict.get(
                        "include_architecture_summary", True
                    ),
                    include_error_trace=policy_dict.get("include_error_trace", True),
                )
                raw_tools = val.get("tools")
                tools_tuple = tuple(raw_tools) if isinstance(raw_tools, (list, tuple)) else ()
                raw_tp = val.get("tool_policy") or {}
                if raw_tp or tools_tuple:
                    is_enabled = bool(raw_tp.get("enabled", bool(tools_tuple)))
                    max_act = int(raw_tp.get("max_active_tools", 5 if tools_tuple else 0))
                    raw_base = raw_tp.get(
                        "base_tools",
                        ["search_available_tools"] if is_enabled else [],
                    )
                    base_tuple = tuple(raw_base) if isinstance(raw_base, (list, tuple)) else ()
                    t_policy = ToolRetrievalPolicy(
                        enabled=is_enabled,
                        max_active_tools=max_act,
                        base_tools=base_tuple,
                    )
                else:
                    t_policy = ToolRetrievalPolicy.tool_less()

                self._agents[key] = AgentConfig(
                    name=key,
                    role=val.get("role", key),
                    model=val.get("model", "gemini-2.5-flash"),
                    temperature=float(val.get("temperature", 0.2)),
                    max_tokens=int(val.get("max_tokens", 2048)),
                    system_prompt=val.get("system_prompt", "").strip(),
                    context_policy=policy,
                    tools=tools_tuple,
                    tool_policy=t_policy,
                )

        if self._tasks_path.is_file():
            with open(self._tasks_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            wf = data.get("workflow", {})
            te = data.get("token_economy", {})
            self._workflow = WorkflowConfig(
                max_retries=int(wf.get("max_retries", 3)),
                checkpoint_path=wf.get("checkpoint_path", "logs/company_state.json"),
                project_root_template=wf.get("project_root_template", "projects/{project_id}"),
                token_economy=TokenEconomyConfig(
                    max_prompt_lines_per_file=int(te.get("max_prompt_lines_per_file", 50)),
                    strip_comments_in_summary=bool(te.get("strip_comments_in_summary", True)),
                    exclude_test_files_from_coder_context=bool(
                        te.get("exclude_test_files_from_coder_context", False)
                    ),
                    truncate_traceback_lines=int(te.get("truncate_traceback_lines", 40)),
                ),
            )

        self._load_orchestrator_profiles()

    def _load_orchestrator_profiles(self) -> None:
        self._routing_rules = _default_routing_rules()
        self._profiles = _default_orchestrator_profiles()
        self._default_intent = INTENT_COMPLEX_PIPELINE

        if not self._profiles_path.is_file():
            return

        with open(self._profiles_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        default_intent = data.get("default_intent")
        if isinstance(default_intent, str) and default_intent.strip():
            self._default_intent = default_intent.strip()

        raw_rules = data.get("routing_rules")
        if isinstance(raw_rules, dict):
            parsed_rules: dict[str, IntentRoutingRule] = {}
            for intent_name, r_val in raw_rules.items():
                if not isinstance(r_val, dict):
                    continue
                parsed_rules[intent_name] = IntentRoutingRule(
                    intent=intent_name,
                    task_type=str(r_val.get("task_type", intent_name)),
                    match_lightweight=bool(r_val.get("match_lightweight", False)),
                    prefix_keywords=tuple(str(k) for k in (r_val.get("prefix_keywords") or ())),
                    keywords=tuple(str(k) for k in (r_val.get("keywords") or ())),
                )
            if parsed_rules:
                self._routing_rules = parsed_rules

        raw_profiles = data.get("profiles")
        if isinstance(raw_profiles, dict):
            parsed_profiles: dict[str, OrchestratorProfileConfig] = {}
            for intent_name, p_val in raw_profiles.items():
                if not isinstance(p_val, dict):
                    continue
                raw_tp = p_val.get("tool_policy") or {}
                enabled = bool(raw_tp.get("enabled", False))
                max_act = int(raw_tp.get("max_active_tools", 0))
                base_tools = tuple(str(t) for t in (raw_tp.get("base_tools") or ()))
                tool_query = str(raw_tp.get("tool_query", ""))
                t_policy = ToolRetrievalPolicy(
                    enabled=enabled,
                    max_active_tools=max_act,
                    base_tools=base_tools,
                )
                raw_plan = p_val.get("plan_template") or ()
                plan_steps = tuple(dict(s) for s in raw_plan if isinstance(s, dict))
                parsed_profiles[intent_name] = OrchestratorProfileConfig(
                    intent=intent_name,
                    task_type=str(p_val.get("task_type", intent_name)),
                    fast_path=bool(p_val.get("fast_path", False)),
                    requires_planner=bool(p_val.get("requires_planner", True)),
                    requires_approval=bool(p_val.get("requires_approval", True)),
                    subgraphs=tuple(str(sg) for sg in (p_val.get("subgraphs") or ())),
                    skills=tuple(str(sk) for sk in (p_val.get("skills") or ())),
                    tool_policy=t_policy,
                    tool_query=tool_query,
                    plan_template=plan_steps,
                )
            if parsed_profiles:
                self._profiles = parsed_profiles

    def get_agent(self, name: str) -> AgentConfig:
        if name in self._agents:
            return self._agents[name]
        return AgentConfig(
            name=name,
            role=name.capitalize(),
            model="gemini-2.5-flash",
            temperature=0.2,
            max_tokens=2048,
            system_prompt=f"You are the {name} agent for the autonomous startup.",
        )

    def get_profile(self, intent_or_task_type: str) -> OrchestratorProfileConfig:
        """Resolve an execution profile by intent name or legacy task_type alias."""
        key = intent_or_task_type.strip().upper()
        if key in self._profiles:
            return self._profiles[key]

        alias_map = {
            "DIRECT_QA": INTENT_DIRECT_QA,
            "FOCUSED_TOOL_TASK": INTENT_FOCUSED_TOOL_TASK,
            "ARCHITECTURE": INTENT_ARCHITECTURE_DESIGN,
            "ARCHITECTURE_DESIGN": INTENT_ARCHITECTURE_DESIGN,
            "CODE_TEST_FIX": INTENT_CODE_TEST_FIX,
            "FULL_PIPELINE": INTENT_COMPLEX_PIPELINE,
            "COMPLEX_PIPELINE": INTENT_COMPLEX_PIPELINE,
            "DEFAULT": self._default_intent,
        }
        mapped = alias_map.get(key)
        if mapped and mapped in self._profiles:
            return self._profiles[mapped]

        for profile in self._profiles.values():
            if profile.task_type.upper() == key:
                return profile

        return self._profiles.get(
            self._default_intent,
            _default_orchestrator_profiles()[INTENT_COMPLEX_PIPELINE],
        )

    def classify_intent(
        self,
        requirement: str,
        *,
        is_lightweight: bool = False,
    ) -> OrchestratorProfileConfig:
        """Classify a prompt into a configured OrchestratorProfileConfig."""
        cleaned = requirement.strip()
        req_lower = cleaned.lower()

        if is_lightweight:
            return self.get_profile(INTENT_DIRECT_QA)

        direct_rule = self._routing_rules.get(INTENT_DIRECT_QA)
        has_action_override = _matches_keywords(req_lower, _DEFAULT_ACTION_OVERRIDES)
        if (
            direct_rule
            and _matches_prefix(req_lower, direct_rule.prefix_keywords)
            and not has_action_override
        ):
            return self.get_profile(INTENT_DIRECT_QA)

        targeted_prefixes = (
            "fix",
            "patch",
            "bug",
            "refactor",
            "architect",
            "design",
            "analyze",
            "inspect",
            "audit",
            "lint",
            "search",
            "review",
        )
        full_build_markers = ("build", "create", "develop", "service", "application", "pipeline")
        if _matches_keywords(req_lower, full_build_markers) and not _matches_prefix(
            req_lower, targeted_prefixes
        ):
            return self.get_profile(INTENT_COMPLEX_PIPELINE)

        for intent_name in (
            INTENT_ARCHITECTURE_DESIGN,
            INTENT_CODE_TEST_FIX,
            INTENT_FOCUSED_TOOL_TASK,
        ):
            rule = self._routing_rules.get(intent_name)
            if rule and _matches_keywords(req_lower, rule.keywords):
                return self.get_profile(intent_name)

        complex_rule = self._routing_rules.get(INTENT_COMPLEX_PIPELINE)
        if complex_rule and _matches_keywords(req_lower, complex_rule.keywords):
            return self.get_profile(INTENT_COMPLEX_PIPELINE)

        return self.get_profile(self._default_intent)

    @property
    def routing_rules(self) -> dict[str, IntentRoutingRule]:
        return dict(self._routing_rules)

    @property
    def profiles(self) -> dict[str, OrchestratorProfileConfig]:
        return dict(self._profiles)

    @property
    def workflow(self) -> WorkflowConfig:
        return self._workflow


_GLOBAL_CONFIG: ConfigManager | None = None


def get_config() -> ConfigManager:
    global _GLOBAL_CONFIG
    if _GLOBAL_CONFIG is None:
        _GLOBAL_CONFIG = ConfigManager()
    return _GLOBAL_CONFIG
