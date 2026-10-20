# Vibe Forge Error Taxonomy & Recovery Decision Matrix

Every provider adapter translates native errors into strongly typed subclasses of `ProviderError` (`vibe.domain.errors`). The resilience layer inspects two class-level flags to make automated routing decisions:

- `retryable: bool` — safe to retry the *same* provider using exponential backoff with jitter.
- `fallback_eligible: bool` — after retries are exhausted (or immediately if `retryable=False`), safe to route the task to the next provider in `ExecutionParams.fallback_provider_ids`.

## Matrix

| Exception Class | Error Code | `retryable` | `fallback_eligible` | Recommended Action |
|---|---|---|---|---|
| `RateLimitedError` | `rate_limited` | **True** | **True** | Wait `retry_after` (if provided and under `retry_after_cap_s`) or apply exponential backoff. Fall back if delay exceeds cap or after max attempts. |
| `ProviderTimeoutError` | `timeout` | **True** | **True** | Retry with full jitter. If timeout persists, route to the next provider in the fallback chain. |
| `TransientProviderError` | `transient` | **True** | **True** | 5xx server errors, connection resets. Backoff (1s -> 2s -> 4s) up to `max_attempts`, then fall back. |
| `StreamDisconnectedError` | `stream_disconnected` | **True** | **True** | Stream ended without `done` event. If provider has `supports_resume=True`, resume from cursor; otherwise, record attempt boundary and restart execution. |
| `ProviderUnavailableError` | `provider_unavailable` | **False** | **True** | Provider is disabled, missing executable, or down. **Do not retry**; immediately fall back to the next configured provider. |
| `AuthenticationError` | `authentication_failed` | **False** | **False** | Invalid or missing API key. **Fail immediately**; do not fall back (configuration problem). |
| `InvalidRequestError` | `invalid_request` | **False** | **False** | Malformed parameters or unrendered prompt variable. **Fail immediately**; do not fall back. |
| `ContentRejectedError` | `content_rejected` | **False** | **False** | Safety policy or content moderation block. **Fail immediately**. |
| `BudgetExceededError` | `budget_exceeded` | **False** | **False** | Task or prompt exceeded allocated `budget_usd` or token quota. **Fail immediately**. |
| `ExecutionCancelledError` | `cancelled` | **False** | **False** | User clicked stop or cancellation was signaled. Clean up subprocess/socket and transition task to `CANCELLED`. |

## Critical Safety Rule: Idempotency Gate

```
if not provider.capabilities.idempotent and task.has_performed_side_effects:
    AUTOMATIC_RETRY = DISABLED
```

If a provider adapter has `idempotent=False` (such as an agent execution runner modifying code in a local workspace) and side-effecting operations have already been executed, the resilience layer **must never automatically retry or fall back**. The task is transitioned to `FAILED` with details, leaving the decision to retry to a human operator.
