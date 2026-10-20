# Architecture Rules: Hexagonal Domain Isolation

This workspace follows Hexagonal / Clean Architecture. Dependencies point strictly **inward only**.

## Dependency Hierarchy

```
vibe.api (Presentation / Web Composition Root)
    ↓
vibe.infrastructure (Adapters: DB, Providers, Config, Eventing)
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

3. **Provider Isolation (`vibe.infrastructure.providers.*`)**:
   - Provider packages MUST be mutually independent. A provider package must NEVER import from another provider package.
   - Provider packages must NEVER import from other infrastructure adapters (`vibe.infrastructure.persistence`, `vibe.infrastructure.config`, `vibe.infrastructure.eventing`).
   - Provider packages may only import from `vibe.domain`, `vibe.domain.ports`, and `vibe.infrastructure.provider_kit`.

4. **Composition Root (`vibe.api`)**:
   - `vibe.api` is the ONLY place allowed to wire concrete infrastructure adapters to application services and domain ports.

These rules are mechanically enforced in CI via `import-linter`.
Run `backend\.venv\Scripts\lint-imports.exe` to verify.
