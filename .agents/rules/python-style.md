# Python Coding Style & Environment Rules

## Environment Compatibility
- Target environment is **Python 3.10.9+**.
- **Do NOT use features introduced in Python 3.11+**:
  - Do NOT import `StrEnum`. Use `(str, Enum)` from `enum`.
  - Do NOT import `tomllib`.
  - Do NOT use `ExceptionGroup` or `TaskGroup`. Use `asyncio.gather` or `anyio`.
  - Always use `from __future__ import annotations` at the top of every file.

## Typing & Static Analysis
- Strict typing is enforced via `mypy --strict`. All function signatures and return types must be explicitly annotated.
- Avoid `Any` where possible. Prefer protocols and generics.
- Line length limit is **100 characters** (enforced by `ruff`).
- Code must pass `ruff check .` and `ruff format --check .` without errors or warnings.

## Asynchronous Conventions
- Use async/await natively for all I/O bound operations.
- Providers must yield `StreamEvent` instances via `async def stream_execute(...) -> AsyncIterator[StreamEvent]`.
- Always implement cancellation handling cleanly without leaving detached background tasks or orphaned processes.
