---
name: provider-scaffolder-skill
description: >-
  Standardized blueprint and automated workflow for creating, registering, and validating
  new agent infrastructure adapters without modifying core business logic, API, or web layers.
  Use when adding a new execution backend, LLM provider, or agent framework to Vibe Forge.
---

# Provider Scaffolder Skill

This skill guides the creation of a new agent infrastructure adapter for Vibe Forge.
The architecture enforces strict domain isolation: adding a provider means creating a new isolated package in `backend/vibe/infrastructure/providers/<name>/` and appending an entry to `config/providers.yaml`. Core domain, application services, and UI layers remain untouched.

## 1. Automated Scaffolding

Run the scaffold script from the workspace root:

```powershell
backend\.venv\Scripts\python.exe .agents\skills\provider-scaffolder-skill\scripts\scaffold_provider.py --name <provider_name>
```

Options:
- `--name`: snake_case package identifier (e.g. `anthropic_direct`, `gemini_live`).
- `--kind`: optional provider family slug (default: name with hyphens).
- `--class-name`: optional CamelCase adapter class name (default: CamelCase + `Provider`).
- `--dry-run`: preview planned file creations without writing to disk.

### Generated File Structure

The script generates:
- `backend/vibe/infrastructure/providers/<name>/__init__.py`: Package export.
- `backend/vibe/infrastructure/providers/<name>/adapter.py`: Adapter subclassing `BaseAgentRunner`.
- `backend/vibe/infrastructure/providers/<name>/mapping.py`: Pure mapping from `ExecutionTask` to provider-native request payloads.
- `backend/vibe/infrastructure/providers/<name>/errors.py`: Error translator from native exceptions to the domain taxonomy.
- `backend/tests/providers/test_<name>.py`: Contract test suite subclassing `ProviderContractTests`.
- Appends a disabled entry to `config/providers.yaml`.

## 2. Adapter Implementation Checklist

When filling in the generated templates:

1. **Implement `stream_execute`**:
   - Yield `StreamEvent` instances using `self.event(task, EventType.TOKEN, text=...)` (or `THOUGHT`, `TOOL_CALL`, `ARTIFACT`, `USAGE`).
   - Leave `seq=0`; the application event store assigns monotonically increasing sequence numbers.
   - Conclude with a single `self.event(task, EventType.DONE, finish_reason="stop")`.
   - Never swallow errors into the stream; always translate native errors using `translate_error` and raise them.

2. **Implement `health_check`**:
   - Perform a cheap, non-blocking check (e.g., verifying an API key or checking binary availability).
   - Never raise exceptions; return `HealthStatus.up()` or `HealthStatus.down("reason")`.

3. **Declare Capabilities Honestly**:
   - `idempotent`: set to `False` if the provider modifies local files or remote resources (prevents automatic blind retries after side effects).
   - `supports_cancel`: set to `True` if you implement the `cancel(task_id)` method.
   - `can_modify_workspace`: set to `True` for agentic coding runners.

4. **Preserve Isolation**:
   - Never import from sibling providers (`vibe.infrastructure.providers.<other>`).
   - Never import from `vibe.infrastructure.persistence`, `vibe.infrastructure.eventing`, or `vibe.api`.
   - Only import from `vibe.domain`, `vibe.domain.ports`, and `vibe.infrastructure.provider_kit`.

## 3. Verification & Quality Gates

Run the verification suite:

```powershell
# Run the contract tests for your provider
backend\.venv\Scripts\python.exe -m pytest backend/tests/providers/test_<name>.py

# Run architecture boundary checks (must keep all 5 contracts)
backend\.venv\Scripts\lint-imports.exe

# Format and lint
backend\.venv\Scripts\ruff.exe check backend/vibe/infrastructure/providers/<name>
backend\.venv\Scripts\ruff.exe format --check backend/vibe/infrastructure/providers/<name>
backend\.venv\Scripts\mypy.exe
```
