---
name: execution-resilience-skill
description: >-
  Fault tolerance, retry policies, circuit breakers, stream reconnection, and fallback
  routing strategies across heterogeneous agent providers. Use when designing error
  handling in provider adapters, troubleshooting interrupted executions, or tuning
  timeouts and backoff algorithms.
---

# Execution Resilience Skill

This skill defines the operational policies and design principles ensuring robust execution across distributed LLM APIs and local agent runners.

## Core Resilience Pillars

### 1. Strongly-Typed Error Routing
Never parse error message strings. All provider adapters map native exceptions to subclasses of `ProviderError` (`app.domain.errors`). The resilience layer checks the class-level properties:
- `retryable`: dictates whether exponential backoff is triggered.
- `fallback_eligible`: dictates whether the task shifts to the next provider in `ExecutionParams.fallback_provider_ids`.

Refer to the full [references/error-taxonomy.md](references/error-taxonomy.md) for the decision matrix.

### 2. Backoff with Full Jitter
When retrying transient errors or rate limits without an explicit `Retry-After`:
```
sleep = min(max_delay_s, base_delay_s * (2 ** attempt))
sleep = uniform(0, sleep)  # Full jitter to prevent thundering herds
```
When `Retry-After` is provided by the backend (e.g. HTTP 429), honour it up to `retry_after_cap_s` (default 60s). If the requested wait exceeds the cap, fail over to the next eligible fallback provider immediately.

### 3. Circuit Breaker per Provider
The application tracks consecutive failures per provider entry:
- **CLOSED**: Provider operates normally.
- **OPEN**: Triggered when failures exceed `breaker_failure_threshold` (default 5). All requests immediately fail over to the next provider in the chain without contacting the failing backend.
- **HALF-OPEN**: After `breaker_reset_s` (default 30s), a single probe request is permitted. Success closes the breaker; failure re-opens it.

### 4. Resumable Streaming & Disconnects
- Every persisted stream event receives a monotonic, integer sequence number (`seq`).
- On network socket drop, the client reconnects via `GET /api/tasks/{id}/events?after_seq=N`.
- The event store replays missing historical events from `N` to current, then seamlessly hooks into the live event bus without duplicating tokens.
- If a provider drops mid-stream without `supports_resume`, an attempt boundary is logged, and a new attempt is initiated if permitted by the idempotency gate.

### 5. Idempotency & Safe Failovers
- Read-only providers (e.g. conversational completions) are marked `idempotent=True` and can be safely retried.
- Workspace-mutating agents (e.g. Antigravity CLI, code-editing runners) are marked `idempotent=False`. If partial work or side-effects occurred before failure, automatic retries are blocked to prevent corruption or race conditions.
