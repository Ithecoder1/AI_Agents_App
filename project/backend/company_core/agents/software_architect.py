"""Software Architect Agent: Synthesizes system blueprints and directory hierarchy."""

from __future__ import annotations

from company_core.agents.base_agent import BaseCompanyAgent
from company_core.config import AgentPersonaConfig
from company_core.state import PHASE_ARCHITECT, PHASE_CODER, CompanyState


class SoftwareArchitectAgent(BaseCompanyAgent):
    """Chief Architect designing modular system architecture and component blueprints."""

    def __init__(self, config: AgentPersonaConfig | None = None) -> None:
        super().__init__(persona_name="architect", config=config)

    @property
    def phase_name(self) -> str:
        return PHASE_ARCHITECT

    def execute(self, state: CompanyState) -> CompanyState:
        """Produce system architecture blueprint from user requirement."""
        requirement = state["user_requirement"]
        architecture_spec = self.design_system_architecture(requirement)

        completed = list(state.get("completed_tasks") or [])
        if "architecture" not in completed:
            completed.append("architecture")

        logs = self.log_event(
            state,
            message=f"Architect: Blueprint synthesized for requirement {requirement[:60]!r}.",
            details={"spec_length": len(architecture_spec)},
        )

        return {
            **state,
            "architecture_spec": architecture_spec,
            "completed_tasks": completed,
            "current_phase": PHASE_CODER,
            "execution_logs": logs,
        }

    def design_system_architecture(self, requirement: str) -> str:
        """Generate structured architecture specification markdown."""
        return f"""# System Architecture Specification

## Project Overview
- **Requirement:** {requirement}
- **Pattern:** Modular Hexagonal Architecture with Pure Domain Models
- **Runtime:** Python 3.10+ Standard Library + Pytest Verification

## Directory Tree
```
├── app/
│   ├── __init__.py
│   ├── engine.py       # Core business logic & evaluator
│   └── models.py       # Domain data structures
├── tests/
│   ├── __init__.py
│   └── test_engine.py  # Automated unit test suite
├── requirements.txt    # Project dependencies
└── README.md           # Quickstart and execution guide
```

## Component Boundaries
1. **Engine Layer (`app.engine`)**: Pure evaluation logic with strict parameter validation.
2. **Domain Layer (`app.models`)**: Lightweight typed structures.
3. **Test Harness (`tests.test_engine`)**: Parameterized assertion suite.
"""
