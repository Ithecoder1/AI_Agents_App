# Architecture Rules: Hexagonal Domain Isolation

This workspace follows Hexagonal / Clean Architecture. Dependencies point strictly **inward only**.

## Dependency Hierarchy

```
app.api (Presentation / Web Composition Root)
    ↓
app.infrastructure (DB, Config, Eventing, Workspace, Orchestration Engine & Nodes)
    ↓
app.application (Use-cases, ChatTurnService, Registry, Triage, Linter, Resilience)
    ↓
app.domain (Pure Models, GraphState, Orchestration & Provider Ports, State Machine, Errors)
```

## Non-Negotiable Boundary Rules

1. **Pure Domain (`app.domain`)**:
   - Must ONLY import Python standard library modules.
   - Zero framework or third-party dependencies (no Pydantic, no SQLAlchemy, no FastAPI, no YAML, no httpx).
   - Owns `GraphState` (`graph_state.py`) and all orchestration contracts (`ports/orchestrator.py`: `ITurnOrchestrator`, `IOrchestratorEngine`, `IOrchestratorEngineFactory`, `IOrchestratorCheckpointer`, `ICodeWorkspaceRunner`, `IProfileClassifier`, `IMermaidWorkflowVisualizer`, `INodeEventMapper`, `TestRunResult`).

2. **Application Layer (`app.application`)**:
   - May only depend on `app.domain`.
   - Must remain I/O-free and framework-free. No database queries, no HTTP requests, no direct file access.
   - Forbidden from importing `company_core` or `app.infrastructure.orchestration`.

3. **Orchestration Infrastructure (`app.infrastructure.orchestration`)**:
   - Houses the concrete OOP LangGraph engine (`OrchestratorEngine`, `TurnOrchestratorService`), Command nodes (`nodes/`), sub-graph builders (`subgraphs/`), graph wiring/routing (`graph/`), Mermaid visualizer, event mapper, checkpointer (`persistence/`), and workspace runner (`adapters/`).
   - Never imports legacy `company_core` modules.

4. **Provider Isolation (`providers.adapters.*`)**:
   - Provider packages MUST be mutually independent. A provider package must NEVER import from another provider package.
   - Provider code lives in its own package (`project/backend/providers/providers` / `providers`) and must NEVER import `app.infrastructure`, `app.application`, `app.api`, or `company_core`.
   - Provider packages (including `StartupOrchestratorProvider`) may only import from `app.domain`, `app.domain.ports`, and `providers.kit`.
   - The app (`vibe`) must NEVER import `providers`; providers are resolved dynamically from `config/providers.yaml` (`adapter:`) or the `app.providers` entry-point group.

5. **Composition Root (`app.api` / `app/api/composition.py`)**:
   - `app.api` is the ONLY place allowed to wire concrete infrastructure adapters and orchestration services (`TurnOrchestratorService`, `OrchestratorEngineFactory`, `MermaidWorkflowVisualizer`, `StartupNodeEventMapper`) to application services (`ChatTurnService`) and `ProviderDeps`.

These rules are mechanically enforced in CI via `import-linter`.
Run with `$env:PYTHONPATH = "project/backend"`:
`project\backend\.venv\Scripts\lint-imports.exe --config project/backend/app/pyproject.toml` and `project\backend\.venv\Scripts\lint-imports.exe --config project/backend/providers/pyproject.toml` to verify.
