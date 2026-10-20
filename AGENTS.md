# AGENTS.md — Contributor & Agent Guidelines

Welcome to **Vibe Forge**, a multi-provider vibe-coding web application and execution engine.

## 🏛️ Architecture & Extensibility Guarantee

This project follows **Hexagonal / Clean Architecture**. Adding a new agent provider or execution runner requires **zero modifications** to core domain, application logic, or UI layers.

1. **Pure Domain (`backend/vibe/domain`)**: Data models, state machine, typed errors, and ports. Stdlib only.
2. **Application (`backend/vibe/application`)**: `ProviderRegistry`, `PromptLinter`, `AppSettings`, execution coordination. No direct I/O.
3. **Infrastructure (`backend/vibe/infrastructure`)**:
   - `provider_kit/`: `BaseAgentRunner` template-method class.
   - `providers/<name>/`: Isolated adapters (OpenRouter, Antigravity, custom packages, Fake).
   - `config/`: YAML/env loader with failure isolation and `${VAR}` interpolation.
4. **Presentation (`backend/vibe/api` & `web`)**: FastAPI REST/WebSocket endpoints and React/Vite UI.

## 🛠️ Workspace Skills & Tools

The repository contains automated skills in `.agents/skills/`:
- **`provider-scaffolder-skill`**: Scaffold a new isolated provider package and contract test in seconds:
  ```powershell
  backend\.venv\Scripts\python.exe .agents\skills\provider-scaffolder-skill\scripts\scaffold_provider.py --name <provider_name>
  ```
- **`vibe-prompt-validator-skill`**: Lint prompt files and check quality/token economy:
  ```powershell
  backend\.venv\Scripts\python.exe .agents\skills\vibe-prompt-validator-skill\scripts\lint_prompt.py --prompt "Your prompt"
  ```
- **`execution-resilience-skill`**: Guide for fault tolerance, backoff, circuit breakers, and error mapping.

## 🧪 Verification Commands

Always run these quality gates before committing code changes:

```powershell
# 1. Test suite
backend\.venv\Scripts\python.exe -m pytest backend/tests

# 2. Architecture boundary checks (import-linter)
backend\.venv\Scripts\lint-imports.exe --config backend/pyproject.toml

# 3. Linter & format checks
backend\.venv\Scripts\ruff.exe check backend
backend\.venv\Scripts\ruff.exe format --check backend

# 4. Strict type checker
backend\.venv\Scripts\mypy.exe --config-file backend/pyproject.toml backend
```
