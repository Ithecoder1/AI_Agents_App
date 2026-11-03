"""CLI Command and Prompt Construction for Antigravity Adapter."""

from __future__ import annotations

from typing import Any

WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "edit_file",
        "write_file",
        "write_to_file",
        "replace_file_content",
        "multi_replace_file_content",
        "create_file",
    }
)


class AntigravityCommandBuilder:
    """Builds the native CLI command line arguments and decorates prompts for `agy`."""

    @staticmethod
    def decorate_prompt(user_prompt: str, is_direct_qa: bool, target_tools: set[str]) -> str:
        """Decorate user prompt with direct-QA or tool whitelist constraints."""
        final_prompt = user_prompt
        if is_direct_qa:
            constraint = (
                "[CONSTRAINT: Direct answer only. "
                "Do NOT use any tools, file operations, or commands.]"
            )
            if constraint not in final_prompt:
                final_prompt = f"{constraint}\n\n{final_prompt}"
        elif target_tools:
            tools_str = ", ".join(sorted(target_tools))
            boundary = (
                f"[ALLOWED TOOLS STRICT WHITELIST: {tools_str}. "
                "Calling any unlisted tool is strictly forbidden.]"
            )
            if boundary not in final_prompt:
                final_prompt = f"{boundary}\n\n{final_prompt}"
        return final_prompt

    @classmethod
    def build_command(
        cls,
        executable: str,
        final_prompt: str,
        *,
        is_direct_qa: bool,
        target_tools: set[str],
        skip_permissions: bool,
        extra_args: list[str],
        mode: Any,
    ) -> list[str]:
        """Construct the complete CLI argument list for the `agy` process."""
        import sys

        if sys.platform == "win32" and executable.lower().endswith((".cmd", ".bat")):
            cmd = [
                "cmd.exe",
                "/c",
                executable,
                "-p",
                final_prompt,
                "--output-format",
                "stream-json",
            ]
        else:
            cmd = [executable, "-p", final_prompt, "--output-format", "stream-json"]

        if skip_permissions:
            cmd.append("--dangerously-skip-permissions")

        effort_flags: list[str] = []
        if "--effort" not in extra_args:
            effort_flags = ["--effort", "low"]

        has_write_tools = bool(target_tools & WRITE_TOOLS)

        if is_direct_qa or (mode == "plan"):
            cmd.extend(["--mode", "plan", "--disable-slash-commands", *effort_flags])
        elif target_tools:
            if not has_write_tools:
                cmd.extend(["--mode", "plan", "--disable-slash-commands"])
            else:
                cmd.append("--disable-slash-commands")
        elif mode:
            cmd.extend(["--mode", str(mode)])

        if extra_args:
            i = 0
            while i < len(extra_args):
                arg = extra_args[i]
                if (
                    arg in ("--disable-slash-commands", "--dangerously-skip-permissions")
                    and arg in cmd
                ):
                    i += 1
                    continue
                if arg in ("--mode", "--effort") and arg in cmd:
                    i += 2
                    continue
                cmd.append(arg)
                i += 1

        return cmd
