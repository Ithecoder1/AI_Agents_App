"""The provider contract suite -- every adapter must pass it.

Usage (in ``tests/providers/test_<name>.py``)::

    class TestMyProviderContract(ProviderContractTests):
        def build(self, secret: str) -> IAgentProvider: ...
        def build_slow(self) -> IAgentProvider | None: ...   # optional, enables cancel test

The class name intentionally does NOT start with ``Test`` so pytest only runs it through
subclasses.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from app.domain.enums import EventType, TaskStatus
from app.domain.errors import ExecutionCancelledError
from app.domain.models import ExecutionResult, HealthStatus, ProviderCapabilities
from app.domain.ports.agent_provider import IAgentProvider

from tests.factories import running_task

SECRET = "sk-test-SUPER-secret-1234567890"


class ProviderContractTests:
    # ---- hooks -------------------------------------------------------------- #
    def build(self, secret: str) -> IAgentProvider:
        """Return a provider whose options contain *secret* (e.g. as ``api_key``)."""
        raise NotImplementedError

    def build_slow(self) -> IAgentProvider | None:
        """Return a provider that streams slowly enough to cancel mid-flight, or ``None``."""
        return None

    # ---- tests ---------------------------------------------------------------- #
    def test_satisfies_protocol_and_declares_capabilities(self) -> None:
        provider = self.build(SECRET)
        assert isinstance(provider, IAgentProvider)
        assert isinstance(provider.capabilities, ProviderCapabilities)
        assert provider.config.id

    async def test_health_check_returns_status_and_never_raises(self) -> None:
        provider = self.build(SECRET)
        status = await provider.health_check()
        assert isinstance(status, HealthStatus)
        if not status:
            assert status.reason, "an unhealthy provider must say why"
        await provider.aclose()

    async def test_stream_is_well_formed_and_ends_with_single_done(self) -> None:
        provider = self.build(SECRET)
        task = running_task(provider.config.id)
        events = [e async for e in provider.stream_execute(task)]

        assert events, "stream produced no events"
        assert events[-1].type is EventType.DONE
        assert sum(e.type is EventType.DONE for e in events) == 1
        for event in events:
            assert event.task_id == task.id
            assert event.attempt == task.attempt
            assert event.seq == 0, "providers must leave seq assignment to the application"
            assert isinstance(event.type, EventType)
        await provider.aclose()

    async def test_execute_matches_folded_stream(self) -> None:
        provider = self.build(SECRET)
        task = running_task(provider.config.id)
        tokens = [
            str(e.data.get("text", ""))
            async for e in provider.stream_execute(task)
            if e.type is EventType.TOKEN
        ]
        streamed = "".join(tokens)
        result = await provider.execute(task)

        assert isinstance(result, ExecutionResult)
        assert result.status is TaskStatus.SUCCEEDED
        assert result.task_id == task.id
        assert result.provider_id == provider.config.id
        assert result.output_text == streamed
        await provider.aclose()

    async def test_cancel_of_unknown_task_is_a_safe_noop(self) -> None:
        provider = self.build(SECRET)
        await provider.cancel("does-not-exist")
        await provider.cancel("does-not-exist")  # idempotent
        await provider.aclose()

    async def test_cancel_stops_a_running_stream(self) -> None:
        provider = self.build_slow()
        if provider is None or not provider.capabilities.supports_cancel:
            pytest.skip("provider does not support cancel / no slow variant supplied")
        task = running_task(provider.config.id)
        stream = provider.stream_execute(task)
        await stream.__anext__()  # stream is live

        async def drain() -> None:
            async for _ in stream:
                pass

        consumer = asyncio.create_task(drain())
        await asyncio.sleep(0.05)
        await provider.cancel(task.id)
        with pytest.raises(ExecutionCancelledError):
            await asyncio.wait_for(consumer, timeout=2.0)
        await provider.aclose()

    async def test_secrets_never_leak_into_events_or_results(self) -> None:
        provider = self.build(SECRET)
        task = running_task(provider.config.id)
        events = [e async for e in provider.stream_execute(task)]
        result = await provider.execute(task)

        blob = json.dumps([e.to_dict() for e in events], default=str) + repr(result)
        assert SECRET not in blob
        assert SECRET not in repr(provider.config)
        await provider.aclose()
