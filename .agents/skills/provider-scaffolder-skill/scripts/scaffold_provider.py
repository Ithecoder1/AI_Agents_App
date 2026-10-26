#!/usr/bin/env python3
"""Scaffold a new agent provider WITHOUT touching any existing provider or core code.

Creates exactly:
  backend/providers/providers/adapters/<name>/{__init__,adapter,mapping,errors}.py
  tests/providers/test_<name>.py
and APPENDS one (disabled) entry to config/providers.yaml.

Usage:
  python scaffold_provider.py --name acme_cloud [--kind acme-cloud] [--class-name AcmeCloudProvider]
                              [--repo-root PATH] [--dry-run]

Prints the list of created/modified files as JSON. Exit code 0 on success, 2 on invalid input.
Only the standard library is used so it runs in any environment.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from string import Template

SKILL_DIR = Path(__file__).resolve().parents[1]
TEMPLATES = SKILL_DIR / "templates"
# <repo>/.agents/skills/<skill>/scripts/ -> core project lives in <repo>/project
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[4] / "project"

NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
RESERVED = {"fake", "openrouter", "antigravity", "package_runner", "base", "kit", "test", "tests"}


class ScaffoldError(Exception):
    pass


def default_class_name(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_")) + "Provider"


def scaffold(
    name: str,
    *,
    repo_root: Path,
    kind: str | None = None,
    class_name: str | None = None,
    dry_run: bool = False,
    allow_reserved: bool = False,
) -> list[Path]:
    """Create the skeleton; returns every path created or modified (absolute)."""
    if not NAME_RE.match(name):
        raise ScaffoldError(f"--name must match {NAME_RE.pattern!r} (got {name!r})")
    if name in RESERVED and not allow_reserved:
        raise ScaffoldError(f"{name!r} is reserved for a built-in provider")

    provider_id = name.replace("_", "-")
    kind = kind or provider_id
    class_name = class_name or default_class_name(name)
    if not re.match(r"^[A-Z][A-Za-z0-9]*$", class_name):
        raise ScaffoldError(f"--class-name must be CamelCase (got {class_name!r})")

    package_dir = repo_root / "backend" / "providers" / "providers" / "adapters" / name
    test_file = repo_root / "tests" / "providers" / f"test_{name}.py"
    config_file = repo_root / "config" / "providers.yaml"

    if package_dir.exists():
        raise ScaffoldError(f"{package_dir} already exists; refusing to overwrite")
    if test_file.exists():
        raise ScaffoldError(f"{test_file} already exists; refusing to overwrite")
    if not config_file.is_file():
        raise ScaffoldError(f"{config_file} not found; is --repo-root correct?")
    existing = config_file.read_text(encoding="utf-8")
    if re.search(rf"^\s*-\s*id:\s*{re.escape(provider_id)}\s*$", existing, re.MULTILINE):
        raise ScaffoldError(f"provider id {provider_id!r} already present in {config_file.name}")

    values = {
        "name": name,
        "kind": kind,
        "provider_id": provider_id,
        "class_name": class_name,
        "display_name": " ".join(p.capitalize() for p in name.split("_")),
    }

    def render(template_name: str) -> str:
        return Template((TEMPLATES / template_name).read_text(encoding="utf-8")).substitute(values)

    plan: dict[Path, str] = {
        package_dir / "__init__.py": render("__init__.py.tmpl"),
        package_dir / "adapter.py": render("adapter.py.tmpl"),
        package_dir / "mapping.py": render("mapping.py.tmpl"),
        package_dir / "errors.py": render("errors.py.tmpl"),
        test_file: render("test_provider.py.tmpl"),
    }
    yaml_entry = render("providers_entry.yaml.tmpl")

    touched = [*plan, config_file]
    if dry_run:
        return touched

    for path, content in plan.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    if not existing.endswith("\n"):
        existing += "\n"
    config_file.write_text(existing + yaml_entry, encoding="utf-8", newline="\n")
    return touched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--name", required=True, help="snake_case package name, e.g. acme_cloud")
    parser.add_argument("--kind", help="provider kind (default: name with '-')")
    parser.add_argument("--class-name", help="adapter class (default: CamelCase + 'Provider')")
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        touched = scaffold(
            args.name,
            repo_root=args.repo_root.resolve(),
            kind=args.kind,
            class_name=args.class_name,
            dry_run=args.dry_run,
        )
    except ScaffoldError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"dry_run": args.dry_run, "files": [str(p) for p in touched]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
