"""Async SQLite engine construction with the mandatory concurrency tuning.

Streaming writes (assistant message flushes) happen concurrently with UI history reads, so every
SQLite connection is configured on connect:

* ``journal_mode=WAL``   readers never block the writer and vice versa
* ``synchronous=NORMAL`` fast commits; still durable against app crashes under WAL
* ``foreign_keys=ON``    enforces ``ON DELETE CASCADE`` from sessions to messages
* ``busy_timeout=5000``  wait for a competing writer instead of "database is locked"
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

SQLITE_PRAGMAS: tuple[str, ...] = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA busy_timeout=5000",
)


def _json_dumps(value: Any) -> str:
    # Tool results / extensions may carry values json can't encode natively (datetimes, paths).
    return json.dumps(value, default=str, ensure_ascii=False)


def _apply_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        for pragma in SQLITE_PRAGMAS:
            cursor.execute(pragma)
    finally:
        cursor.close()


def resolve_database_url(db_url: str, base_dir: Path) -> URL:
    """Parse *db_url*; make relative SQLite file paths absolute against *base_dir*.

    Without this a relative ``./data/app.db`` would silently point at a different database
    depending on the process's working directory.
    """
    url = make_url(db_url)
    if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
        assert url.database is not None
        path = Path(url.database)
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        url = url.set(database=str(path))
    return url


def to_sync_url(url: URL) -> URL:
    """The synchronous-driver twin of an async URL (used by Alembic). SQLite only for now."""
    if url.get_backend_name() == "sqlite":
        return url.set(drivername="sqlite")
    return url


def ensure_database_dir(url: URL) -> None:
    if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
        assert url.database is not None
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)


def create_engine(url: URL | str, *, echo: bool = False) -> AsyncEngine:
    resolved = make_url(url) if isinstance(url, str) else url
    engine = create_async_engine(resolved, echo=echo, json_serializer=_json_dumps)
    if resolved.get_backend_name() == "sqlite":
        event.listen(engine.sync_engine, "connect", _apply_sqlite_pragmas)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
