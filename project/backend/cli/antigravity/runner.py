"""Antigravity CLI runner main function."""

from __future__ import annotations

import asyncio

from .parser import parse_args
from .stream_runner import run_agy_stream


def run_cli_main() -> int:
    """CLI entrypoint invoked by antigravity.py."""
    args, extra_args = parse_args()

    mode = "direct_qa" if args.direct_qa else ("dynamic" if args.dynamic or args.tools else "full")
    requested_tools = (
        [t.strip() for t in args.tools.split(",") if t.strip()] if args.tools else None
    )

    target_prompt = args.print_prompt if args.print_prompt is not None else args.prompt

    if not target_prompt and not extra_args:
        if not args.no_metrics:
            from .metrics import print_full_context_payload, resolve_cli_turn_metrics

            metrics = resolve_cli_turn_metrics(
                tool_mode=mode,
                requested_tools=requested_tools,
                system_instructions=args.system_instructions,
            )
            print_full_context_payload(
                system_instructions=metrics.system_instructions,
                tools=metrics.active_tools,
                tools_count=metrics.active_tools_count,
                used_files=metrics.used_files,
                injected_schemas=metrics.injected_schemas,
                verbose=args.verbose,
            )
        return 0

    return asyncio.run(
        run_agy_stream(
            prompt=target_prompt,
            tool_mode=mode,
            requested_tools=requested_tools,
            tools_str=args.tools,
            extra_passthrough_args=extra_args,
            mode=args.mode,
            verbose=args.verbose,
            log_file=args.log_file,
            skip_permissions=args.dangerously_skip_permissions,
            system_instructions=args.system_instructions,
            show_metrics=not args.no_metrics,
            output_format=args.output_format,
        )
    )
