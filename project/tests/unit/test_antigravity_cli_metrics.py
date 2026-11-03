"""Unit tests for Antigravity CLI turn metrics and telemetry."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.application.tools import DynamicToolRegistry
from app.domain.tools import ToolDescriptor
from cli.antigravity import (
    CliTurnMetrics,
    build_parser,
    format_turn_metrics,
    resolve_cli_turn_metrics,
    run_agy_stream,
)


def _make_sample_registry() -> DynamicToolRegistry:
    t1 = ToolDescriptor(
        name="view_file",
        category="filesystem",
        summary="View file contents",
        schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    t2 = ToolDescriptor(
        name="list_dir",
        category="filesystem",
        summary="List directory entries",
        schema={"type": "object", "properties": {"dir": {"type": "string"}}},
    )
    return DynamicToolRegistry([t1, t2])


def test_resolve_metrics_direct_qa() -> None:
    reg = _make_sample_registry()
    metrics = resolve_cli_turn_metrics(tool_mode="direct_qa", registry=reg)

    assert metrics.active_tools_count == 0
    assert metrics.active_tools == ()
    assert metrics.injected_schemas == ()
    assert "Direct answer only" in metrics.system_instructions


def test_resolve_metrics_explicit_tools() -> None:
    reg = _make_sample_registry()
    metrics = resolve_cli_turn_metrics(
        tool_mode="manual",
        requested_tools=["view_file", "list_dir"],
        system_instructions="You are a helpful assistant.",
        registry=reg,
    )

    assert metrics.active_tools_count == 2
    assert "view_file" in metrics.active_tools
    assert "list_dir" in metrics.active_tools
    assert len(metrics.injected_schemas) == 2
    assert "STRICT WHITELIST" in metrics.system_instructions
    assert "You are a helpful assistant." in metrics.system_instructions


def test_resolve_metrics_dynamic_mode() -> None:
    reg = _make_sample_registry()
    metrics = resolve_cli_turn_metrics(tool_mode="dynamic", registry=reg)

    assert metrics.active_tools_count == 1
    assert "search_available_tools" in metrics.active_tools
    assert "DYNAMIC TOOLS" in metrics.system_instructions


def test_format_turn_metrics() -> None:
    metrics = CliTurnMetrics(
        system_instructions="Custom prompt",
        active_tools=("view_file",),
        active_tools_count=1,
        injected_schemas=({"name": "view_file", "category": "fs", "description": "view"},),
    )

    req_formatted = format_turn_metrics(metrics, phase="REQUEST", verbose=True)
    assert "[REQUEST TURN METRICS]" in req_formatted
    assert "view_file" in req_formatted
    assert "Custom prompt" in req_formatted

    res_formatted = format_turn_metrics(
        metrics, phase="RESPONSE", usage={"input_tokens": 10, "output_tokens": 5}
    )
    assert "[RESPONSE TURN METRICS]" in res_formatted
    assert "input=10, output=5" in res_formatted


def test_cli_parser_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "test prompt",
            "--tools",
            "view_file,list_dir",
            "--system",
            "Follow rules",
            "--no-metrics",
        ]
    )
    assert args.prompt == "test prompt"
    assert args.tools == "view_file,list_dir"
    assert args.system_instructions == "Follow rules"
    assert args.no_metrics is True


@pytest.mark.asyncio
async def test_run_agy_stream_with_metrics_and_logs(tmp_path: Path) -> None:
    log_file = tmp_path / "cli_session.log"

    mock_process = MagicMock()
    mock_process.returncode = 0

    async def mock_stdout() -> asyncio.StreamReader:
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"event":"step_update","step_update":{"text_delta":"Hello back!"}}\n')
        reader.feed_data(
            b'{"event":"result","result":{"usage":{"input_tokens":5,"output_tokens":2}}}\n'
        )
        reader.feed_eof()
        return reader

    mock_process.stdout = await mock_stdout()
    mock_process.stderr = asyncio.StreamReader()
    mock_process.stderr.feed_eof()
    mock_process.wait = AsyncMock(return_value=0)

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        rc = await run_agy_stream(
            prompt="Hello agent",
            tool_mode="direct_qa",
            log_file=log_file,
            show_metrics=False,
        )

    assert rc == 0
    assert log_file.is_file()
    log_content = log_file.read_text(encoding="utf-8")
    assert "REQUEST_METRICS" in log_content
    assert "RESPONSE_METRICS" in log_content
    assert "RESPONSE_DELTA" in log_content


def test_print_full_context_payload(capsys: pytest.CaptureFixture[str]) -> None:
    from cli.antigravity import print_full_context_payload

    print_full_context_payload(
        system_instructions="Direct answer only",
        tools=["view_file", "list_dir"],
        tools_count=2,
        used_files=["main.py", "config.py"],
        total_duration=3.72,
        usage={
            "input_tokens": 15746,
            "output_tokens": 1003,
            "thinking_tokens": 114,
            "cache_read_tokens": 18794,
            "total_tokens": 16749,
        },
    )
    captured = capsys.readouterr().out
    assert "=" * 40 + " [FULL CONTEXT PAYLOAD] " + "=" * 40 in captured
    assert "System Instructions: Direct answer only" in captured
    assert "Active Tools: ['view_file', 'list_dir']" in captured
    assert "Active Tools Count: 2" in captured
    assert "Used Files: ['main.py', 'config.py']" in captured
    assert "Used Files Count: 2" in captured
    assert "Total Duration: 3.72s" in captured
    expected_usage = (
        "Usage Metrics: {"
        "'input_tokens': 15746, "
        "'output_tokens': 1003, "
        "'thinking_tokens': 114, "
        "'cache_read_tokens': 18794, "
        "'total_tokens': 16749}"
    )
    assert expected_usage in captured
    assert "=" * 100 in captured


def test_extract_file_paths() -> None:
    from cli.antigravity.stream_runner import extract_file_paths

    collector: list[str] = []
    payload = {
        "step_type": "tool",
        "tool_info": {
            "name": "view_file",
            "parameters": {
                "AbsolutePath": "C:\\Dev\\AI-Agents-App\\main.py",
                "other": 123,
            },
        },
    }
    extract_file_paths(payload, collector)
    assert collector == ["C:\\Dev\\AI-Agents-App\\main.py"]

    # Deduplication test
    extract_file_paths({"file_path": "C:\\Dev\\AI-Agents-App\\main.py"}, collector)
    assert len(collector) == 1


@pytest.mark.asyncio
async def test_run_agy_stream_formats(capsys: pytest.CaptureFixture[str]) -> None:
    from cli.antigravity import run_agy_stream

    mock_process = MagicMock()
    mock_process.returncode = 0

    async def mock_stdout() -> asyncio.StreamReader:
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"event":"init","init":{"tools":["view_file"]}}\n')
        reader.feed_data(
            b'{"event":"step_update","step_update":{"thought_delta":"pondering..."}}\n'
        )
        reader.feed_data(b'{"event":"step_update","step_update":{"text_delta":"Got answer"}}\n')
        reader.feed_data(b'{"event":"result","result":{"usage":{}}}\n')
        reader.feed_eof()
        return reader

    mock_process.stdout = await mock_stdout()
    mock_process.stderr = asyncio.StreamReader()
    mock_process.stderr.feed_eof()
    mock_process.wait = AsyncMock(return_value=0)

    # 1. Test text format (shows [Thinking], [Response])
    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        await run_agy_stream("test", output_format="text", show_metrics=False)

    out = capsys.readouterr().out
    assert "[Thinking] pondering..." in out
    assert "[Response]" in out
    assert "Got answer" in out

    # 2. Test stream-json format (shows raw JSON lines)
    mock_process.stdout = await mock_stdout()
    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        await run_agy_stream("test", output_format="stream-json", show_metrics=False)

    out2 = capsys.readouterr().out
    assert '{"event":"step_update","step_update":{"thought_delta":"pondering..."}}' in out2
