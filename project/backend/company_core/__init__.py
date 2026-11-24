"""DEPRECATED: Legacy Autonomous Startup Multi-Agent Core compatibility package.

Prefer ``app.domain.ports`` and ``app.infrastructure.orchestration`` for new code.
"""

from __future__ import annotations

__deprecated__ = True

from company_core.agents import (
    BaseCompanyAgent,
    CeoOrchestratorAgent,
    DevOpsEngineerAgent,
    QualityAssuranceAgent,
    SoftwareArchitectAgent,
    SoftwareEngineerAgent,
    TaskDecomposerAgent,
)
from company_core.config import ConfigManager, get_config
from company_core.graph import (
    compile_company_workflow,
    create_company_workflow,
)
from company_core.orchestrator import AutonomousStartupOrchestrator
from company_core.orchestrator_graph import (
    compile_orchestrator_workflow,
    create_orchestrator_workflow,
)
from company_core.persistence import (
    JsonStateCheckpointer,
    can_resume,
    hydrate_resumed_state,
    load_company_state,
    save_company_state,
)
from company_core.state import (
    PHASE_ARCHITECT,
    PHASE_CODER,
    PHASE_COMPLETED,
    PHASE_DECOMPOSE,
    PHASE_DEVOPS,
    PHASE_FAILED,
    PHASE_QA,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_WAITING_APPROVAL,
    CompanyState,
    GraphState,
    GraphStatus,
    append_execution_log,
    create_initial_graph_state,
    create_initial_state,
)
from company_core.subgraphs import (
    compile_architect_subgraph,
    compile_coder_qa_subgraph,
    create_architect_subgraph,
    create_coder_qa_subgraph,
)
from company_core.turn_orchestrator import (
    GraphTurnOrchestrator,
    generate_mermaid_workflow,
)

__all__ = [
    "PHASE_ARCHITECT",
    "PHASE_CODER",
    "PHASE_COMPLETED",
    "PHASE_DECOMPOSE",
    "PHASE_DEVOPS",
    "PHASE_FAILED",
    "PHASE_QA",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_PENDING",
    "STATUS_RUNNING",
    "STATUS_WAITING_APPROVAL",
    "AutonomousStartupOrchestrator",
    "BaseCompanyAgent",
    "CeoOrchestratorAgent",
    "CompanyState",
    "ConfigManager",
    "DevOpsEngineerAgent",
    "GraphState",
    "GraphStatus",
    "GraphTurnOrchestrator",
    "JsonStateCheckpointer",
    "QualityAssuranceAgent",
    "SoftwareArchitectAgent",
    "SoftwareEngineerAgent",
    "TaskDecomposerAgent",
    "append_execution_log",
    "can_resume",
    "compile_architect_subgraph",
    "compile_coder_qa_subgraph",
    "compile_company_workflow",
    "compile_orchestrator_workflow",
    "create_architect_subgraph",
    "create_coder_qa_subgraph",
    "create_company_workflow",
    "create_initial_graph_state",
    "create_initial_state",
    "create_orchestrator_workflow",
    "generate_mermaid_workflow",
    "get_config",
    "hydrate_resumed_state",
    "load_company_state",
    "save_company_state",
]
