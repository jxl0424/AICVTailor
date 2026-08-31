"""SQLite engine and session handling."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from . import paths
from .models import *  # noqa: F401,F403  -- import for table registration

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        paths.ensure_dirs()
        _engine = create_engine(
            f"sqlite:///{paths.DB_PATH}",
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return _engine


def init_db() -> None:
    """Create tables if they don't exist. No migration framework by design."""
    SQLModel.metadata.create_all(get_engine())


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
