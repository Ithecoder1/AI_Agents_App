# Vibe Forge — Multi-Provider Vibe-Coding Platform

A web application and execution engine designed for **Vibe Coding**. The platform allows users to author prompts and execute them against configurable AI agent infrastructures (OpenRouter, Antigravity headless agent runner, and custom third-party package runners) with real-time streaming, task status tracking, and artifact viewing.

---

## 🏛️ Architecture & Extensibility

Vibe Forge is built on **Hexagonal / Clean Architecture** principles. Adding a new agent provider or execution runner requires **zero modifications** to core business logic, API services, or UI layers.

```
                    ┌─────────────────────────┐
                    │ Presentation Layer      │
                    │ FastAPI + React/Vite UI │
                    └───────────┬─────────────┘
                                │
                                ▼
                    ┌─────────────────────────┐
                    │ Application Layer       │
                    │ Registry, Linter, Runner│
                    └───────────┬─────────────┘
                                │
                                ▼
                    ┌─────────────────────────┐
                    │ Domain Layer (Pure)     │
                    │ Models, Ports, FSM      │
                    └─────────────────────────┘
                                ▲
                                │ implements
                    ┌───────────┴─────────────┐
                    │ Infrastructure Adapters │
                    │ OpenRouter, Antigravity │
                    │ Package Runner, Fake    │
                    └─────────────────────────┘
```

---

## 🚀 How to Run the Project

### 1. Prerequisites
- **Python 3.10+** (virtual environment located in `backend\.venv`)
- **Node.js v20+** and **npm** (for the Phase 4 web layer)

---

### 2. Run the Interactive Execution Engine

Boot the configuration, resolve active providers, and stream an execution directly in your terminal:

```powershell
# Default demo prompt
backend\.venv\Scripts\python.exe scripts\demo_run.py

# Custom vibe prompt
backend\.venv\Scripts\python.exe scripts\demo_run.py "Refactor database connection pool with automatic retry backoff"
```

You will see real-time streaming:
- Status transitions (`QUEUED` -> `RUNNING` -> `SUCCEEDED`)
- Agent thoughts
- Token streaming output
- Artifact generation (e.g. unified diffs)
- Execution metrics & token counts

---

### 3. Inspect & Lint Prompts

Run the deterministic prompt validator against any prompt text or file to score it (0–100) and check for Chain-of-Thought, guardrails, schema contracts, and token efficiency:

```powershell
backend\.venv\Scripts\python.exe .agents\skills\vibe-prompt-validator-skill\scripts\lint_prompt.py --prompt "Build a calculator"
```

---

### 4. Scaffold a New Provider Adapter

Add an isolated provider package and contract test in seconds without touching core code:

```powershell
backend\.venv\Scripts\python.exe .agents\skills\provider-scaffolder-skill\scripts\scaffold_provider.py --name my_agent
```

---

### 5. Run Verification & Quality Gates

Run all quality checks (124+ tests, strict architecture boundary enforcement, linting, and types):

```powershell
# Test suite
backend\.venv\Scripts\python.exe -m pytest backend/tests

# Architecture boundary checks (import-linter)
backend\.venv\Scripts\lint-imports.exe --config backend/pyproject.toml

# Formatting and linting
backend\.venv\Scripts\ruff.exe check backend
backend\.venv\Scripts\ruff.exe format --check backend

# Strict type checker
backend\.venv\Scripts\mypy.exe --config-file backend/pyproject.toml backend
```

---

## 🛠️ Automated Skills & Rules

The repository contains automated skills in [`.agents/skills/`](file:///c:/Dev/AI-Agents-App/.agents/skills):
- **`provider-scaffolder-skill`**: Standardized scaffold generator for new agent adapters.
- **`vibe-prompt-validator-skill`**: Deterministic linter scoring prompts against best practices.
- **`execution-resilience-skill`**: Error taxonomy and recovery decision matrix (circuit breaker, backoff, fallback routing).
- **Guidelines**: See [`AGENTS.md`](file:///c:/Dev/AI-Agents-App/AGENTS.md) and [`.agents/rules/architecture.md`](file:///c:/Dev/AI-Agents-App/.agents/rules/architecture.md).
