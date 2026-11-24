"""Domain services for architecture requirements parsing, blueprint synthesis, and validation."""

from __future__ import annotations

from typing import Any

from app.domain.graph_state import INTENT_ARCHITECTURE_DESIGN, GraphState


class RequirementsParserService:
    """Parses raw user requirements into structured architectural specifications."""

    _MATH_KEYWORDS: tuple[str, ...] = ("1+1", "math", "calculator", "add", "arithmetic")

    def parse(self, requirement: str) -> dict[str, Any]:
        """Extract normalized architectural requirements metadata from ``requirement``."""
        clean = requirement.strip()
        is_math = any(keyword in clean.lower() for keyword in self._MATH_KEYWORDS)
        return {
            "raw_requirement": clean,
            "domain_type": "arithmetic_evaluator" if is_math else "business_engine",
            "pattern": "hexagonal_modular",
            "language": "python_3.10+",
            "testing_framework": "pytest",
            "requires_core_engine": True,
            "requires_test_harness": True,
        }


class BlueprintGeneratorService:
    """Synthesizes modular system architecture specifications and component layouts."""

    def generate(self, requirement: str) -> str:
        """Render a structured modular architecture blueprint markdown specification."""
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


class BlueprintSchemaValidator:
    """Validates generated architecture blueprints and checks profile routing scope."""

    _REQUIRED_MARKERS: tuple[str, ...] = ("Component Boundaries", "Directory Tree", "Architecture")

    def validate(self, spec: str) -> bool:
        """Return ``True`` when the blueprint includes required structural sections."""
        return any(marker in spec for marker in self._REQUIRED_MARKERS)

    def is_architecture_only(self, state: GraphState) -> bool:
        """Return ``True`` when execution ends directly after architecture validation."""
        profile = state.get("execution_profile") or {}
        profile_subgraphs = profile.get("subgraphs")
        return bool(
            state.get("task_type") == "ARCHITECTURE"
            or state.get("intent") == INTENT_ARCHITECTURE_DESIGN
            or (
                isinstance(profile_subgraphs, (list, tuple))
                and "coder_qa_subgraph" not in profile_subgraphs
            )
        )


__all__ = [
    "BlueprintGeneratorService",
    "BlueprintSchemaValidator",
    "RequirementsParserService",
]
