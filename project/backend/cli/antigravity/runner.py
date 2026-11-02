"""Antigravity CLI runner main function."""

from __future__ import annotations

import asyncio

from .parser import parse_args
from .stream_runner import run_agy_stream


def run_cli_main() -> int:
    """CLI entrypoint invoked by antigravity.py."""
    args, extra_args = parse_args()

    if not args.prompt and not extra_args:
        return 0

    mode = "direct_qa" if args.direct_qa else ("dynamic" if args.dynamic or args.tools else "full")
    requested_tools = (
        [t.strip() for t in args.tools.split(",") if t.strip()] if args.tools else None
    )

    return asyncio.run(
        run_agy_stream(
            prompt=args.prompt,
            tool_mode=mode,
            requested_tools=requested_tools,
            tools_str=args.tools,
            extra_passthrough_args=extra_args,
            mode=args.mode,
            verbose=args.verbose,
            log_file=args.log_file,
            skip_permissions=args.dangerously_skip_permissions,
        )
    )
