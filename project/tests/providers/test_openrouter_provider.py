from __future__ import annotations

import json

import httpx
import pytest
from app.domain.enums import EventType
from app.domain.errors import (
    AuthenticationError,
    InvalidRequestError,
    RateLimitedError,
    StreamDisconnectedError,
)
from app.domain.ports.agent_provider import IAgentProvider
from providers.adapters.openrouter import OpenRouterProvider

from tests.contract.provider_contract import ProviderContractTests
from tests.factories import make_config, make_deps, running_task
from tests.providers.http_fixtures import slow_transport, sse, stream_transport


def _chunk(**delta: object) -> dict[str, object]:
    return {"choices": [{"delta": delta}]}


_BODY = sse(
    _chunk(reasoning="Let me think. "),
    _chunk(reasoning="Done."),
    _chunk(content="Hello "),
    _chunk(content="world"),
    {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 5, "cost": 0.001}},
    "[DONE]",
)


def _provider(
    transport: httpx.AsyncBaseTransport | None = None, **options: object
) -> OpenRouterProvider:
    options.setdefault("api_key", "sk-or-test")
    cfg = make_config("openrouter-default", "openrouter", options=options)
    return OpenRouterProvider(cfg, make_deps(), transport=transport or stream_transport(_BODY))


class TestOpenRouterContract(ProviderContractTests):
    def build(self, secret: str) -> IAgentProvider:
        return _provider(api_key=secret)

    def build_slow(self) -> IAgentProvider | None:
        chunk = sse(_chunk(content="x"))
        return OpenRouterProvider(
            make_config("openrouter-default", "openrouter", options={"api_key": "k"}),
            make_deps(),
            transport=slow_transport(chunk),
        )


async def test_reasoning_streams_as_thoughts_and_answer_as_tokens() -> None:
    provider = _provider()
    events = [e async for e in provider.stream_execute(running_task("openrouter-default"))]
    thoughts = "".join(str(e.data["text"]) for e in events if e.type is EventType.THOUGHT)
    tokens = "".join(str(e.data["text"]) for e in events if e.type is EventType.TOKEN)
    assert thoughts == "Let me think. Done."
    assert tokens == "Hello world"

    result = await provider.execute(running_task("openrouter-default"))
    assert (result.usage.prompt_tokens, result.usage.completion_tokens) == (7, 5)
    assert result.usage.cost_usd == 0.001


async def test_request_maps_task_and_reasoning_options() -> None:
    seen: list[httpx.Request] = []
    provider = _provider(
        transport=stream_transport(_BODY, on_request=seen.append),
        provider_routing={"order": ["anthropic"]},
    )
    task = running_task(
        "openrouter-default",
        model="anthropic/claude-sonnet-4",
        temperature=0.2,
        max_tokens=100,
        extra={"reasoning": {"effort": "high"}},
    )
    await provider.execute(task)
    request = seen[0]
    body = json.loads(request.content)
    assert request.headers["authorization"] == "Bearer sk-or-test"
    assert request.url.path.endswith("/chat/completions")
    assert body["model"] == "anthropic/claude-sonnet-4"
    assert body["reasoning"] == {"effort": "high"}
    assert body["provider"] == {"order": ["anthropic"]}
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 100


async def test_missing_api_key_is_an_authentication_error() -> None:
    provider = _provider(api_key="")
    with pytest.raises(AuthenticationError):
        await provider.execute(running_task("openrouter-default"))
    assert not await provider.health_check()


async def test_http_429_maps_to_rate_limited_with_retry_after() -> None:
    body = b'{"error": {"message": "slow down"}}'
    provider = _provider(transport=stream_transport(body, status=429, headers={"retry-after": "3"}))
    with pytest.raises(RateLimitedError) as info:
        await provider.execute(running_task("openrouter-default"))
    assert info.value.retry_after == 3.0


async def test_http_400_is_an_invalid_request() -> None:
    provider = _provider(transport=stream_transport(b'{"error": {"message": "bad"}}', status=400))
    with pytest.raises(InvalidRequestError):
        await provider.execute(running_task("openrouter-default"))


async def test_stream_without_finish_reason_is_a_disconnect() -> None:
    provider = _provider(transport=stream_transport(sse(_chunk(content="partial"))))
    with pytest.raises(StreamDisconnectedError):
        await provider.execute(running_task("openrouter-default"))
