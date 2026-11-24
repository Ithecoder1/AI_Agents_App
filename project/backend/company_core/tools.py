"""DEPRECATED: Compatibility shim for local workspace execution tools.

Prefer ``app.infrastructure.orchestration.LocalCodeWorkspaceRunner``.
"""

from __future__ import annotations

__deprecated__ = True

from collections.abc import Mapping
from pathlib import Path

from app.infrastructure.orchestration.adapters import (
    LocalCodeWorkspaceRunner,
    PytestSubprocessRunner,
    WorkspaceFileManager,
)

_FILE_MANAGER = WorkspaceFileManager()
_PYTEST_RUNNER = PytestSubprocessRunner()


def write_project_files(target_dir: Path, files: Mapping[str, str]) -> list[str]:
    return _FILE_MANAGER.write_files(target_dir, files)


def read_project_files(
    target_dir: Path,
    extensions: tuple[str, ...] = (".py", ".json", ".yaml", ".yml", ".md", ".txt"),
) -> dict[str, str]:
    return _FILE_MANAGER.read_files(target_dir, extensions=extensions)


def summarize_code_for_context(
    files: Mapping[str, str],
    max_lines_per_file: int = 50,
    strip_comments: bool = True,
) -> str:
    return _FILE_MANAGER.summarize_code(
        files,
        max_lines_per_file=max_lines_per_file,
        strip_comments=strip_comments,
    )


def extract_clean_traceback(raw_output: str, max_chars: int = 4000) -> str:
    return _PYTEST_RUNNER.extract_clean_traceback(raw_output, max_chars=max_chars)


def run_pytest_suite(target_dir: Path, timeout_s: float = 30.0) -> tuple[bool, str, str]:
    return _PYTEST_RUNNER.run_tuple(target_dir, timeout_s=timeout_s)


__all__ = [
    "LocalCodeWorkspaceRunner",
    "PytestSubprocessRunner",
    "WorkspaceFileManager",
    "extract_clean_traceback",
    "read_project_files",
    "run_pytest_suite",
    "summarize_code_for_context",
    "write_project_files",
]
