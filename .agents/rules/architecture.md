# Architecture Rules: Hexagonal Domain Isolation

This workspace follows Hexagonal / Clean Architecture. Dependencies point strictly **inward only**.

## Dependency Hierarchy

```
app.api (Presentation / Web Composition Root)
    ↓
app.infrastructure (App adapters: DB, Config, Eventing, Workspace)
    ↓
app.application (Use-cases, Orchestration, Registry, Linter, Resilience)
    ↓
app.domain (Pure Models, Ports, State Machine, Error Taxonomy)
```

## Non-Negotiable Boundary Rules

1. **Pure Domain (`app.domain`)**:
   - Must ONLY import Python standard library modules.
   - Zero framework or third-party dependencies (no Pydantic, no SQLAlchemy, no FastAPI, no YAML, no httpx).
   - Domain models are frozen dataclasses.

2. **Application Layer (`app.application`)**:
   - May only depend on `app.domain`.
   - Must remain I/O-free and framework-free. No database queries, no HTTP requests, no direct file access.

3. **Provider Isolation (`providers.adapters.*`)**:
   - Provider packages MUST be mutually independent. A provider package must NEVER import from another provider package.
   - Provider code lives in its own package (`project/backend/providers/providers`) and must NEVER import `app.infrastructure`, `app.application` or `app.api`.
   - Provider packages may only import from `app.domain`, `app.domain.ports`, and `providers.kit`.
   - The app (`vibe`) must NEVER import `providers`; providers are resolved dynamically from `config/providers.yaml` (`adapter:`) or the `app.providers` entry-point group.

4. **Composition Root (`app.api`)**:
   - `app.api` is the ONLY place allowed to wire concrete infrastructure adapters to application services and domain ports.

These rules are mechanically enforced in CI via `import-linter`.
Run `project\backend\.venv\Scripts\lint-imports.exe --config project/backend/app/pyproject.toml` and the same with `project/backend/providers/pyproject.toml` to verify.
