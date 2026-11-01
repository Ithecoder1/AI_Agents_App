"""Local Execution Tools & Context Hygiene Utilities."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def write_project_files(target_dir: Path, files: dict[str, str]) -> list[str]:
    """Write all files from the dictionary to target_dir. Returns list of written relative paths."""
    written: list[str] = []
    target_dir.mkdir(parents=True, exist_ok=True)
    for rel_path, content in files.items():
        # Sanitize path to prevent directory traversal
        norm_rel = os.path.normpath(rel_path).lstrip("/\\")
        dest = target_dir / norm_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        written.append(norm_rel)
    return written


def read_project_files(
    target_dir: Path,
    extensions: tuple[str, ...] = (".py", ".json", ".yaml", ".yml", ".md", ".txt"),
) -> dict[str, str]:
    """Read all text files in target_dir matching extensions."""
    result: dict[str, str] = {}
    if not target_dir.is_dir():
        return result
    for path in target_dir.rglob("*"):
        if path.is_file() and any(path.name.endswith(ext) for ext in extensions):
            if any(
                part in (".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache")
                for part in path.parts
            ):
                continue
            rel = str(path.relative_to(target_dir)).replace("\\", "/")
            try:
                result[rel] = path.read_text(encoding="utf-8")
            except OSError:
                pass
    return result


def summarize_code_for_context(
    files: dict[str, str],
    max_lines_per_file: int = 50,
    strip_comments: bool = True,
) -> str:
    """Token Hygiene: Render an outline/summary of project files instead of dumping entire codebases."""
    if not files:
        return "No code files implemented yet."

    sections: list[str] = []
    for filepath, content in sorted(files.items()):
        lines = content.splitlines()
        total_lines = len(lines)

        if total_lines <= max_lines_per_file:
            rendered = "\n".join(lines)
        else:
            # Extract signatures (def, class, import) plus head and tail
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

        sections.append(
            f"### File: {filepath} ({total_lines} lines)\n```python\n{rendered}\n```"
        )

    return "\n\n".join(sections)


def extract_clean_traceback(raw_output: str, max_lines: int = 40) -> str:
    """Extract diagnostic error traceback from pytest output, stripping noise."""
    if not raw_output:
        return "No test failure details available."

    lines = raw_output.splitlines()
    # Find failure sections
    failure_idx = -1
    for i, line in enumerate(lines):
        if "=== FAILURES ===" in line or "=== ERRORS ===" in line:
            failure_idx = i
            break

    if failure_idx != -1:
        extracted = lines[failure_idx:]
    else:
        # Look for traceback or assertion keywords
        filtered = [
            l
            for l in lines
            if any(
                k in l
                for k in ("Error", "AssertionError", "FAILED", "Traceback", "Exception")
            )
        ]
        extracted = filtered or lines[-max_lines:]

    if len(extracted) > max_lines:
        extracted = extracted[-max_lines:]

    return "\n".join(extracted)


def run_pytest_suite(
    target_dir: Path,
    python_executable: str | None = None,
    timeout_s: float = 30.0,
) -> tuple[bool, str, str | None]:
    """Execute pytest on target_dir, returning (success, stdout/summary, clean_traceback)."""
    # Locate python executable
    py = python_executable
    if not py:
        # Check current venv
        venv_py = (
            Path(__file__).resolve().parents[1]
            / "project"
            / "backend"
            / ".venv"
            / "Scripts"
            / "python.exe"
        )
        if venv_py.is_file():
            py = str(venv_py)
        else:
            py = sys.executable

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
