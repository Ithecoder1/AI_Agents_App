from __future__ import annotations

import json

import httpx
import pytest

from tests.contract.provider_contract import ProviderContractTests
from tests.factories import make_config, make_deps, running_task
from tests.providers.http_fixtures import slow_transport, sse, stream_transport
from vibe.domain.enums import EventType
from vibe.domain.errors import ContentRejectedError
from vibe.domain.ports.agent_provider import IAgentProvider
from vibe.infrastructure.providers.gemini import GeminiProvider


def _chunk(*parts: dict[str, object], **extra: object) -> dict[str, object]:
    return {"candidates": [{"content": {"parts": list(parts)}, **extra}]}


_BODY = sse(
    _chunk({"text": "Reasoning...", "thought": True}),
    _chunk({"text": "Answer "}),
    {
        **_chunk({"text": "text"}, finishReason="STOP"),
        "usageMetadata": {
            "promptTokenCount": 6,
            "candidatesTokenCount": 4,
            "thoughtsTokenCount": 3,
        },
    },
)


def _provider(
    transport: httpx.AsyncBaseTransport | None = None, **options: object
) -> GeminiProvider:
    options.setdefault("api_key", "gk-test")
    cfg = make_config("gemini-default", "gemini", options=options)
    return GeminiProvider(cfg, make_deps(), transport=transport or stream_transport(_BODY))


class TestGeminiContract(ProviderContractTests):
    def build(self, secret: str) -> IAgentProvider:
        return _provider(api_key=secret)

    def build_slow(self) -> IAgentProvider | None:
        return GeminiProvider(
            make_config("gemini-default", "gemini", options={"api_key": "k"}),
            make_deps(),
            transport=slow_transport(sse(_chunk({"text": "x"}))),
        )


async def test_thought_parts_stream_as_thoughts_and_usage_counts_thinking_tokens() -> None:
    provider = _provider()
    task = running_task("gemini-default")
    events = [e async for e in provider.stream_execute(task)]
    assert [str(e.data["text"]) for e in events if e.type is EventType.THOUGHT] == ["Reasoning..."]
    result = await provider.execute(task)
    assert result.output_text == "Answer text"
    assert (result.usage.prompt_tokens, result.usage.completion_tokens) == (6, 7)


async def test_request_uses_header_auth_sse_and_thinking_config() -> None:
    seen: list[httpx.Request] = []
    provider = _provider(
        transport=stream_transport(_BODY, on_request=seen.append), thinking_budget=-1
    )
    await provider.execute(running_task("gemini-default", model="gemini-2.5-pro", max_tokens=50))
    request = seen[0]
    body = json.loads(request.content)
    assert request.headers["x-goog-api-key"] == "gk-test"
    assert "gk-test" not in str(request.url)
    assert request.url.path.endswith("/models/gemini-2.5-pro:streamGenerateContent")
    assert request.url.params["alt"] == "sse"
    assert body["generationConfig"]["thinkingConfig"] == {
        "includeThoughts": True,
        "thinkingBudget": -1,
    }
    assert body["generationConfig"]["maxOutputTokens"] == 50


async def test_prompt_block_is_content_rejected() -> None:
    body = sse({"promptFeedback": {"blockReason": "SAFETY"}})
    provider = _provider(transport=stream_transport(body))
    with pytest.raises(ContentRejectedError):
        await provider.execute(running_task("gemini-default"))


async def test_google_api_key_env_is_accepted_as_fallback() -> None:
    cfg = make_config("gemini-default", "gemini")
    provider = GeminiProvider(
        cfg, make_deps({"GOOGLE_API_KEY": "from-env"}), transport=stream_transport(_BODY)
    )
    assert (await provider.execute(running_task("gemini-default"))).output_text == "Answer text"
