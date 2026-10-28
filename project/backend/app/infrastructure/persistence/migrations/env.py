"""Alembic environment.

Used two ways:

* programmatically at app start-up (:func:`app.infrastructure.persistence.migrate.upgrade_database`
  passes the URL through ``config.attributes["sqlalchemy_url"]``), and
* from the CLI (``alembic -c backend/app/alembic.ini revision --autogenerate -m "..."``), where the
  URL comes from ``VIBE_DB_URL`` or the default database path.
"""

from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import URL, make_url

from app.infrastructure.persistence.engine import resolve_database_url, to_sync_url
from app.infrastructure.persistence.models import Base, alembic_include_object

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> URL:
    supplied = config.attributes.get("sqlalchemy_url")
    if supplied is not None:
        return make_url(supplied) if isinstance(supplied, str) else supplied
    raw = os.environ.get("VIBE_DB_URL") or "sqlite+aiosqlite:///./data/app.db"
    project_root = Path(__file__).resolve().parents[5]  # <project>/
    return to_sync_url(resolve_database_url(raw, project_root))


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url().render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=alembic_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = (
        _database_url().render_as_string(hide_password=False).replace("%", "%%")
    )
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=alembic_include_object,
            render_as_batch=True,  # SQLite cannot ALTER most things in place
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
