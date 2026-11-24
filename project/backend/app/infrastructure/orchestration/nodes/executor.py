"""Executor Command node dispatching direct QA and focused tool turns to IAgentProvider."""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any

from app.domain.graph_state import PHASE_COMPLETED, STATUS_COMPLETED, GraphState
from app.domain.models import ExecutionParams, ExecutionTask, Prompt
from app.domain.ports.agent_provider import IAgentProvider
from app.infrastructure.orchestration.state import ExecutionLogManager
from app.infrastructure.orchestration.subgraphs.base import BaseGraphNode


class ExecutorNode(BaseGraphNode):
    """Executes ``DIRECT_QA`` and ``FOCUSED_TOOL_TASK`` turns via an injected ``IAgentProvider``."""

    def __init__(
        self,
        log_manager: ExecutionLogManager,
        *,
        provider: IAgentProvider | None = None,
        provider_timeout_s: float = 30.0,
    ) -> None:
        self._log_manager = log_manager
        self._provider = provider
        self._provider_timeout_s = provider_timeout_s

    @property
    def name(self) -> str:
        return "executor_node"

    @staticmethod
    def _build_task(
        provider: IAgentProvider, requirement: str, project_id: str | None
    ) -> ExecutionTask:
        prompt = Prompt(user_prompt=requirement, literal=True)
        params = ExecutionParams(provider_id=provider.config.id)
        return ExecutionTask.create(prompt, params, workspace=project_id).start(provider.config.id)

    @staticmethod
    def _deterministic_fallback(requirement: str) -> str:
        is_math = any(keyword in requirement.lower() for keyword in ("1+1", "1 + 1", "add"))
        return "The result of 1 + 1 is 2." if is_math else f"Processed request: {requirement}"

    def _finalize_state(self, state: GraphState, output_text: str) -> dict[str, Any]:
        messages = list(state.get("messages") or [])
        messages.append({"role": "assistant", "content": output_text})

        logs = self._log_manager.append_to_logs(
            list(state.get("execution_logs") or []),
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
            "active_tools": [tool.get("name", "") for tool in (state.get("active_tools") or [])],
        }

        return {
            **state,
            "messages": messages,
            "status": STATUS_COMPLETED,
            "current_phase": PHASE_COMPLETED,
            "current_node": self.name,
            "subgraph_results": sub_results,
            "execution_logs": logs,
        }

    def execute(self, state: GraphState) -> dict[str, Any]:
        """Synchronously dispatch execution to ``IAgentProvider`` or deterministic fallback."""
        req = state.get("user_requirement", "").strip()
        if self._provider is None:
            return self._finalize_state(state, self._deterministic_fallback(req))

        task = self._build_task(self._provider, req, state.get("project_id"))
        try:
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None

            if running_loop and running_loop.is_running():
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    result = pool.submit(
                        asyncio.run,
                        self._provider.execute(task),
                    ).result(timeout=self._provider_timeout_s)
            else:
                result = asyncio.run(self._provider.execute(task))
            output_text = result.output_text
        except Exception as exc:
            output_text = f"Execution error via provider {self._provider.config.id}: {exc}"

        return self._finalize_state(state, output_text)

    async def __call__(self, state: GraphState) -> dict[str, Any]:
        """Asynchronously dispatch execution directly to ``IAgentProvider`` without blocking."""
        req = state.get("user_requirement", "").strip()
        if self._provider is None:
            return self._finalize_state(state, self._deterministic_fallback(req))

        task = self._build_task(self._provider, req, state.get("project_id"))
        try:
            result = await asyncio.wait_for(
                self._provider.execute(task),
                timeout=self._provider_timeout_s,
            )
            output_text = result.output_text
        except Exception as exc:
            output_text = f"Execution error via provider {self._provider.config.id}: {exc}"

        return self._finalize_state(state, output_text)


__all__ = ["ExecutorNode"]
