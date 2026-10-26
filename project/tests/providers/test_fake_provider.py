from __future__ import annotations

import pytest
from app.domain.enums import ArtifactKind, EventType, TaskTrigger
from app.domain.errors import (
    ConfigurationError,
    InvalidRequestError,
    RateLimitedError,
    StreamDisconnectedError,
)
from app.domain.ports.agent_provider import IAgentProvider
from providers.adapters.fake import FakeProvider

from tests.contract.provider_contract import ProviderContractTests
from tests.factories import make_config, make_deps, running_task


def _fake(**options: object) -> FakeProvider:
    return FakeProvider(make_config(options=options), make_deps())


class TestFakeProviderContract(ProviderContractTests):
    def build(self, secret: str) -> IAgentProvider:
        return _fake(api_key=secret)

    def build_slow(self) -> IAgentProvider | None:
        return _fake(delay_ms=100)


async def test_fail_first_n_fails_with_retry_after_then_succeeds() -> None:
    provider = _fake(fail_first_n=1, fail_error="rate_limited", retry_after_s=0.25, chunks=["ok"])
    first = running_task()  # attempt 1

    with pytest.raises(RateLimitedError) as info:
        await provider.execute(first)
    assert info.value.retry_after == 0.25
    assert info.value.retryable is True

    second = first.close_attempt(info.value.to_info()).apply(TaskTrigger.RETRY).start("fake")
    assert second.attempt == 2
    assert (await provider.execute(second)).output_text == "ok"


async def test_mid_stream_failure_emits_partial_tokens_first() -> None:
    provider = _fake(
        chunks=["a", "b", "c"], fail_first_n=1, fail_after_chunks=2, fail_error="disconnect"
    )
    task = running_task()
    seen: list[str] = []
    with pytest.raises(StreamDisconnectedError):
        async for event in provider.stream_execute(task):
            if event.type is EventType.TOKEN:
                seen.append(str(event.data["text"]))
    assert seen == ["a", "b"]


async def test_retry_attempt_succeeds_after_scripted_failures() -> None:
    from app.domain.enums import TaskTrigger

    provider = _fake(fail_first_n=1, chunks=["ok"])
    first = running_task()
    with pytest.raises(Exception, match="simulated"):
        await provider.execute(first)

    second = first.close_attempt().apply(TaskTrigger.RETRY).start("fake")
    assert second.attempt == 2
    result = await provider.execute(second)
    assert result.output_text == "ok"


async def test_artifact_is_emitted_and_folded_into_result() -> None:
    result = await _fake(emit_artifact=True).execute(running_task())
    assert len(result.artifacts) == 1
    assert result.artifacts[0].kind is ArtifactKind.DIFF
    assert result.artifacts[0].inline_content
    assert result.usage.total_tokens > 0


async def test_undefined_prompt_variable_is_an_invalid_request() -> None:
    task = running_task(user="Hello {{name}}")
    with pytest.raises(InvalidRequestError):
        await _fake().execute(task)


async def test_unhealthy_option_is_reported_with_reason() -> None:
    status = await _fake(healthy=False).health_check()
    assert not status
    assert status.reason


def test_unknown_fail_error_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError):
        _fake(fail_error="nope")


def test_capability_overrides_from_config_apply() -> None:
    provider = FakeProvider(
        make_config(capabilities={"idempotent": False, "models": ["m1"]}), make_deps()
    )
    assert provider.capabilities.idempotent is False
    assert provider.capabilities.models == ("m1",)
