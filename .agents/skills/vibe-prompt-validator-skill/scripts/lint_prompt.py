#!/usr/bin/env python3
"""CLI utility for linting prompt files and intentions against Vibe Forge best practices.

Outputs a JSON LintReport containing a score (0-100), pass/fail status, token estimate,
and prioritized actionable issues.

Usage:
  python lint_prompt.py --prompt "Your prompt text here" [--system "System instructions"]
  python lint_prompt.py --file path/to/prompt.txt [--system-file path/to/system.txt]
  python lint_prompt.py --stdin
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add backend directory to path if running directly from script location
repo_root = Path(__file__).resolve().parents[4]
backend_dir = repo_root / "project" / "backend" / "app"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.application.prompt_linter import lint_prompt
from app.domain.models import Prompt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prompt", help="Raw user prompt string")
    group.add_argument("--file", type=Path, help="File containing user prompt")
    group.add_argument("--stdin", action="store_true", help="Read prompt from standard input")

    parser.add_argument("--system", default="", help="Raw system instructions string")
    parser.add_argument("--system-file", type=Path, help="File containing system instructions")
    parser.add_argument("--max-tokens", type=int, help="Maximum allowed token limit")

    args = parser.parse_args(argv)

    if args.stdin:
        user_prompt = sys.stdin.read()
    elif args.file:
        user_prompt = args.file.read_text(encoding="utf-8")
    else:
        user_prompt = args.prompt

    if args.system_file:
        system_instructions = args.system_file.read_text(encoding="utf-8")
    else:
        system_instructions = args.system

    prompt_obj = Prompt(user_prompt=user_prompt, system_instructions=system_instructions)
    report = lint_prompt(prompt_obj, max_tokens=args.max_tokens)

    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
