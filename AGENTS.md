# AGENTS.md — Contributor & Agent Guidelines

Welcome to **Vibe Forge**, a multi-provider vibe-coding web application and execution engine.

## 🏛️ Architecture & Extensibility Guarantee

This project follows **Hexagonal / Clean Architecture**. Adding a new agent provider or execution runner requires **zero modifications** to core domain, application logic, or UI layers.

1. **Pure Domain (`project/backend/app/domain`)**: Data models, state machine, typed errors, and ports. Stdlib only.
2. **Application (`project/backend/app/application`)**: `ProviderRegistry`, `PromptLinter`, `AppSettings`, execution coordination. No direct I/O.
3. **App Infrastructure (`project/backend/app/infrastructure`)**: `config/` (YAML/env loader with failure isolation and `${VAR}` interpolation), `workspace/`, persistence, eventing.
4. **LLM Providers (`project/backend/providers/providers`)** - a separate package, depends only on `app.domain`; the app never imports it:
   - `kit/`: `BaseAgentRunner` template-method class and HTTP streaming runner.
   - `adapters/<name>/`: Isolated adapters (OpenRouter, Gemini, Anthropic, Antigravity, custom packages, Fake).
5. **Presentation (`project/backend/app/api` & `project/web`)**: FastAPI REST/WebSocket endpoints and React/Vite UI.

## 🛠️ Workspace Skills & Tools

The repository contains automated skills in `.agents/skills/`:
- **`provider-scaffolder-skill`**: Scaffold a new isolated provider package and contract test in seconds:
  ```powershell
  project\backend\.venv\Scripts\python.exe .agents\skills\provider-scaffolder-skill\scripts\scaffold_provider.py --name <provider_name>
  ```
- **`vibe-prompt-validator-skill`**: Lint prompt files and check quality/token economy:
  ```powershell
  project\backend\.venv\Scripts\python.exe .agents\skills\vibe-prompt-validator-skill\scripts\lint_prompt.py --prompt "Your prompt"
  ```
- **`execution-resilience-skill`**: Guide for fault tolerance, backoff, circuit breakers, and error mapping.

## 🧪 Verification Commands

Always run these quality gates before committing code changes:

```powershell
# 1. Test suite
project\backend\.venv\Scripts\python.exe -m pytest project/tests

# 2. Architecture boundary checks (import-linter)
project\backend\.venv\Scripts\lint-imports.exe --config project/backend/app/pyproject.toml
project\backend\.venv\Scripts\lint-imports.exe --config project/backend/providers/pyproject.toml

# 3. Linter & format checks
project\backend\.venv\Scripts\ruff.exe check project
project\backend\.venv\Scripts\ruff.exe format --check project

# 4. Strict type checker
project\backend\.venv\Scripts\mypy.exe --config-file project/backend/app/pyproject.toml project/backend/app
project\backend\.venv\Scripts\mypy.exe --config-file project/backend/providers/pyproject.toml project/backend/providers/providers
```
