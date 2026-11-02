"""Configuration loader for Agent personas and Task pipeline rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
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


def _locate_config_file(filename: str) -> Path:
    candidates = [
        Path.cwd() / "config" / filename,
        Path(__file__).resolve().parents[1] / "config" / filename,
        Path(__file__).resolve().parents[1] / "project" / "config" / filename,
        Path(__file__).resolve().parent / "config" / filename,
    ]
    for path in candidates:
        if path.is_file():
            return path
    # Fallback to first candidate
    return candidates[0]


class ConfigManager:
    """Manages loaded agent definitions and workflow rules."""

    def __init__(
        self,
        agents_path: Path | str | None = None,
        tasks_path: Path | str | None = None,
    ) -> None:
        self._agents_path = Path(agents_path) if agents_path else _locate_config_file("agents.yaml")
        self._tasks_path = Path(tasks_path) if tasks_path else _locate_config_file("tasks.yaml")
        self._agents: dict[str, AgentConfig] = {}
        self._workflow: WorkflowConfig = WorkflowConfig()
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

    @property
    def workflow(self) -> WorkflowConfig:
        return self._workflow


_GLOBAL_CONFIG: ConfigManager | None = None


def get_config() -> ConfigManager:
    global _GLOBAL_CONFIG
    if _GLOBAL_CONFIG is None:
        _GLOBAL_CONFIG = ConfigManager()
    return _GLOBAL_CONFIG
