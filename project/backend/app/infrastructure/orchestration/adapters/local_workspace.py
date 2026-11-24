"""Object-oriented local workspace file manager, pytest runner, and ICodeWorkspaceRunner adapter."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

from app.domain.ports import TestRunResult

_DEFAULT_EXTENSIONS: tuple[str, ...] = (".py", ".json", ".yaml", ".yml", ".md", ".txt")
_IGNORED_DIRS: frozenset[str] = frozenset(
    {".git", "__pycache__", ".venv", "venv", ".pytest_cache", "node_modules", ".mypy_cache"}
)
_NOISE_LINE_PREFIXES: tuple[str, ...] = (
    "============================= test session starts",
    "platform ",
    "rootdir:",
    "configfile:",
    "plugins:",
    "cachedir:",
    "collecting ",
    "collected ",
)


class WorkspaceFileManager:
    """Manages contained local workspace paths, atomic file R/W, and context summaries."""

    def __init__(self, base_workspace_dir: Path | str | None = None) -> None:
        self._base_workspace_dir = (
            Path(base_workspace_dir)
            if base_workspace_dir is not None
            else Path("company_workspace")
        )

    @property
    def base_workspace_dir(self) -> Path:
        """Return the base root directory containing project workspaces."""
        return self._base_workspace_dir

    def resolve_project_dir(self, project_id: str) -> Path:
        """Sanitize ``project_id`` and resolve its contained directory under workspace root."""
        raw = project_id.strip()
        if not raw or ".." in Path(raw).parts or Path(raw).is_absolute():
            raise ValueError(f"Unsafe project_id for workspace resolution: {project_id!r}")
        sanitized = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw).strip("-.")
        if not sanitized:
            raise ValueError(f"Empty sanitized project_id from: {project_id!r}")

        base = self._base_workspace_dir.resolve()
        candidate = (base / sanitized).resolve()
        if not candidate.is_relative_to(base):
            raise ValueError(f"Project directory escaped workspace root: {project_id!r}")
        return candidate

    @staticmethod
    def _resolve_contained_path(target_dir: Path, rel_path: str) -> Path:
        candidate = Path(rel_path)
        if not rel_path or candidate.is_absolute():
            raise ValueError(f"Absolute or empty path not allowed in workspace: {rel_path!r}")
        base = target_dir.resolve()
        resolved = (base / candidate).resolve()
        if not resolved.is_relative_to(base):
            raise ValueError(f"Path traversal blocked outside workspace: {rel_path!r}")
        return resolved

    def write_files(self, target_dir: Path, files: Mapping[str, str]) -> list[str]:
        """Atomically write ``files`` inside ``target_dir`` while blocking path traversal."""
        target_dir.mkdir(parents=True, exist_ok=True)
        base = target_dir.resolve()
        written: list[str] = []

        for rel_path, content in files.items():
            dest = self._resolve_contained_path(base, rel_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(dest.parent),
                prefix=f".{dest.stem}_",
                suffix=".tmp",
            )
            tmp_path = Path(tmp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(content)
                    handle.flush()
                os.replace(tmp_path, dest)
            except Exception:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
                raise
            written.append(dest.relative_to(base).as_posix())
        return written

    def read_files(
        self,
        target_dir: Path,
        extensions: tuple[str, ...] = _DEFAULT_EXTENSIONS,
    ) -> dict[str, str]:
        """Read contained workspace files matching ``extensions`` cleanly as UTF-8."""
        base = target_dir.resolve()
        if not base.is_dir():
            return {}

        collected: dict[str, str] = {}
        allowed_exts = tuple(ext.lower() for ext in extensions)
        for path in sorted(base.rglob("*")):
            if any(part in _IGNORED_DIRS for part in path.relative_to(base).parts):
                continue
            if not path.is_file():
                continue
            if allowed_exts and path.suffix.lower() not in allowed_exts:
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(base):
                continue
            rel_key = resolved.relative_to(base).as_posix()
            collected[rel_key] = resolved.read_text(encoding="utf-8", errors="replace")
        return collected

    def summarize_code(
        self,
        files: Mapping[str, str],
        max_lines_per_file: int = 50,
        strip_comments: bool = True,
    ) -> str:
        """Render a token-compact context summary of workspace files."""
        if not files:
            return "(no files)"

        sections: list[str] = []
        limit = max(2, max_lines_per_file)
        for rel_path in sorted(files):
            raw_lines = files[rel_path].splitlines()
            total_lines = len(raw_lines)
            if strip_comments and rel_path.endswith(".py"):
                kept = [
                    line for line in raw_lines if line.strip() and not line.lstrip().startswith("#")
                ]
            else:
                kept = raw_lines

            if len(kept) > limit:
                head_count = max(1, limit // 2)
                tail_count = max(1, limit - head_count)
                omitted = len(kept) - head_count - tail_count
                preview = [
                    *kept[:head_count],
                    f"... ({omitted} lines collapsed) ...",
                    *kept[-tail_count:],
                ]
            else:
                preview = kept

            sections.append(
                f"=== File: {rel_path} ({total_lines} lines) ===\n" + "\n".join(preview)
            )
        return "\n\n".join(sections)


class PytestSubprocessRunner:
    """Executes isolated pytest subprocesses with timeout guards and traceback sanitization."""

    def __init__(
        self,
        python_executable: str | None = None,
        default_timeout_s: float = 30.0,
    ) -> None:
        self._python_executable = python_executable or sys.executable
        self._default_timeout_s = default_timeout_s

    @property
    def python_executable(self) -> str:
        """Python interpreter executable invoked for pytest runs."""
        return self._python_executable

    def extract_clean_traceback(self, raw_output: str, max_chars: int = 4000) -> str:
        """Isolate relevant failure/error sections and strip pytest session noise."""
        if not raw_output.strip():
            return ""

        filtered_lines = [
            line
            for line in raw_output.splitlines()
            if not any(line.startswith(prefix) for prefix in _NOISE_LINE_PREFIXES)
        ]
        cleaned = "\n".join(filtered_lines).strip()

        for marker in (
            "=================================== FAILURES",
            "==================================== ERRORS",
            "Traceback (most recent call last):",
            "=========================== short test summary info",
        ):
            idx = cleaned.find(marker)
            if idx != -1:
                cleaned = cleaned[idx:].strip()
                break

        if len(cleaned) > max_chars:
            return cleaned[-max_chars:]
        return cleaned

    def run(self, target_dir: Path, timeout_s: float | None = None) -> TestRunResult:
        """Execute ``pytest`` inside ``target_dir`` and return a rich ``TestRunResult``."""
        effective_timeout = timeout_s if timeout_s is not None else self._default_timeout_s
        if not target_dir.is_dir():
            msg = f"Target directory does not exist: {target_dir}"
            return TestRunResult.failed(msg, clean_traceback=msg)

        env = os.environ.copy()
        resolved_dir = str(target_dir.resolve())
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{resolved_dir}{os.pathsep}{existing_pp}" if existing_pp else resolved_dir
        )

        try:
            proc = subprocess.run(
                [self._python_executable, "-m", "pytest", "-q", "--tb=short"],
                cwd=resolved_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return TestRunResult.timed_out(effective_timeout)
        except OSError as exc:
            msg = f"Failed to execute pytest subprocess: {exc}"
            return TestRunResult.failed(msg, clean_traceback=msg)

        combined = "\n".join(part for part in (proc.stdout, proc.stderr) if part).strip()
        if proc.returncode == 0:
            return TestRunResult.passed(combined or "All tests passed.")

        clean = self.extract_clean_traceback(combined)
        return TestRunResult.failed(
            combined or f"pytest exited with code {proc.returncode}",
            clean_traceback=clean or None,
        )

    def run_tuple(self, target_dir: Path, timeout_s: float | None = None) -> tuple[bool, str, str]:
        """Execute ``run`` and return the legacy 3-tuple ``(success, output, clean_traceback)``."""
        result = self.run(target_dir, timeout_s=timeout_s)
        return (result.success, result.combined_output, result.clean_traceback or "")


class LocalCodeWorkspaceRunner:
    """``ICodeWorkspaceRunner`` adapter composing file manager and pytest runner."""

    def __init__(
        self,
        base_workspace_dir: Path | str | None = None,
        python_executable: str | None = None,
        *,
        file_manager: WorkspaceFileManager | None = None,
        pytest_runner: PytestSubprocessRunner | None = None,
    ) -> None:
        self._file_manager = (
            file_manager
            if file_manager is not None
            else WorkspaceFileManager(base_workspace_dir=base_workspace_dir)
        )
        self._pytest_runner = (
            pytest_runner
            if pytest_runner is not None
            else PytestSubprocessRunner(python_executable=python_executable)
        )

    @property
    def file_manager(self) -> WorkspaceFileManager:
        """Return the composed ``WorkspaceFileManager``."""
        return self._file_manager

    @property
    def pytest_runner(self) -> PytestSubprocessRunner:
        """Return the composed ``PytestSubprocessRunner``."""
        return self._pytest_runner

    @property
    def base_workspace_dir(self) -> Path:
        """Return the workspace base directory from ``WorkspaceFileManager``."""
        return self._file_manager.base_workspace_dir

    def resolve_project_dir(self, project_id: str) -> Path:
        """Resolve a contained directory for ``project_id``."""
        return self._file_manager.resolve_project_dir(project_id)

    def write_files(self, target_dir: Path, files: Mapping[str, str]) -> list[str]:
        """Write files safely within ``target_dir``."""
        return self._file_manager.write_files(target_dir, files)

    def read_files(
        self,
        target_dir: Path,
        extensions: tuple[str, ...] = _DEFAULT_EXTENSIONS,
    ) -> dict[str, str]:
        """Read workspace files matching ``extensions``."""
        return self._file_manager.read_files(target_dir, extensions=extensions)

    def summarize_code(
        self,
        files: Mapping[str, str],
        max_lines_per_file: int = 50,
        strip_comments: bool = True,
    ) -> str:
        """Render a compact summary of source files."""
        return self._file_manager.summarize_code(
            files,
            max_lines_per_file=max_lines_per_file,
            strip_comments=strip_comments,
        )

    def run_tests(self, target_dir: Path, timeout_s: float = 30.0) -> TestRunResult:
        """Execute pytest in ``target_dir`` and return structured ``TestRunResult``."""
        return self._pytest_runner.run(target_dir, timeout_s=timeout_s)


__all__ = [
    "LocalCodeWorkspaceRunner",
    "PytestSubprocessRunner",
    "WorkspaceFileManager",
]
