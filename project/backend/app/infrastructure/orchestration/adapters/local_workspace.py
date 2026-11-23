"""Local workspace filesystem and pytest execution adapter implementing ICodeWorkspaceRunner."""

from __future__ import annotations

import contextlib
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from app.domain.ports.orchestrator import TestRunResult

_EXCLUDED_DIR_PARTS: frozenset[str] = frozenset(
    {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache"}
)


def _resolve_contained_path(
    base_dir: Path,
    relative_path: str,
    *,
    allow_root: bool = False,
) -> tuple[Path, Path]:
    """Validate *relative_path* stays strictly inside *base_dir* and return ``(abs, rel)``."""
    cleaned = relative_path.strip()
    if not cleaned:
        raise ValueError("Relative path must not be empty")

    candidate_input = Path(cleaned)
    if (
        cleaned.startswith(("/", "\\"))
        or candidate_input.is_absolute()
        or bool(candidate_input.drive)
    ):
        raise ValueError(f"Absolute paths are not allowed: {relative_path!r}")

    root = base_dir.resolve()
    resolved = (root / candidate_input).resolve()
    try:
        rel = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path {relative_path!r} escapes workspace root {str(root)!r}") from exc

    if not allow_root and rel == Path("."):
        raise ValueError(f"Path {relative_path!r} must refer to a child entry")

    return resolved, rel


def write_project_files(target_dir: Path, files: Mapping[str, str]) -> list[str]:
    """Write all files from *files* inside *target_dir*, guarding against path traversal."""
    written: list[str] = []
    target_dir.mkdir(parents=True, exist_ok=True)
    for rel_path, content in files.items():
        dest, rel = _resolve_contained_path(target_dir, rel_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        written.append(str(rel))
    return written


def read_project_files(
    target_dir: Path,
    extensions: tuple[str, ...] = (".py", ".json", ".yaml", ".yml", ".md", ".txt"),
) -> dict[str, str]:
    """Read all matching text files contained inside *target_dir*."""
    result: dict[str, str] = {}
    if not target_dir.is_dir():
        return result
    root = target_dir.resolve()
    for path in root.rglob("*"):
        if not path.is_file() or not any(path.name.endswith(ext) for ext in extensions):
            continue
        if any(part in _EXCLUDED_DIR_PARTS for part in path.parts):
            continue
        try:
            rel_path = path.resolve().relative_to(root)
        except ValueError:
            continue
        rel = rel_path.as_posix()
        with contextlib.suppress(OSError):
            result[rel] = path.read_text(encoding="utf-8")
    return result


def summarize_code_for_context(
    files: Mapping[str, str],
    max_lines_per_file: int = 50,
    strip_comments: bool = True,
) -> str:
    """Render a compact outline/summary of project files for prompt hygiene."""
    _ = strip_comments
    if not files:
        return "No code files implemented yet."

    sections: list[str] = []
    for filepath, content in sorted(files.items()):
        lines = content.splitlines()
        total_lines = len(lines)

        if total_lines <= max_lines_per_file:
            rendered = "\n".join(lines)
        else:
            key_lines: list[str] = []
            head = lines[:15]
            tail = lines[-10:]
            middle = lines[15:-10]

            key_lines.extend(head)
            key_lines.append(
                f"\n# ... [collapsed {len(middle)} lines; outlining signatures below] ...\n"
            )

            for line in middle:
                stripped = line.strip()
                if stripped.startswith(("def ", "class ", "async def ", "@")):
                    key_lines.append(line)

            key_lines.extend(tail)
            rendered = "\n".join(key_lines)

        sections.append(f"### File: {filepath} ({total_lines} lines)\n```python\n{rendered}\n```")

    return "\n\n".join(sections)


def extract_clean_traceback(raw_output: str, max_lines: int = 40) -> str:
    """Extract diagnostic error traceback from pytest output, stripping header noise."""
    if not raw_output:
        return "No test failure details available."

    lines = raw_output.splitlines()
    failure_idx = -1
    for i, line in enumerate(lines):
        if "=== FAILURES ===" in line or "=== ERRORS ===" in line:
            failure_idx = i
            break

    if failure_idx != -1:
        extracted = lines[failure_idx:]
    else:
        filtered = [
            line
            for line in lines
            if any(
                k in line for k in ("Error", "AssertionError", "FAILED", "Traceback", "Exception")
            )
        ]
        extracted = filtered or lines[-max_lines:]

    if len(extracted) > max_lines:
        extracted = extracted[-max_lines:]

    return "\n".join(extracted)


def _resolve_python_executable(python_executable: str | None = None) -> str:
    if python_executable:
        return python_executable
    for parent in Path(__file__).resolve().parents:
        for candidate in (
            parent / ".venv" / "Scripts" / "python.exe",
            parent / ".venv" / "bin" / "python",
        ):
            if candidate.is_file():
                return str(candidate)
    return sys.executable


def run_pytest_suite(
    target_dir: Path,
    python_executable: str | None = None,
    timeout_s: float = 30.0,
) -> tuple[bool, str, str | None]:
    """Execute pytest on *target_dir*, returning ``(success, combined_output, traceback)``."""
    py = _resolve_python_executable(python_executable)
    cmd = [py, "-m", "pytest", str(target_dir), "-v", "--tb=short"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(target_dir),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        combined = f"{proc.stdout}\n{proc.stderr}".strip()
        success = proc.returncode == 0
        traceback_summary = None if success else extract_clean_traceback(combined)
        return success, combined, traceback_summary
    except subprocess.TimeoutExpired:
        msg = f"Test execution timed out after {timeout_s}s"
        return False, msg, msg
    except (subprocess.SubprocessError, OSError) as exc:
        msg = f"Failed to run pytest: {exc}"
        return False, msg, msg


class LocalCodeWorkspaceRunner:
    """Workspace adapter providing contained file operations and pytest execution."""

    def __init__(
        self,
        base_dir: Path | str | None = None,
        *,
        python_executable: str | None = None,
        default_timeout_s: float = 30.0,
    ) -> None:
        self._base_dir = (
            Path(base_dir).resolve()
            if base_dir is not None
            else (Path.cwd() / "projects").resolve()
        )
        self._python_executable = python_executable
        self._default_timeout_s = default_timeout_s

    @property
    def base_dir(self) -> Path:
        """Resolved root directory containing isolated project workspaces."""
        return self._base_dir

    def resolve_project_dir(self, project_id: str) -> Path:
        """Resolve and create a contained workspace directory for *project_id*."""
        self._base_dir.mkdir(parents=True, exist_ok=True)
        project_dir, _ = _resolve_contained_path(self._base_dir, project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        return project_dir

    def write_files(self, target_dir: Path, files: Mapping[str, str]) -> list[str]:
        """Write *files* safely inside *target_dir*."""
        return write_project_files(target_dir, files)

    def read_files(
        self,
        target_dir: Path,
        extensions: tuple[str, ...] = (".py", ".json", ".yaml", ".yml", ".md", ".txt"),
    ) -> dict[str, str]:
        """Read matching files contained inside *target_dir*."""
        return read_project_files(target_dir, extensions=extensions)

    def summarize_code(
        self,
        files: Mapping[str, str],
        max_lines_per_file: int = 50,
        strip_comments: bool = True,
    ) -> str:
        """Summarize source files for token-hygienic prompts."""
        return summarize_code_for_context(
            files,
            max_lines_per_file=max_lines_per_file,
            strip_comments=strip_comments,
        )

    def run_tests(self, target_dir: Path, timeout_s: float | None = None) -> TestRunResult:
        """Run pytest inside *target_dir* and return a structured ``TestRunResult``."""
        effective_timeout = self._default_timeout_s if timeout_s is None else timeout_s
        success, combined, clean_tb = run_pytest_suite(
            target_dir,
            python_executable=self._python_executable,
            timeout_s=effective_timeout,
        )
        return TestRunResult(
            success=success,
            combined_output=combined,
            clean_traceback=clean_tb,
        )


__all__ = [
    "LocalCodeWorkspaceRunner",
    "extract_clean_traceback",
    "read_project_files",
    "run_pytest_suite",
    "summarize_code_for_context",
    "write_project_files",
]
