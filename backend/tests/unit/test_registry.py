from __future__ import annotations

import pytest

from tests.factories import make_config, make_deps, running_task
from vibe.application.registry import (
    ProviderRegistry,
    ProviderState,
    entry_point_lookup,
    load_factory,
)
from vibe.domain.errors import (
    ConfigurationError,
    ProviderNotFoundError,
    ProviderUnavailableError,
)
from vibe.domain.models import AgentProviderConfig, HealthStatus
from vibe.domain.ports.agent_provider import ProviderDeps
from vibe.infrastructure.providers.fake import FakeProvider

FAKE = "vibe.infrastructure.providers.fake:FakeProvider"


def not_a_provider(config: AgentProviderConfig, deps: ProviderDeps) -> object:
    return object()


class ExplodingHealth(FakeProvider):
    async def health_check(self) -> HealthStatus:
        raise RuntimeError("kaboom")


def _registry(**kwargs: object) -> ProviderRegistry:
    return ProviderRegistry(make_deps(), **kwargs)  # type: ignore[arg-type]


async def test_registers_resolves_and_runs_a_provider_by_adapter_path() -> None:
    registry = _registry()
    entry = registry.register(make_config("fake", adapter=FAKE))
    assert entry.state is ProviderState.READY

    provider = registry.resolve("fake")
    result = await provider.execute(running_task("fake", user="hi there"))
    assert "hi" in result.output_text


def test_disabled_provider_is_listed_but_unavailable() -> None:
    registry = _registry()
    entry = registry.register(make_config("off", adapter=FAKE, enabled=False))
    assert entry.state is ProviderState.DISABLED
    with pytest.raises(ProviderUnavailableError) as info:
        registry.resolve("off")
    assert info.value.fallback_eligible  # callers may fail over


@pytest.mark.parametrize(
    "adapter",
    [
        "no_such_module_xyz:Thing",
        "vibe.infrastructure.providers.fake:Missing",
        "tests.unit.test_registry:not_a_provider",  # builds, but violates the contract
        "tests.unit.test_registry:FAKE",  # not callable
    ],
)
def test_broken_adapter_is_isolated_not_fatal(adapter: str) -> None:
    registry = _registry()
    entry = registry.register(make_config("bad", adapter=adapter))
    assert entry.state is ProviderState.FAILED
    assert entry.error
    registry.register(make_config("good", adapter=FAKE))  # other providers unaffected
    assert registry.get_entry("good").state is ProviderState.READY
    with pytest.raises(ProviderUnavailableError):
        registry.resolve("bad")


def test_duplicate_and_unknown_ids() -> None:
    registry = _registry()
    registry.register(make_config("fake", adapter=FAKE))
    with pytest.raises(ConfigurationError):
        registry.register(make_config("fake", adapter=FAKE))
    with pytest.raises(ProviderNotFoundError):
        registry.resolve("ghost")


def test_resolution_by_entry_point_kind_when_no_adapter() -> None:
    lookups: list[str] = []

    def lookup(kind: str):  # type: ignore[no-untyped-def]
        lookups.append(kind)
        return FakeProvider if kind == "plugin-kind" else None

    registry = _registry(entry_points=lookup)
    assert registry.register(make_config("p1", kind="plugin-kind")).state is ProviderState.READY
    missing = registry.register(make_config("p2", kind="unknown-kind"))
    assert missing.state is ProviderState.FAILED
    assert "entry point" in (missing.error or "")
    assert lookups == ["plugin-kind", "unknown-kind"]


def test_builtin_fake_provider_is_declared_as_an_entry_point() -> None:
    assert entry_point_lookup("fake") is FakeProvider


def test_load_factory_validates_spec_format() -> None:
    with pytest.raises(ConfigurationError):
        load_factory("no-colon")
    assert load_factory(FAKE) is FakeProvider


async def test_health_is_cached_until_ttl_or_forced() -> None:
    now = [0.0]
    registry = _registry(health_ttl_s=10.0, clock=lambda: now[0])
    registry.register(make_config("fake", adapter=FAKE, options={"healthy": True}))
    provider = registry.resolve("fake")
    assert isinstance(provider, FakeProvider)

    first = await registry.health("fake")
    provider._healthy = False  # flip underlying state
    assert (await registry.health("fake")) is first  # cached
    assert not await registry.health("fake", force=True)  # forced refresh sees the change
    provider._healthy = True
    now[0] = 11.0  # TTL elapsed
    assert await registry.health("fake")


async def test_health_never_raises_and_reports_non_ready_reasons() -> None:
    registry = _registry()
    registry.register(make_config("boom", adapter="tests.unit.test_registry:ExplodingHealth"))
    registry.register(make_config("off", adapter=FAKE, enabled=False))
    status = await registry.health("boom")
    assert not status
    assert "kaboom" in (status.reason or "")
    assert not await registry.health("off")

    everything = await registry.health_all()
    assert set(everything) == {"boom", "off"}


def test_register_failure_lists_unconfigurable_entries() -> None:
    registry = _registry()
    entry = registry.register_failure("broken", "schema error")
    assert entry.state is ProviderState.FAILED
    assert [e.config.id for e in registry.entries()] == ["broken"]
