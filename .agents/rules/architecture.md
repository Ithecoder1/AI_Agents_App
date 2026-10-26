# Architecture Rules: Hexagonal Domain Isolation

This workspace follows Hexagonal / Clean Architecture. Dependencies point strictly **inward only**.

## Dependency Hierarchy

```
vibe.api (Presentation / Web Composition Root)
    ↓
vibe.infrastructure (App adapters: DB, Config, Eventing, Workspace)
    ↓
vibe.application (Use-cases, Orchestration, Registry, Linter, Resilience)
    ↓
vibe.domain (Pure Models, Ports, State Machine, Error Taxonomy)
```

## Non-Negotiable Boundary Rules

1. **Pure Domain (`vibe.domain`)**:
   - Must ONLY import Python standard library modules.
   - Zero framework or third-party dependencies (no Pydantic, no SQLAlchemy, no FastAPI, no YAML, no httpx).
   - Domain models are frozen dataclasses.

2. **Application Layer (`vibe.application`)**:
   - May only depend on `vibe.domain`.
   - Must remain I/O-free and framework-free. No database queries, no HTTP requests, no direct file access.

3. **Provider Isolation (`vibe_providers.adapters.*`)**:
   - Provider packages MUST be mutually independent. A provider package must NEVER import from another provider package.
   - Provider code lives in its own package (`project/backend/providers/vibe_providers`) and must NEVER import `vibe.infrastructure`, `vibe.application` or `vibe.api`.
   - Provider packages may only import from `vibe.domain`, `vibe.domain.ports`, and `vibe_providers.kit`.
   - The app (`vibe`) must NEVER import `vibe_providers`; providers are resolved dynamically from `config/providers.yaml` (`adapter:`) or the `vibe.providers` entry-point group.

4. **Composition Root (`vibe.api`)**:
   - `vibe.api` is the ONLY place allowed to wire concrete infrastructure adapters to application services and domain ports.

These rules are mechanically enforced in CI via `import-linter`.
Run `project\backend\.venv\Scripts\lint-imports.exe --config project/backend/app/pyproject.toml` and the same with `project/backend/providers/pyproject.toml` to verify.
