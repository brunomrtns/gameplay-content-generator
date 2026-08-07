"""Catalog database engine, session, and table initialization.

Separate from the main GPCG database (gpcg.db). The catalog DB
(catalog.db) lives in the same data volume but is fully independent —
the catalog service never touches the main GPCG tables and vice versa.

Uses SQLite with WAL mode for concurrent read access from the GPCG API
(which queries the catalog via HTTP, not directly).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from gpcg.config import get_settings


class CatalogBase(DeclarativeBase):
    """Declarative base for catalog models. Separate from gpcg.domain.models.Base."""
    pass


# Module-level engine (lazy)
_engine = None
_SessionLocal = None


def _ensure_engine():
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        db_url = settings.catalog_db_url
        _engine = create_engine(
            db_url,
            echo=False,
            connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {},
            pool_pre_ping=True,
        )
        # Enable WAL mode for SQLite — allows concurrent reads during sync writes.
        # This is important because the GPCG API (or other readers) may query
        # the catalog DB while a sync is writing.
        if db_url.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def set_sqlite_pragma(dbapi_conn, conn_record):
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()
        _SessionLocal = sessionmaker(
            bind=_engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
    return _engine


def get_engine():
    return _ensure_engine()


def get_sessionmaker():
    _ensure_engine()
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session context. Commits on success, rolls back on error."""
    _ensure_engine()
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session (no commit — caller manages)."""
    _ensure_engine()
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_catalog_db() -> None:
    """Create all catalog tables. Idempotent."""
    from gpcg.catalog.models import CatalogGame, CatalogAlias, SyncState  # noqa: F401

    engine = get_engine()
    CatalogBase.metadata.create_all(bind=engine)


def reset_engine() -> None:
    """Reset the cached engine and session factory.

    Used in tests to force re-initialization with a new DB path.
    """
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None
