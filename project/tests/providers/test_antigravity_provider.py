"""Unit tests for the AntigravityProvider adapter with dynamic tool decoupling."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.domain.enums import EventType, ExecutionMode
from app.domain.errors import (
    AuthenticationError,
    ProviderTimeoutError,
    RateLimitedError,
    TransientProviderError,
)
from app.domain.models import (
    AgentProviderConfig,
    MetricsEvent,
    TextDeltaEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.domain.tools import ToolDescriptor
from providers.adapters.antigravity import AntigravityProvider

from tests.factories import make_deps, running_task


def _make_provider(**options: object) -> AntigravityProvider:
    config = AgentProviderConfig(
        id="antigravity",
        kind="antigravity",
        display_name="Antigravity Headless",
        adapter="providers.adapters.antigravity:AntigravityProvider",
        options=options,
    )
    deps = make_deps()
    return AntigravityProvider(config, deps)


@pytest.mark.asyncio
async def test_health_check_detects_executable() -> None:
    provider = _make_provider(executable="agy")
    with patch("shutil.which", return_value="C:\\Fake\\agy.exe"):
        status = await provider.health_check()
        assert status.ok

    with patch("shutil.which", return_value=None):
        status_down = await provider.health_check()
        assert not status_down.ok
        assert "not found" in (status_down.reason or "")


@pytest.mark.asyncio
async def test_stream_execute_maps_ndjson_events() -> None:
    provider = _make_provider(executable="agy")
    task = running_task(user="what is this project?")

    # Simulated NDJSON lines from agy CLI
    ndjson_lines = [
        (
            b'{"event":"init","conversation_id":"c-123",'
            b'"init":{"tools":["view_file"],"permission_mode":"request-review"}}\n'
        ),
        (
            b'{"event":"step_update","step_update":{"step_type":"agent_response",'
            b'"thought_delta":"Analyzing codebase..."}}\n'
        ),
        (
            b'{"event":"step_update","step_update":{"step_type":"tool","state":"ACTIVE",'
            b'"tool_name":"view_file","tool_info":{"parameters":{"AbsolutePath":"README.md"}}}}\n'
        ),
        (
            b'{"event":"step_update","step_update":{"step_type":"tool","state":"DONE",'
            b'"tool_name":"view_file","tool_info":{"output":"Project README content"}}}\n'
        ),
        (
            b'{"event":"step_update","step_update":{"step_type":"agent_response",'
            b'"text_delta":"Vibe Forge is "}}\n'
        ),
        (
            b'{"event":"step_update","step_update":{"step_type":"agent_response",'
            b'"text_delta":"an AI execution platform."}}\n'
        ),
        (
            b'{"event":"result","result":{"status":"SUCCESS","duration_seconds":1.2,'
            b'"usage":{"input_tokens":120,"output_tokens":15}}}\n'
        ),
    ]

    mock_process = MagicMock()
    mock_process.returncode = 0

    async def mock_stdout() -> asyncio.StreamReader:
        reader = asyncio.StreamReader()
        for line in ndjson_lines:
            reader.feed_data(line)
        reader.feed_eof()
        return reader

    mock_process.stdout = await mock_stdout()
    mock_process.stderr = asyncio.StreamReader()
    mock_process.stderr.feed_eof()
    mock_process.wait = AsyncMock(return_value=0)

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        events = [e async for e in provider.stream_execute(task)]

    event_types = [e.type for e in events]
    assert EventType.STATUS in event_types
    assert EventType.THOUGHT in event_types
    assert EventType.TOOL_CALL in event_types
    assert EventType.TOOL_RESULT in event_types
    assert EventType.TOKEN in event_types
    assert EventType.USAGE in event_types
    assert EventType.DONE in event_types

    # Verify tool call details
    tool_call_event = next(e for e in events if e.type is EventType.TOOL_CALL)
    assert tool_call_event.data["name"] == "view_file"
    assert tool_call_event.data["args"]["AbsolutePath"] == "README.md"
    assert "raw_payload" in tool_call_event.data

    # Verify usage token economy (minimal tokens, not blown up)
    usage_event = next(e for e in events if e.type is EventType.USAGE)
    assert usage_event.data["prompt_tokens"] == 120
    assert usage_event.data["completion_tokens"] == 15


@pytest.mark.asyncio
async def test_direct_qa_mode_bypasses_tool_injection() -> None:
    provider = _make_provider(executable="agy")
    task = running_task(user="DIRECT_QA: What is 2+2?")

    mock_process = MagicMock()
    mock_process.returncode = 0

    async def mock_stdout() -> asyncio.StreamReader:
        reader = asyncio.StreamReader()
        reader.feed_data(
            b'{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"4"}}\n'
        )
        reader.feed_data(
            b'{"event":"result","result":{"status":"SUCCESS","usage":{"input_tokens":35,"output_tokens":2}}}\n'
        )
        reader.feed_eof()
        return reader

    mock_process.stdout = await mock_stdout()
    mock_process.stderr = asyncio.StreamReader()
    mock_process.stderr.feed_eof()
    mock_process.wait = AsyncMock(return_value=0)

    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        events = [e async for e in provider.stream_execute(task)]

        # Verify command launched without tool schema dump
        assert mock_exec.called
        call_args = mock_exec.call_args[0]
        assert "-p" in call_args
        assert any("DIRECT_QA: What is 2+2?" in str(a) for a in call_args)
        assert any("[CONSTRAINT: Direct answer only" in str(a) for a in call_args)
        assert "--mode" in call_args
        assert "plan" in call_args
        assert "--disable-slash-commands" in call_args
        assert "--effort" in call_args
        assert "low" in call_args

    status_event = next(e for e in events if e.type is EventType.STATUS)
    assert "direct_qa (0-tool)" in status_event.data.get("mode", "")

    tokens = [str(e.data.get("text", "")) for e in events if e.type is EventType.TOKEN]
    assert "".join(tokens) == "4"


@pytest.mark.asyncio
async def test_restricted_tools_maps_flags_and_decorates_prompt() -> None:
    provider = _make_provider(executable="agy")
    task = running_task(user="read the readme", extra={"tools": ["view_file"]})

    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.stdout = asyncio.StreamReader()
    mock_process.stdout.feed_data(b'{"event":"result","result":{"status":"SUCCESS"}}\n')
    mock_process.stdout.feed_eof()
    mock_process.stderr = asyncio.StreamReader()
    mock_process.stderr.feed_eof()
    mock_process.wait = AsyncMock(return_value=0)

    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        _ = [e async for e in provider.stream_execute(task)]
        assert mock_exec.called
        call_args = mock_exec.call_args[0]
        assert "--mode" in call_args
        assert "plan" in call_args
        assert "--disable-slash-commands" in call_args
        assert any("[ALLOWED TOOLS STRICT WHITELIST: view_file." in str(a) for a in call_args)


@pytest.mark.asyncio
async def test_write_tools_whitelist_allows_execution_mode() -> None:
    provider = _make_provider(executable="agy")
    task = running_task(user="edit the readme", extra={"tools": ["edit_file", "view_file"]})

    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.stdout = asyncio.StreamReader()
    mock_process.stdout.feed_data(b'{"event":"result","result":{"status":"SUCCESS"}}\n')
    mock_process.stdout.feed_eof()
    mock_process.stderr = asyncio.StreamReader()
    mock_process.stderr.feed_eof()
    mock_process.wait = AsyncMock(return_value=0)

    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        _ = [e async for e in provider.stream_execute(task)]
        assert mock_exec.called
        call_args = mock_exec.call_args[0]
        # Since edit_file is a write tool in whitelist, --mode plan should NOT be forced
        assert "--mode" not in call_args
        assert "--disable-slash-commands" in call_args
        assert any(
            "[ALLOWED TOOLS STRICT WHITELIST: edit_file, view_file." in str(a) for a in call_args
        )


@pytest.mark.asyncio
async def test_passthrough_arguments_forwarded() -> None:
    provider = _make_provider(executable="agy")
    task = running_task(
        user="what is this?",
        extra={"extra_args": ["--new-project", "--disable-slash-commands"]},
    )

    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.stdout = asyncio.StreamReader()
    mock_process.stdout.feed_data(b'{"event":"result","result":{"status":"SUCCESS"}}\n')
    mock_process.stdout.feed_eof()
    mock_process.stderr = asyncio.StreamReader()
    mock_process.stderr.feed_eof()
    mock_process.wait = AsyncMock(return_value=0)

    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        _ = [e async for e in provider.stream_execute(task)]
        assert mock_exec.called
        call_args = mock_exec.call_args[0]
        assert "--new-project" in call_args
        assert "--disable-slash-commands" in call_args


@pytest.mark.asyncio
async def test_universal_active_tools_contract() -> None:
    provider = _make_provider(executable="agy")
    task = running_task(
        user="read file",
        execution_mode=ExecutionMode.DYNAMIC_JIT,
        active_tools=(ToolDescriptor("read_file", "fs", "Read file"),),
    )

    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.stdout = asyncio.StreamReader()
    mock_process.stdout.feed_data(b'{"event":"result","result":{"status":"SUCCESS"}}\n')
    mock_process.stdout.feed_eof()
    mock_process.stderr = asyncio.StreamReader()
    mock_process.stderr.feed_eof()
    mock_process.wait = AsyncMock(return_value=0)

    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        _ = [e async for e in provider.stream_execute(task)]
        assert mock_exec.called
        call_args = mock_exec.call_args[0]
        assert "--mode" in call_args
        assert "plan" in call_args
        assert "--disable-slash-commands" in call_args
        assert any("[ALLOWED TOOLS STRICT WHITELIST: read_file." in str(a) for a in call_args)


@pytest.mark.asyncio
async def test_antigravity_telemetry_observer() -> None:
    provider = _make_provider(executable="agy")
    seen_telemetry: list[object] = []
    provider.add_observer(seen_telemetry.append)

    task = running_task(user="observe session")

    ndjson_lines = [
        b'{"event":"step_update","step_update":{"step_type":"agent_response",'
        b'"thought_delta":"pondering"}}\n',
        b'{"event":"step_update","step_update":{"step_type":"tool","state":"ACTIVE",'
        b'"tool_name":"view_file","tool_info":{"parameters":{"path":"a.py"}}}}\n',
        b'{"event":"step_update","step_update":{"step_type":"tool","state":"DONE",'
        b'"tool_name":"view_file","tool_info":{"output":"done"}}}\n',
        b'{"event":"step_update","step_update":{"step_type":"agent_response",'
        b'"text_delta":"result text"}}\n',
        b'{"event":"result","result":{"status":"SUCCESS",'
        b'"usage":{"input_tokens":50,"output_tokens":10}}}\n',
    ]
    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.stdout = asyncio.StreamReader()
    for line in ndjson_lines:
        mock_process.stdout.feed_data(line)
    mock_process.stdout.feed_eof()
    mock_process.stderr = asyncio.StreamReader()
    mock_process.stderr.feed_eof()
    mock_process.wait = AsyncMock(return_value=0)

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        _ = [e async for e in provider.stream_execute(task)]

    event_types = [type(e) for e in seen_telemetry]
    assert ThoughtEvent in event_types
    assert ToolCallEvent in event_types
    assert ToolResultEvent in event_types
    assert TextDeltaEvent in event_types
    assert MetricsEvent in event_types


@pytest.mark.asyncio
async def test_antigravity_sdk_mode() -> None:
    provider = _make_provider(mode="sdk")
    status = await provider.health_check()
    assert status.ok

    task = running_task(user="hello sdk")
    events = [e async for e in provider.stream_execute(task)]
    types = [e.type for e in events]
    assert EventType.STATUS in types
    assert EventType.TOKEN in types
    assert EventType.USAGE in types
    assert EventType.DONE in types
    assert any("Antigravity SDK" in str(e.data.get("text", "")) for e in events)


@pytest.mark.asyncio
async def test_antigravity_error_taxonomy_mapping() -> None:
    provider = _make_provider(executable="agy")
    task = running_task(user="fail please")

    # Test rate limit error
    mock_process = MagicMock()
    mock_process.returncode = 1
    mock_process.stdout = asyncio.StreamReader()
    mock_process.stdout.feed_eof()
    mock_process.stderr = asyncio.StreamReader()
    mock_process.stderr.feed_data(b"Error: 429 rate limit exceeded\n")
    mock_process.stderr.feed_eof()
    mock_process.wait = AsyncMock(return_value=1)

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_process),
        pytest.raises(RateLimitedError),
    ):
        _ = [e async for e in provider.stream_execute(task)]

    # Test authentication error
    mock_process.stderr = asyncio.StreamReader()
    mock_process.stderr.feed_data(b"Error: 401 unauthorized or permission denied\n")
    mock_process.stderr.feed_eof()
    mock_process.stdout = asyncio.StreamReader()
    mock_process.stdout.feed_eof()
    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_process),
        pytest.raises(AuthenticationError),
    ):
        _ = [e async for e in provider.stream_execute(task)]

    # Test timeout error
    mock_process.stderr = asyncio.StreamReader()
    mock_process.stderr.feed_data(b"Error: connection timed out\n")
    mock_process.stderr.feed_eof()
    mock_process.stdout = asyncio.StreamReader()
    mock_process.stdout.feed_eof()
    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_process),
        pytest.raises(ProviderTimeoutError),
    ):
        _ = [e async for e in provider.stream_execute(task)]

    # Test generic transient error
    mock_process.stderr = asyncio.StreamReader()
    mock_process.stderr.feed_data(b"Error: unexpected internal failure\n")
    mock_process.stderr.feed_eof()
    mock_process.stdout = asyncio.StreamReader()
    mock_process.stdout.feed_eof()
    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_process),
        pytest.raises(TransientProviderError),
    ):
        _ = [e async for e in provider.stream_execute(task)]


@pytest.mark.asyncio
async def test_antigravity_direct_qa_mode_cli_flags() -> None:
    provider = _make_provider(executable="agy")
    task = running_task(
        user="hi",
        execution_mode=ExecutionMode.DIRECT_QA,
        extra={"direct_qa": True},
    )

    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.stdout = asyncio.StreamReader()
    mock_process.stdout.feed_data(b'{"event":"result","result":{"status":"SUCCESS"}}\n')
    mock_process.stdout.feed_eof()
    mock_process.stderr = asyncio.StreamReader()
    mock_process.stderr.feed_eof()
    mock_process.wait = AsyncMock(return_value=0)

    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        _ = [e async for e in provider.stream_execute(task)]
        assert mock_exec.called
        call_args = list(mock_exec.call_args[0])
        # Verify: --mode plan --disable-slash-commands --effort low
        assert "--mode" in call_args
        mode_idx = call_args.index("--mode")
        assert call_args[mode_idx + 1] == "plan"
        assert "--disable-slash-commands" in call_args
        assert "--effort" in call_args
        effort_idx = call_args.index("--effort")
        assert call_args[effort_idx + 1] == "low"
        # Constraint in prompt
        assert any("[CONSTRAINT: Direct answer only." in str(a) for a in call_args)


@pytest.mark.asyncio
async def test_antigravity_popen_fallback_on_not_implemented_error() -> None:
    provider = _make_provider(executable="agy")
    task = running_task(user="hi")

    mock_popen = MagicMock()
    mock_popen.returncode = 0
    mock_popen.stdout.readline.side_effect = [
        b'{"event":"init","init":{"tools":[]}}\n',
        b'{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"Hello!"}}\n',
        b'{"event":"result","result":{"status":"SUCCESS"}}\n',
        b"",
    ]
    mock_popen.stderr.readline.side_effect = [b""]
    mock_popen.wait.return_value = 0

    with (
        patch(
            "asyncio.create_subprocess_exec",
            side_effect=NotImplementedError("Windows selector loop"),
        ),
        patch("subprocess.Popen", return_value=mock_popen) as mock_popen_cls,
    ):
        events = [e async for e in provider.stream_execute(task)]
        assert mock_popen_cls.called
        event_types = [e.type for e in events]
        assert EventType.STATUS in event_types
        assert EventType.TOKEN in event_types
        assert EventType.DONE in event_types
        assert any("Hello!" in str(e.data.get("text", "")) for e in events)
