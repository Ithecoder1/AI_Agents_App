from __future__ import annotations

import pytest
from app.domain.enums import EventType, TaskStatus, TaskTrigger
from app.domain.errors import (
    AuthenticationError,
    BudgetExceededError,
    ContentRejectedError,
    ExecutionCancelledError,
    IllegalTransitionError,
    InvalidRequestError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitedError,
    StreamDisconnectedError,
    TransientProviderError,
    ValidationError,
)
from app.domain.models import (
    AgentProviderConfig,
    ExecutionParams,
    HealthStatus,
    ProviderCapabilities,
    StreamEvent,
    Usage,
)

from tests.factories import make_config, make_prompt, make_task


# --- error taxonomy ---------------------------------------------------------- #
@pytest.mark.parametrize(
    ("error", "retryable", "fallback"),
    [
        (RateLimitedError, True, True),
        (ProviderTimeoutError, True, True),
        (TransientProviderError, True, True),
        (StreamDisconnectedError, True, True),
        (ProviderUnavailableError, False, True),
        (AuthenticationError, False, False),
        (InvalidRequestError, False, False),
        (ContentRejectedError, False, False),
        (BudgetExceededError, False, False),
        (ExecutionCancelledError, False, False),
    ],
)
def test_error_flags_match_the_resilience_policy(
    error: type[ProviderError], retryable: bool, fallback: bool
) -> None:
    assert error.retryable is retryable
    assert error.fallback_eligible is fallback
    info = error("boom", retry_after=1.5).to_info()
    assert info.code == error.code
    assert info.retryable is retryable
    assert info.retry_after == 1.5


# --- tasks --------------------------------------------------------------------- #
def test_task_start_opens_attempts_and_close_records_error() -> None:
    task = make_task()
    assert (task.status, task.attempt) == (TaskStatus.QUEUED, 0)

    running = task.start("fake")
    assert running.status is TaskStatus.RUNNING
    assert running.attempt == 1
    assert running.attempts[0].provider_id == "fake"

    err = RateLimitedError("slow down").to_info()
    retrying = running.close_attempt(err).apply(TaskTrigger.RETRY)
    assert retrying.attempts[0].finished_at is not None
    assert retrying.attempts[0].error == err

    again = retrying.start("other")
    assert again.attempt == 2
    assert [a.provider_id for a in again.attempts] == ["fake", "other"]


def test_task_transitions_are_immutable_and_validated() -> None:
    task = make_task()
    task.start("fake")
    assert task.status is TaskStatus.QUEUED  # original untouched
    with pytest.raises(IllegalTransitionError):
        task.apply(TaskTrigger.SUCCEED)


def test_terminal_task_cannot_restart() -> None:
    done = make_task().apply(TaskTrigger.CANCEL)
    assert done.is_terminal
    with pytest.raises(IllegalTransitionError):
        done.start("fake")


# --- params / config validation -------------------------------------------------- #
@pytest.mark.parametrize(
    "kwargs",
    [
        {"temperature": 2.5},
        {"temperature": -0.1},
        {"max_tokens": 0},
        {"budget_usd": -1.0},
        {"timeout_s": 0},
    ],
)
def test_execution_params_reject_invalid_values(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        ExecutionParams(provider_id="fake", **kwargs)  # type: ignore[arg-type]


def test_provider_chain_dedupes_and_keeps_order() -> None:
    params = ExecutionParams("a", fallback_provider_ids=("b", "a", "c", "b"))
    assert params.provider_chain == ("a", "b", "c")


@pytest.mark.parametrize("bad_id", ["", "Has Space", "UPPER", "-lead", "a/b"])
def test_provider_config_rejects_bad_ids(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        AgentProviderConfig(id=bad_id, kind="x")


def test_provider_config_masks_secrets_and_hides_options_in_repr() -> None:
    cfg = make_config(
        options={"api_key": "sk-123", "base_url": "https://x", "nested": {"token": "t"}}
    )
    assert "sk-123" not in repr(cfg)
    masked = cfg.safe_options()
    assert masked["api_key"] == "***"
    assert masked["nested"]["token"] == "***"
    assert masked["base_url"] == "https://x"


def test_capability_overrides_are_validated() -> None:
    base = ProviderCapabilities(supports_cancel=True)
    merged = base.merged({"idempotent": True, "models": ["a", "b"]})
    assert merged.supports_cancel and merged.idempotent
    assert merged.models == ("a", "b")
    with pytest.raises(ValidationError):
        base.merged({"warp_drive": True})


# --- prompt / events / usage / health ------------------------------------------- #
def test_prompt_render_substitutes_and_reports_missing() -> None:
    prompt = make_prompt("Write {{lang}} for {{task}}", parameters={"lang": "Go", "task": "x"})
    assert prompt.render() == ("", "Write Go for x")
    assert prompt.variables_used() == {"lang", "task"}
    with pytest.raises(ValidationError, match="task"):
        make_prompt("{{task}}").render()


def test_stream_event_round_trips_through_dict() -> None:
    event = StreamEvent.create("t1", 2, EventType.TOKEN, text="hi").with_seq(7)
    again = StreamEvent.from_dict(event.to_dict())
    assert again == event
    assert again.seq == 7


def test_usage_addition_handles_unknown_cost() -> None:
    total = Usage(1, 2) + Usage(3, 4, 0.5)
    assert (total.prompt_tokens, total.completion_tokens, total.total_tokens) == (4, 6, 10)
    assert total.cost_usd == 0.5
    assert (Usage(1, 1) + Usage(1, 1)).cost_usd is None


def test_health_status_truthiness() -> None:
    assert HealthStatus.up()
    assert not HealthStatus.down("no binary")
