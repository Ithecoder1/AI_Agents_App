from __future__ import annotations

from pathlib import Path

import pytest
from app.api.composition import build_registry
from app.application.registry import ProviderState
from app.application.settings import AppSettings
from app.domain.errors import ConfigurationError
from app.infrastructure.config import (
    EnvSecretResolver,
    interpolate,
    load_environment,
    load_provider_configs,
    load_settings,
)

from tests.factories import REPO_CONFIG, REPO_ROOT, running_task

SCHEMA = REPO_CONFIG / "providers.schema.json"


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def load(path: Path, env: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    return load_provider_configs(path, env or {}, schema_path=SCHEMA)


# --- interpolation ---------------------------------------------------------------- #
def test_interpolate_values_defaults_and_nesting() -> None:
    env = {"A": "1", "EMPTY": ""}
    out = interpolate({"x": "${A}-${B:-fallback}", "l": ["${EMPTY:-d}"], "n": 5}, env)
    assert out == {"x": "1-fallback", "l": ["d"], "n": 5}


def test_interpolate_missing_variable_names_it() -> None:
    with pytest.raises(ConfigurationError, match="NOPE"):
        interpolate({"k": "${NOPE}"}, {})


# --- providers.yaml ----------------------------------------------------------------- #
def test_loads_valid_providers_with_env_interpolation(tmp_path: Path) -> None:
    cfg = write(
        tmp_path / "providers.yaml",
        """
providers:
  - id: or
    kind: openrouter
    options: {api_key: "${KEY}", model: m}
    capabilities: {idempotent: true}
""",
    )
    result = load(cfg, {"KEY": "sk-abc"})
    assert not result.failures
    (config,) = result.configs
    assert config.options["api_key"] == "sk-abc"
    assert config.capabilities == {"idempotent": True}
    assert "sk-abc" not in repr(config)


def test_disabled_provider_may_reference_unset_secrets(tmp_path: Path) -> None:
    cfg = write(
        tmp_path / "providers.yaml",
        "providers:\n  - {id: parked, kind: x, enabled: false, options: {api_key: '${MISSING}'}}\n",
    )
    result = load(cfg)
    assert not result.failures
    assert result.configs[0].enabled is False


def test_enabled_provider_with_unset_secret_fails_alone(tmp_path: Path) -> None:
    cfg = write(
        tmp_path / "providers.yaml",
        """
providers:
  - {id: needs-key, kind: x, options: {api_key: "${MISSING}"}}
  - {id: fine, kind: y}
""",
    )
    result = load(cfg)
    assert [c.id for c in result.configs] == ["fine"]
    (failure,) = result.failures
    assert failure.provider_id == "needs-key"
    assert "MISSING" in failure.reason
    assert failure.config is not None and failure.config.enabled is False


def test_schema_violations_are_isolated_per_entry(tmp_path: Path) -> None:
    cfg = write(
        tmp_path / "providers.yaml",
        """
providers:
  - {id: BadId, kind: x}
  - {id: no-kind}
  - {id: typo, kind: x, enabld: true}
  - {id: bad-adapter, kind: x, adapter: "not valid"}
  - {id: ok, kind: x}
""",
    )
    result = load(cfg)
    assert [c.id for c in result.configs] == ["ok"]
    assert len(result.failures) == 4
    assert {f.provider_id for f in result.failures} == {"BadId", "no-kind", "typo", "bad-adapter"}


def test_duplicate_ids_reject_the_later_entry(tmp_path: Path) -> None:
    cfg = write(
        tmp_path / "providers.yaml", "providers:\n  - {id: a, kind: x}\n  - {id: a, kind: y}\n"
    )
    result = load(cfg)
    assert [c.kind for c in result.configs] == ["x"]
    assert "duplicate" in result.failures[0].reason


@pytest.mark.parametrize("content", ["foo: bar\n", "providers: 5\n", "- just\n- a list\n", ""])
def test_malformed_file_is_fatal(tmp_path: Path, content: str) -> None:
    with pytest.raises(ConfigurationError):
        load(write(tmp_path / "providers.yaml", content))


def test_missing_file_is_fatal(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load(tmp_path / "nope.yaml")


def test_local_file_overrides_by_id_and_appends_new(tmp_path: Path) -> None:
    write(
        tmp_path / "providers.yaml",
        "providers:\n  - {id: a, kind: x, options: {p: 1, q: 2}}\n  - {id: b, kind: y}\n",
    )
    write(
        tmp_path / "providers.local.yaml",
        "providers:\n  - {id: a, kind: x, options: {q: 99}}\n  - {id: c, kind: z}\n",
    )
    result = load(tmp_path / "providers.yaml")
    by_id = {c.id: c for c in result.configs}
    assert list(by_id) == ["a", "b", "c"]
    assert by_id["a"].options == {"p": 1, "q": 99}  # deep-merged


# --- settings --------------------------------------------------------------------- #
def test_settings_defaults_without_files(tmp_path: Path) -> None:
    assert load_settings(tmp_path, {}) == AppSettings()


def test_settings_layering_yaml_local_then_env(tmp_path: Path) -> None:
    write(tmp_path / "app.yaml", "server: {port: 9001}\nrunner: {concurrency: 2}\n")
    write(tmp_path / "app.local.yaml", "server: {port: 9002}\n")
    s = load_settings(tmp_path, {"VIBE_RUNNER__CONCURRENCY": "8"})
    assert s.server.port == 9002  # local beats base
    assert s.runner.concurrency == 8  # env beats yaml
    assert s.server.host == "127.0.0.1"  # untouched default


def test_settings_env_aliases_and_tuple_coercion(tmp_path: Path) -> None:
    s = load_settings(
        tmp_path,
        {
            "VIBE_AUTH_TOKEN": "tok",
            "VIBE_DB_URL": "sqlite+aiosqlite:///x.db",
            "VIBE_WORKSPACE_ROOTS": "C:/a, C:/b",
            "VIBE_RESILIENCE__JITTER": "false",
        },
    )
    assert s.security.auth_token == "tok"
    assert s.storage.db_url.endswith("x.db")
    assert s.security.workspace_roots == ("C:/a", "C:/b")
    assert s.resilience.jitter is False


@pytest.mark.parametrize(
    "yaml_text",
    ["server: {prot: 1}\n", "nonsense: {a: 1}\n", "server: {port: abc}\n", "server: 5\n"],
)
def test_settings_typos_and_bad_types_fail_loudly(tmp_path: Path, yaml_text: str) -> None:
    write(tmp_path / "app.yaml", yaml_text)
    with pytest.raises(ConfigurationError):
        load_settings(tmp_path, {})


# --- environment ----------------------------------------------------------------- #
def test_real_environment_beats_dotenv_file(tmp_path: Path) -> None:
    dotenv = write(tmp_path / ".env", "A=from-file\nB=only-file\n")
    env = load_environment(dotenv, {"A": "from-env"})
    assert env["A"] == "from-env"
    assert env["B"] == "only-file"
    assert EnvSecretResolver(env).get("MISSING") is None
    assert EnvSecretResolver({"E": ""}).get("E") is None


# --- the repository's own config (Phase 2 acceptance criterion) ------------------ #
async def test_shipped_config_boots_and_fake_provider_resolves_and_runs() -> None:
    registry, settings = build_registry(REPO_ROOT / "config", {})  # empty env: no secrets
    states = {e.config.id: e.state for e in registry.entries()}

    assert states["fake"] is ProviderState.READY
    assert states["openrouter-default"] is ProviderState.DISABLED
    assert states["antigravity-headless"] is ProviderState.DISABLED
    assert settings.server.host == "127.0.0.1"  # safe-by-default

    provider = registry.resolve("fake")
    assert await registry.health("fake")
    result = await provider.execute(running_task("fake", user="ping"))
    assert "ping" in result.output_text
    assert result.artifacts  # shipped fake config emits a diff artifact
    await registry.aclose()
