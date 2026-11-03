"""Command line argument parser for Antigravity runner."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for Antigravity CLI."""
    parser = argparse.ArgumentParser(
        description="Antigravity Headless Runner with Dynamic Tool Retrieval",
    )
    parser.add_argument("prompt", nargs="?", default="", help="Target prompt for the agent")
    parser.add_argument(
        "-p",
        "--print",
        dest="print_prompt",
        type=str,
        default=None,
        help="Target prompt for the agent (alias for positional prompt)",
    )
    parser.add_argument(
        "--output-format",
        choices=["text", "json", "stream-json"],
        default="text",
        help="Output format for agy execution (text, json, stream-json)",
    )
    parser.add_argument(
        "--direct-qa",
        action="store_true",
        help="Bypass tool injection entirely (0-tool pure text mode)",
    )
    parser.add_argument(
        "--dynamic",
        action="store_true",
        help="Use Dynamic Tool Retrieval (lean baseline with search_available_tools)",
    )
    parser.add_argument(
        "--tools",
        type=str,
        default="",
        help=(
            "Comma-separated list of explicit tools to activate upfront "
            "(e.g. view_file,write_to_file)"
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["accept-edits", "auto", "plan"],
        default=None,
        help="Set the agent execution mode for this session (accept-edits, auto, plan)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print verbose streaming events and raw JSON payloads directly to console",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="File path to save the complete session interaction trace and raw event logs",
    )
    parser.add_argument(
        "--dangerously-skip-permissions",
        "--skip-permissions",
        action="store_true",
        help="Auto-approve all tool permission requests",
    )
    parser.add_argument(
        "--system",
        "--system-instructions",
        dest="system_instructions",
        type=str,
        default=None,
        help="Optional system instructions override for this session",
    )
    parser.add_argument(
        "--no-metrics",
        action="store_true",
        help="Suppress printing turn metrics to terminal",
    )
    return parser


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    """Parse known arguments and return (args, extra_passthrough_args)."""
    parser = build_parser()
    return parser.parse_known_args()
