from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tests.contract.provider_contract import ProviderContractTests
from tests.factories import make_config, make_deps, running_task
from tests.providers.http_fixtures import slow_transport, sse, stream_transport
from vibe.domain.enums import EventType
from vibe.domain.errors import ContentRejectedError, InvalidRequestError, TransientProviderError
from vibe.domain.ports.agent_provider import IAgentProvider
from vibe.infrastructure.providers.anthropic import AnthropicProvider

_EVENTS: list[dict[str, Any]] = [
    {"type": "message_start", "message": {"usage": {"input_tokens": 11, "output_tokens": 1}}},
    {"type": "ping"},
    {
        "type": "content_block_delta",
        "delta": {"type": "thinking_delta", "thinking": "Considering options."},
    },
    {"type": "content_block_delta", "delta": {"type": "signature_delta", "signature": "abc"}},
    {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi "}},
    {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "there"}},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 9}},
    {"type": "message_stop"},
]
_BODY = sse(*_EVENTS, events=[str(e["type"]) for e in _EVENTS])


def _provider(
    transport: httpx.AsyncBaseTransport | None = None, **options: object
) -> AnthropicProvider:
    options.setdefault("api_key", "sk-ant-test")
    cfg = make_config("anthropic-default", "anthropic", options=options)
    return AnthropicProvider(cfg, make_deps(), transport=transport or stream_transport(_BODY))


class TestAnthropicContract(ProviderContractTests):
    def build(self, secret: str) -> IAgentProvider:
        return _provider(api_key=secret)

    def build_slow(self) -> IAgentProvider | None:
        chunk = sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "x"}})
        return AnthropicProvider(
            make_config("anthropic-default", "anthropic", options={"api_key": "k"}),
            make_deps(),
            transport=slow_transport(chunk),
        )


async def test_thinking_and_text_are_separated_and_usage_is_reported() -> None:
    provider = _provider()
    task = running_task("anthropic-default")
    events = [e async for e in provider.stream_execute(task)]
    thoughts = [str(e.data["text"]) for e in events if e.type is EventType.THOUGHT]
    assert thoughts == ["Considering options."]
    result = await provider.execute(task)
    assert result.output_text == "Hi there"
    assert (result.usage.prompt_tokens, result.usage.completion_tokens) == (11, 9)
    assert events[-1].data["finish_reason"] == "stop"


async def test_thinking_budget_enables_extended_thinking_and_drops_temperature() -> None:
    seen: list[httpx.Request] = []
    provider = _provider(transport=stream_transport(_BODY, on_request=seen.append))
    task = running_task("anthropic-default", temperature=0.3, extra={"thinking_budget": 2048})
    await provider.execute(task)
    request = seen[0]
    body = json.loads(request.content)
    assert request.headers["x-api-key"] == "sk-ant-test"
    assert request.headers["anthropic-version"]
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 2048}
    assert "temperature" not in body
    assert body["max_tokens"] > 2048


@pytest.mark.parametrize(
    "extra, max_tokens",
    [({"thinking_budget": 100}, None), ({"thinking_budget": 4000}, 2000)],
)
async def test_invalid_thinking_budget_is_an_invalid_request(
    extra: dict[str, int], max_tokens: int | None
) -> None:
    provider = _provider()
    with pytest.raises(InvalidRequestError):
        await provider.execute(
            running_task("anthropic-default", extra=extra, max_tokens=max_tokens)
        )


async def test_overloaded_error_event_is_transient() -> None:
    body = sse({"type": "error", "error": {"type": "overloaded_error", "message": "busy"}})
    provider = _provider(transport=stream_transport(body))
    with pytest.raises(TransientProviderError):
        await provider.execute(running_task("anthropic-default"))


async def test_refusal_is_content_rejected() -> None:
    body = sse({"type": "message_delta", "delta": {"stop_reason": "refusal"}, "usage": {}})
    provider = _provider(transport=stream_transport(body))
    with pytest.raises(ContentRejectedError):
        await provider.execute(running_task("anthropic-default"))
