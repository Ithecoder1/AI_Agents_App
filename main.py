"""Autonomous Startup Orchestrator — CLI Entrypoint.

Orchestrates specialized agents (CEO, Architect, Coder, DevOps, QA, Task Decomposer)
using LangGraph, maintaining persistent local state in logs/company_state.json with
resume-on-failure capabilities.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure backend and workspace root are on sys.path
WORKSPACE_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = WORKSPACE_ROOT / "project" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from company_core.orchestrator import AutonomousStartupOrchestrator
from company_core.persistence import (
    can_resume,
    load_company_state,
)
from company_core.state import (
    PHASE_COMPLETED,
    CompanyState,
)


def run_orchestrator(
    user_requirement: str = "Implement a deterministic arithmetic calculator engine that evaluates 1+1 and basic addition expressions",
    project_id: str = "vibe-app",
    *,
    resume_if_possible: bool = True,
    reset_state: bool = False,
    simulate_initial_bug: bool = False,
    checkpoint_file: str = "logs/company_state.json",
    use_master_graph: bool = False,
) -> CompanyState:
    """Instantiate and run the AutonomousStartupOrchestrator engine."""
    checkpoint_path = WORKSPACE_ROOT / checkpoint_file

    if reset_state and checkpoint_path.is_file():
        checkpoint_path.unlink()
        print(f"[*] Reset previous checkpoint: {checkpoint_path}")

    # Inspect previous checkpoint for informative startup diagnostics
    persisted = load_company_state(checkpoint_path) if resume_if_possible else None
    if persisted and can_resume(persisted):
        print(f"\n[RESUME] Found existing run for project {persisted['project_id']!r}.")
        print(f"    Last Phase: {persisted.get('current_phase')}")
        print(f"    Completed Tasks: {persisted.get('completed_tasks')}")
        print(f"    Retries so far: {persisted.get('retry_count')}")
    else:
        print(
            f"\n[STARTUP] Initializing new autonomous run for project {project_id!r}..."
        )
        print(f"    Requirement: {user_requirement}")

    orchestrator = AutonomousStartupOrchestrator(
        checkpoint_path=checkpoint_path,
        simulate_initial_bug=simulate_initial_bug,
    )

    engine_label = (
        "LangGraph Master Graph & Sub-Graphs"
        if use_master_graph
        else "LangGraph Engine"
    )
    print("=" * 70)
    print(f"  ORCHESTRATION PIPELINE STARTING ({engine_label})")
    print("=" * 70)

    if use_master_graph:
        final_state = orchestrator.run_master_workflow(
            user_requirement=user_requirement,
            project_id=project_id,
            resume_if_possible=resume_if_possible,
            reset_state=reset_state,
        )
    else:
        final_state = orchestrator.run(
            user_requirement=user_requirement,
            project_id=project_id,
            resume_if_possible=resume_if_possible,
            reset_state=reset_state,
        )

    print("\n" + "=" * 70)
    print("  ORCHESTRATION PIPELINE FINISHED")
    print("=" * 70)
    print(f"  Final Phase:     {final_state.get('current_phase')}")
    print(f"  Completed Tasks: {final_state.get('completed_tasks')}")
    print(f"  Total Files:     {len(final_state.get('code_files') or {})}")
    print(f"  Retries Used:    {final_state.get('retry_count')}")
    print(f"  Checkpoint:      {checkpoint_path}")

    test_results = final_state.get("test_results")
    if test_results:
        status_line = test_results.splitlines()[0] if test_results else "No output"
        print(f"  Test Status:     {status_line}")

    if final_state.get("current_phase") == PHASE_COMPLETED:
        print(
            "\n[SUCCESS] Project built and verified successfully by Autonomous Startup!"
        )
    else:
        print(
            f"\n[WARNING] Project paused in phase: {final_state.get('current_phase')}"
        )
        if final_state.get("error_message"):
            print(f"   Diagnostic: {final_state.get('error_message')}")

    return final_state


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LangGraph Autonomous Startup Orchestrator"
    )
    parser.add_argument(
        "--requirement",
        "-r",
        default="Implement a deterministic arithmetic calculator engine that evaluates 1+1 and basic addition expressions",
        help="Target user requirement",
    )
    parser.add_argument(
        "--project-id",
        "-p",
        default="vibe-app",
        help="Project workspace ID",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=True,
        help="Resume from previous checkpoint if available",
    )
    parser.add_argument(
        "--no-resume",
        action="store_false",
        dest="resume",
        help="Do not resume; start fresh",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset and delete any existing checkpoint file before starting",
    )
    parser.add_argument(
        "--simulate-bug",
        action="store_true",
        help="Inject a deliberate bug on attempt 0 to demonstrate QA self-correction loop",
    )
    parser.add_argument(
        "--master-graph",
        action="store_true",
        default=False,
        help="Execute using deterministic LangGraph Master Graph with Sub-Graph delegation",
    )

    args = parser.parse_args()

    run_orchestrator(
        user_requirement=args.requirement,
        project_id=args.project_id,
        resume_if_possible=args.resume,
        reset_state=args.reset,
        simulate_initial_bug=args.simulate_bug,
        use_master_graph=args.master_graph,
    )


if __name__ == "__main__":
    main()
