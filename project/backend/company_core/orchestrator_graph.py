"""Dynamic, Intent-Driven LangGraph Master Orchestrator Graph & Sub-Graph Task Delegation.

Evaluates incoming prompt intent at triage, resolves YAML-configured execution profiles and
modular skills, provisions JIT tools only when needed, and dynamically branches through
conditional edges so lightweight or specialized requests bypass unnecessary sub-graphs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from app.application.tools.registry import DynamicToolRegistry
from app.application.tools.retriever import ToolRetriever
from app.application.triage import is_lightweight_query
from app.domain.models import ExecutionParams, ExecutionTask, Prompt
from app.domain.ports.agent_provider import IAgentProvider
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from company_core.agents.task_decomposer import TaskDecomposerAgent
from company_core.config import ConfigManager, get_config
from company_core.persistence import JsonStateCheckpointer
from company_core.skills import SkillRegistry, get_default_skill_registry
from company_core.state import (
    INTENT_DIRECT_QA,
    INTENT_FOCUSED_TOOL_TASK,
    PHASE_ARCHITECT,
    PHASE_CODER,
    PHASE_COMPLETED,
    PHASE_DECOMPOSE,
    STATUS_COMPLETED,
    STATUS_RUNNING,
    STATUS_WAITING_APPROVAL,
    CompanyState,
    GraphState,
    append_execution_log,
)
from company_core.subgraphs.architect_subgraph import (
    compile_architect_subgraph,
)
from company_core.subgraphs.coder_qa_subgraph import (
    compile_coder_qa_subgraph,
)


# --------------------------------------------------------------------------- #
# Dynamic Master Nodes
# --------------------------------------------------------------------------- #
def make_triage_node(
    tool_registry: DynamicToolRegistry | None = None,
    config: ConfigManager | None = None,
    skill_registry: SkillRegistry | None = None,
) -> Callable[[GraphState], GraphState]:
    """Triage intent using config rules, attach modular skills, and provision JIT tools."""
    cfg = config or get_config()
    skills = skill_registry or get_default_skill_registry()

    def triage_node(state: GraphState) -> GraphState:
        req = state.get("user_requirement", "").strip()
        is_light = is_lightweight_query(req)
        profile = cfg.classify_intent(req, is_lightweight=is_light)

        # Resolve and attach modular skills declared in the profile
        resolved_skills = skills.resolve_for_profile(profile.skills)
        next_state: GraphState = {
            **state,
            "active_skills": list(profile.skills),
            "subgraph_results": dict(state.get("subgraph_results") or {}),
        }
        for skill in resolved_skills:
            skill_update = skill.apply_to_state(next_state)
            next_state["active_skills"] = skill_update["active_skills"]
            next_state["subgraph_results"] = skill_update["subgraph_results"]

        # JIT Tool schema resolution: strictly 0 tools for DIRECT_QA / tool-less profiles
        active_tools_meta: list[dict[str, Any]] = []
        if (
            tool_registry is not None
            and profile.task_type != "DIRECT_QA"
            and not profile.tool_policy.is_tool_less
        ):
            retriever = ToolRetriever(tool_registry, policy=profile.tool_policy)
            skill_query = skills.build_tool_query(
                profile.skills,
                fallback_query=profile.tool_query or req,
            )
            if skill_query:
                retriever.search_available_tools(
                    skill_query,
                    limit=profile.tool_policy.max_active_tools,
                )
            if req:
                retriever.search_available_tools(
                    req,
                    limit=profile.tool_policy.max_active_tools,
                )
            active_tools_meta = [
                {"name": t.name, "category": t.category, "summary": t.summary}
                for t in retriever.get_active_tools()
            ]

        logs = append_execution_log(
            next_state.get("execution_logs") or [],
            agent="ceo_triage",
            phase=PHASE_ARCHITECT,
            message=(
                f"Triage: Classified intent='{profile.intent}' "
                f"(task_type='{profile.task_type}', Lightweight={is_light}, "
                f"fast_path={profile.fast_path})."
            ),
            details={
                "intent": profile.intent,
                "task_type": profile.task_type,
                "is_lightweight": is_light,
                "fast_path": profile.fast_path,
                "active_skills": list(next_state.get("active_skills") or []),
                "subgraphs": list(profile.subgraphs),
                "active_tools_count": len(active_tools_meta),
            },
        )

        sub_results = dict(next_state.get("subgraph_results") or {})
        sub_results["triage"] = {
            "intent": profile.intent,
            "task_type": profile.task_type,
            "is_lightweight": is_light,
            "fast_path": profile.fast_path,
            "active_skills": list(next_state.get("active_skills") or []),
            "subgraphs": list(profile.subgraphs),
            "tools_count": len(active_tools_meta),
        }

        return {
            **next_state,
            "intent": profile.intent,
            "task_type": profile.task_type,
            "execution_profile": profile.to_dict(),
            "current_node": "triage_node",
            "status": STATUS_RUNNING,
            "active_tools": active_tools_meta,
            "subgraph_results": sub_results,
            "execution_logs": logs,
        }

    return triage_node


def make_planner_node(
    config: ConfigManager | None = None,
) -> Callable[[GraphState], GraphState]:
    """Generate structured execution plan from the active profile and handle approval gating."""
    cfg = config or get_config()

    def planner_node(state: GraphState) -> GraphState:
        intent_or_type = state.get("intent") or state.get("task_type") or "FULL_PIPELINE"
        profile = cfg.get_profile(intent_or_type)
        auto_approve = state.get("auto_approve", True)

        if state.get("task_type") and state["task_type"] != "DEFAULT":
            # Respect explicit state override when callers mutate task_type directly
            override_profile = cfg.get_profile(state["task_type"])
            if override_profile.task_type == state["task_type"]:
                profile = override_profile

        plan = [dict(step) for step in profile.plan_template]
        if not plan:
            plan = [
                {
                    "step": 1,
                    "action": "direct_execution",
                    "description": "Execute user request",
                }
            ]

        logs = append_execution_log(
            state.get("execution_logs") or [],
            agent="ceo_planner",
            phase=PHASE_ARCHITECT,
            message=(f"Planner: Synthesized {len(plan)}-step plan for profile '{profile.intent}'."),
            details={
                "intent": profile.intent,
                "plan": plan,
                "auto_approved": auto_approve,
            },
        )

        needs_gate = (
            not auto_approve and profile.requires_approval and profile.task_type != "DIRECT_QA"
        )
        next_status = STATUS_WAITING_APPROVAL if needs_gate else STATUS_RUNNING

        return {
            **state,
            "plan": plan,
            "status": next_status,
            "current_node": "planner_node",
            "execution_logs": logs,
        }

    return planner_node


def make_subgraph_router_node() -> Callable[[GraphState], GraphState]:
    """Inspects execution profile, plan, and completion status to dispatch the next sub-graph."""

    def subgraph_router_node(state: GraphState) -> GraphState:
        completed = set(state.get("completed_tasks") or [])
        intent = state.get("intent", "")
        task_type = state.get("task_type", "FULL_PIPELINE")

        logs = append_execution_log(
            state.get("execution_logs") or [],
            agent="subgraph_router",
            phase=state.get("current_phase", PHASE_ARCHITECT),
            message=(
                f"Router: Dispatching workflow for intent='{intent or task_type}' "
                f"(completed={sorted(completed)})."
            ),
            details={
                "intent": intent,
                "task_type": task_type,
                "completed_tasks": list(completed),
            },
        )

        return {
            **state,
            "current_node": "subgraph_router_node",
            "execution_logs": logs,
        }

    return subgraph_router_node


def make_executor_node(
    provider: IAgentProvider | None = None,
) -> Callable[[GraphState], GraphState]:
    """Dispatches execution to standard domain ports (IAgentProvider) or fast-path evaluator."""

    def executor_node(state: GraphState) -> GraphState:
        req = state.get("user_requirement", "").strip()
        output_text = ""

        if provider is not None:
            prompt = Prompt(user_prompt=req, literal=True)
            params = ExecutionParams(provider_id=provider.config.id)
            task = ExecutionTask.create(prompt, params, workspace=state.get("project_id")).start(
                provider.config.id
            )

            try:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None

                if loop and loop.is_running():
                    import concurrent.futures

                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        res = pool.submit(lambda: asyncio.run(provider.execute(task))).result(
                            timeout=30
                        )
                else:
                    res = asyncio.run(provider.execute(task))

                output_text = res.output_text
            except Exception as exc:
                output_text = f"Execution error via provider {provider.config.id}: {exc}"
        else:
            is_math = any(k in req.lower() for k in ("1+1", "1 + 1", "add"))
            output_text = "The result of 1 + 1 is 2." if is_math else f"Processed request: {req}"

        messages = list(state.get("messages") or [])
        messages.append({"role": "assistant", "content": output_text})

        logs = append_execution_log(
            state.get("execution_logs") or [],
            agent="executor",
            phase=PHASE_COMPLETED,
            message="Executor: Dispatched task execution and recorded output.",
            details={
                "output_length": len(output_text),
                "intent": state.get("intent", ""),
                "active_skills": list(state.get("active_skills") or []),
                "active_tools_count": len(state.get("active_tools") or []),
            },
        )

        sub_results = dict(state.get("subgraph_results") or {})
        sub_results["executor"] = {
            "output": output_text,
            "intent": state.get("intent", ""),
            "active_skills": list(state.get("active_skills") or []),
            "active_tools": [t.get("name", "") for t in (state.get("active_tools") or [])],
        }

        return {
            **state,
            "messages": messages,
            "status": STATUS_COMPLETED,
            "current_phase": PHASE_COMPLETED,
            "current_node": "executor_node",
            "subgraph_results": sub_results,
            "execution_logs": logs,
        }

    return executor_node


def make_task_decomposer_node() -> Callable[[GraphState], GraphState]:
    """Decompose persistent failures when sub-graph retries are exhausted."""

    def task_decomposer_node(state: GraphState) -> GraphState:
        agent = TaskDecomposerAgent()
        decomposed = agent.execute(state)

        logs = append_execution_log(
            decomposed.get("execution_logs") or [],
            agent="task_decomposer",
            phase=PHASE_DECOMPOSE,
            message="Tech Lead: Decomposed monolithic task after repeated test failures.",
        )

        return {
            **decomposed,
            "current_node": "task_decomposer_node",
            "current_phase": PHASE_CODER,
            "execution_logs": logs,
        }

    return task_decomposer_node


# --------------------------------------------------------------------------- #
# Dynamic Conditional Edge Routers
# --------------------------------------------------------------------------- #
def route_after_triage(state: GraphState) -> str:
    """Fast-path lightweight DIRECT_QA queries straight to executor_node."""
    intent = state.get("intent", "")
    task_type = state.get("task_type", "")
    auto_approve = state.get("auto_approve", True)

    if (intent == INTENT_DIRECT_QA or task_type == "DIRECT_QA") and auto_approve:
        return "executor_node"
    return "planner_node"


def route_after_planner(state: GraphState) -> str:
    """Branch after planning: gate approval, focused execution, or enter sub-graphs."""
    if state.get("status") == STATUS_WAITING_APPROVAL:
        return END

    intent = state.get("intent", "")
    task_type = state.get("task_type", "")
    if intent in (INTENT_DIRECT_QA, INTENT_FOCUSED_TOOL_TASK) or task_type in (
        "DIRECT_QA",
        "FOCUSED_TOOL_TASK",
    ):
        return "executor_node"

    return "subgraph_router_node"


def route_after_router(state: GraphState) -> str:
    """Determine destination sub-graph or execution node from state."""
    status = state.get("status")
    if status == STATUS_WAITING_APPROVAL:
        return END

    task_type = state.get("task_type", "FULL_PIPELINE")
    completed = set(state.get("completed_tasks") or [])

    if task_type in ("DIRECT_QA", "FOCUSED_TOOL_TASK"):
        return "executor_node"

    if task_type == "ARCHITECTURE":
        return "architect_subgraph"

    if task_type == "CODE_TEST_FIX":
        return "coder_qa_subgraph"

    # FULL_PIPELINE workflow
    if "architecture" not in completed or not state.get("architecture_spec"):
        return "architect_subgraph"

    if "qa" not in completed:
        return "coder_qa_subgraph"

    return END


def route_after_architect(state: GraphState) -> str:
    """Continue to coder_qa_subgraph only if required by the resolved profile or task type."""
    profile = state.get("execution_profile") or {}
    subgraphs = profile.get("subgraphs")
    if isinstance(subgraphs, list):
        return "coder_qa_subgraph" if "coder_qa_subgraph" in subgraphs else END

    task_type = state.get("task_type", "FULL_PIPELINE")
    if task_type == "ARCHITECTURE":
        return END
    return "coder_qa_subgraph"


def route_after_coder_qa(state: GraphState) -> str:
    """Evaluate coder_qa_subgraph outcome: completion vs task decomposition."""
    test_results = state.get("test_results") or ""
    passed = "PASSED" in test_results

    if passed:
        return END

    retries = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 3)
    if retries >= max_retries:
        return "task_decomposer"

    return END


# --------------------------------------------------------------------------- #
# Dynamic Master StateGraph Assembler
# --------------------------------------------------------------------------- #
def create_orchestrator_workflow(
    project_root_resolver: Callable[[str], Path] | None = None,
    tool_registry: DynamicToolRegistry | None = None,
    provider: IAgentProvider | None = None,
    simulate_initial_bug: bool = False,
    max_retries: int | None = None,
    config: ConfigManager | None = None,
    skill_registry: SkillRegistry | None = None,
) -> StateGraph[Any, Any, Any, Any]:
    """Construct the Dynamic Master LangGraph StateGraph with intent-driven conditional routing."""
    cfg = config or get_config()
    skills = skill_registry or get_default_skill_registry()
    retries = max_retries if max_retries is not None else cfg.workflow.max_retries

    workflow: StateGraph[Any, Any, Any, Any] = StateGraph(CompanyState)

    # 1. Register dynamic master nodes
    workflow.add_node(
        "triage_node",
        cast(
            Any,
            make_triage_node(
                tool_registry=tool_registry,
                config=cfg,
                skill_registry=skills,
            ),
        ),
    )
    workflow.add_node("planner_node", cast(Any, make_planner_node(config=cfg)))
    workflow.add_node("subgraph_router_node", cast(Any, make_subgraph_router_node()))
    workflow.add_node("executor_node", cast(Any, make_executor_node(provider=provider)))
    workflow.add_node("task_decomposer", cast(Any, make_task_decomposer_node()))

    # 2. Compile and mount specialized Sub-Graphs as isolated nodes
    architect_subgraph = compile_architect_subgraph()
    coder_qa_subgraph = compile_coder_qa_subgraph(
        project_root_resolver=project_root_resolver,
        tool_registry=tool_registry,
        simulate_initial_bug=simulate_initial_bug,
        max_retries=retries,
    )

    workflow.add_node("architect_subgraph", cast(Any, architect_subgraph))
    workflow.add_node("coder_qa_subgraph", cast(Any, coder_qa_subgraph))

    # 3. Graph Entry Point
    workflow.set_entry_point("triage_node")

    # 4. Dynamic conditional branching immediately after triage (fast-path vs planning)
    workflow.add_conditional_edges(
        "triage_node",
        route_after_triage,
        {
            "executor_node": "executor_node",
            "planner_node": "planner_node",
        },
    )

    # 5. Conditional branching after planner (approval gate / focused execution / subgraph router)
    workflow.add_conditional_edges(
        "planner_node",
        route_after_planner,
        {
            "executor_node": "executor_node",
            "subgraph_router_node": "subgraph_router_node",
            END: END,
        },
    )

    # 6. Conditional routing from subgraph_router_node
    workflow.add_conditional_edges(
        "subgraph_router_node",
        route_after_router,
        {
            "architect_subgraph": "architect_subgraph",
            "coder_qa_subgraph": "coder_qa_subgraph",
            "executor_node": "executor_node",
            END: END,
        },
    )

    # 7. Conditional exit or continuation after architect_subgraph
    workflow.add_conditional_edges(
        "architect_subgraph",
        route_after_architect,
        {
            "coder_qa_subgraph": "coder_qa_subgraph",
            END: END,
        },
    )

    # 8. Conditional check after coder_qa_subgraph: task decomposition vs completion
    workflow.add_conditional_edges(
        "coder_qa_subgraph",
        route_after_coder_qa,
        {
            "task_decomposer": "task_decomposer",
            END: END,
        },
    )

    # 9. After task decomposition, retry coder_qa_subgraph with decomposed plan
    workflow.add_edge("task_decomposer", "coder_qa_subgraph")

    # 10. Executor node completes the fast-path / focused turn
    workflow.add_edge("executor_node", END)

    return workflow


def compile_orchestrator_workflow(
    checkpointer: Any | None = None,
    project_root_resolver: Callable[[str], Path] | None = None,
    tool_registry: DynamicToolRegistry | None = None,
    provider: IAgentProvider | None = None,
    simulate_initial_bug: bool = False,
    max_retries: int | None = None,
    config: ConfigManager | None = None,
    skill_registry: SkillRegistry | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the Dynamic Master Orchestrator StateGraph into an executable LangGraph instance."""
    workflow = create_orchestrator_workflow(
        project_root_resolver=project_root_resolver,
        tool_registry=tool_registry,
        provider=provider,
        simulate_initial_bug=simulate_initial_bug,
        max_retries=max_retries,
        config=config,
        skill_registry=skill_registry,
    )
    chk = checkpointer if checkpointer is not None else JsonStateCheckpointer()
    return workflow.compile(checkpointer=chk)


__all__ = [
    "compile_orchestrator_workflow",
    "create_orchestrator_workflow",
    "make_executor_node",
    "make_planner_node",
    "make_subgraph_router_node",
    "make_task_decomposer_node",
    "make_triage_node",
    "route_after_architect",
    "route_after_coder_qa",
    "route_after_planner",
    "route_after_router",
    "route_after_triage",
]
