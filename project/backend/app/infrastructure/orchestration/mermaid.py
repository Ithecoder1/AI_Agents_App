"""Single source of truth for deterministic, intent-aware Mermaid workflow diagrams."""

from __future__ import annotations

from app.application.triage import CascadingTriageService
from app.domain.graph_state import (
    INTENT_ARCHITECTURE_DESIGN,
    INTENT_CODE_TEST_FIX,
    INTENT_DIRECT_QA,
    INTENT_FOCUSED_TOOL_TASK,
)
from app.domain.ports import IProfileClassifier
from app.infrastructure.orchestration.state import OrchestratorConfigLoader


class MermaidWorkflowVisualizer:
    """Renders intent-tailored Mermaid flowcharts implementing ``IMermaidWorkflowVisualizer``."""

    _STYLE_DEFS = (
        "    classDef nodeDone fill:#065f46,stroke:#10b981,stroke-width:1.5px,color:#fff;\n"
        "    classDef nodeWaiting fill:#7c2d12,stroke:#f97316,stroke-width:2px,color:#fff;\n"
        "    classDef nodeActive fill:#1e1b4b,stroke:#818cf8,stroke-width:2px,color:#fff;\n"
        "    classDef active fill:#6366f1,stroke:#818cf8,stroke-width:2px,color:#fff;\n"
    )

    def __init__(self, profile_classifier: IProfileClassifier | None = None) -> None:
        self._profile_classifier: IProfileClassifier = (
            profile_classifier
            if profile_classifier is not None
            else OrchestratorConfigLoader.default()
        )

    @staticmethod
    def _sanitize_provider(provider_name: str) -> str:
        return provider_name.replace("(", "").replace(")", "").strip() or "Agent"

    @staticmethod
    def _build_approval_edges(*, status: str, target_label: str) -> tuple[str, str]:
        if status == "WAITING_APPROVAL":
            branch = (
                '    Planner --> Approval{"⏳ Human Approval"}\n'
                f"    Approval -->|Approved| {target_label}\n"
                "    Approval -.->|Cancelled| Abort([🛑 Abort Run])\n"
            )
            return branch, "Approval nodeWaiting;"
        branch = f'    Planner --> Approval["✓ Approved"]\n    Approval --> {target_label}\n'
        return branch, "Approval nodeDone;"

    def generate(
        self,
        user_requirement: str,
        *,
        status: str = "WAITING_APPROVAL",
        provider_name: str = "Agent",
        intent: str | None = None,
        triage_service: CascadingTriageService | None = None,
    ) -> str:
        """Render a Mermaid diagram tailored to ``intent`` (or classified requirement)."""
        safe_provider = self._sanitize_provider(provider_name)
        resolved_intent = intent
        if not resolved_intent:
            profile, _decision = self._profile_classifier.classify_intent_with_decision(
                user_requirement,
                triage_service=triage_service,
            )
            resolved_intent = str(profile.intent)

        if resolved_intent == INTENT_DIRECT_QA:
            if status == "WAITING_APPROVAL":
                return (
                    "flowchart LR\n"
                    '    Start([User Query]) --> Triage["⚡ Intent Triage: DIRECT_QA"]\n'
                    '    Triage --> Approval{"⏳ Human Approval"}\n'
                    f"    Approval -->|Approved| Executor"
                    f'["⚡ {safe_provider} Fast-Path Executor"]\n'
                    "    Approval -.->|Cancelled| Abort([🛑 Abort Run])\n"
                    "    Executor --> Done([✅ Response Delivered])\n"
                    f"{self._STYLE_DEFS}"
                    "    class Start,Triage nodeDone;\n"
                    "    class Approval nodeWaiting;\n"
                    "    class Executor active;\n"
                )
            return (
                "flowchart LR\n"
                '    Start([User Query]) --> Triage["⚡ Intent Triage: DIRECT_QA"]\n'
                f'    Triage -->|Fast-Path: 0 Tools| Executor["⚡ {safe_provider} Executor"]\n'
                "    Executor --> Done([✅ Response Delivered])\n"
                f"{self._STYLE_DEFS}"
                "    class Start,Triage nodeDone;\n"
                "    class Executor active;\n"
            )

        if resolved_intent == INTENT_FOCUSED_TOOL_TASK:
            branch, approval_cls = self._build_approval_edges(
                status=status,
                target_label=f'Executor["🔧 {safe_provider} Focused Tool Executor"]',
            )
            return (
                "flowchart LR\n"
                '    Start([User Request]) --> Triage["⚡ Intent Triage and JIT Tools"]\n'
                '    Triage --> Planner["📋 Focused Planner"]\n'
                f"{branch}"
                "    Executor --> Done([✅ Analysis Delivered])\n"
                f"{self._STYLE_DEFS}"
                "    class Start,Triage,Planner nodeDone;\n"
                f"    class {approval_cls}\n"
                "    class Executor active;\n"
            )

        if resolved_intent == INTENT_ARCHITECTURE_DESIGN:
            branch, approval_cls = self._build_approval_edges(
                status=status,
                target_label='Router["🔀 Sub-Graph Router"]',
            )
            return (
                "flowchart TD\n"
                "    Start([User Requirement]) --> Triage"
                '["⚡ Intent Triage: ARCHITECTURE_DESIGN"]\n'
                '    Triage --> Planner["📋 Architecture Planner"]\n'
                f"{branch}"
                "    Router --> ArchitectSub\n\n"
                '    subgraph ArchitectSub ["🏛️ Architecture Sub-Graph"]\n'
                '        ReqParser["Requirements Parser"] --> Blueprint["Blueprint Generator"]\n'
                '        Blueprint --> Validator["Schema and Boundary Validator"]\n'
                "    end\n\n"
                "    ArchitectSub --> Done([✅ Blueprint Validated])\n\n"
                f"{self._STYLE_DEFS}"
                "    class Start,Triage,Planner nodeDone;\n"
                f"    class {approval_cls}\n"
            )

        if resolved_intent == INTENT_CODE_TEST_FIX:
            branch, approval_cls = self._build_approval_edges(
                status=status,
                target_label='Router["🔀 Sub-Graph Router"]',
            )
            return (
                "flowchart TD\n"
                '    Start([User Requirement]) --> Triage["⚡ Intent Triage: CODE_TEST_FIX"]\n'
                '    Triage --> Planner["📋 Code-Fix Planner"]\n'
                f"{branch}"
                "    Router --> CoderSub\n\n"
                f'    subgraph CoderSub ["🧪 Code-Test-Fix Sub-Graph ({safe_provider})"]\n'
                f'        Coder["💻 Code Synthesis ({safe_provider})"] --> '
                'DevOps["⚙️ DevOps PyTest"]\n'
                '        DevOps --> QA{"🔍 QA Evaluator"}\n'
                "        QA -.->|Tests Fail: Self-Correction Loop| Coder\n"
                "    end\n\n"
                '    QA -->|All Tests Passed| Delivery["🎯 Verification and Delivery"]\n'
                "    Delivery --> Done([✅ Verified Fix])\n\n"
                f"{self._STYLE_DEFS}"
                "    class Start,Triage,Planner nodeDone;\n"
                f"    class {approval_cls}\n"
            )

        branch, approval_cls = self._build_approval_edges(
            status=status,
            target_label='Router["🔀 Sub-Graph Router"]',
        )
        return (
            "flowchart TD\n"
            '    Start([User Requirement]) --> Triage["⚡ Triage and JIT Discovery"]\n'
            '    Triage --> Planner["📋 Structured Planner"]\n'
            f"{branch}"
            "    Router --> ArchitectSub\n\n"
            '    subgraph ArchitectSub ["🏛️ Architecture Sub-Graph"]\n'
            '        ReqParser["Requirements Parser"] --> Blueprint["Blueprint Generator"]\n'
            '        Blueprint --> Validator["Schema and Boundary Validator"]\n'
            "    end\n\n"
            "    ArchitectSub --> CoderSub\n\n"
            f'    subgraph CoderSub ["🧪 Code-Test-Fix Sub-Graph ({safe_provider})"]\n'
            f'        Coder["💻 Code Synthesis ({safe_provider})"] --> DevOps["⚙️ DevOps PyTest"]\n'
            '        DevOps --> QA{"🔍 QA Evaluator"}\n'
            "        QA -.->|Tests Fail: Self-Correction Loop| Coder\n"
            "    end\n\n"
            '    QA -->|All Tests Passed| Delivery["🎯 Verification and Delivery"]\n'
            "    Delivery --> Done([✅ Verified Solution])\n\n"
            f"{self._STYLE_DEFS}"
            "    class Start,Triage,Planner nodeDone;\n"
            f"    class {approval_cls}\n"
        )


__all__ = ["MermaidWorkflowVisualizer"]
