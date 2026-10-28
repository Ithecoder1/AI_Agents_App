"""Programmatic Alembic runner: bring a database to the latest schema revision."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL, make_url

from app.infrastructure.persistence.engine import ensure_database_dir, to_sync_url

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def upgrade_database(url: URL | str, revision: str = "head") -> None:
    """Blocking. Accepts the async *or* sync URL; call via ``asyncio.to_thread`` from async code."""
    resolved = make_url(url) if isinstance(url, str) else url
    ensure_database_dir(resolved)
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["sqlalchemy_url"] = to_sync_url(resolved)
    command.upgrade(config, revision)
