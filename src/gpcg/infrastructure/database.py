"""Database engine, session, and table initialization."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from gpcg.config import get_settings

# Module-level engine (lazy)
_engine = None
_SessionLocal = None


def _ensure_engine():
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.db_url,
            echo=False,
            connect_args={"check_same_thread": False} if settings.db_url.startswith("sqlite") else {},
            pool_pre_ping=True,
        )
        # Enable WAL mode for SQLite to allow concurrent reads during writes
        if settings.db_url.startswith("sqlite"):
            from sqlalchemy import event
            @event.listens_for(_engine, "connect")
            def set_sqlite_pragma(dbapi_conn, conn_record):
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.close()
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False)
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


def _ensure_column(engine, table: str, column: str, ddl_type: str) -> None:
    """Add a column to a table if it doesn't exist (SQLite/Postgres compatible).

    Uses PRAGMA table_info on SQLite; information_schema on Postgres.
    """
    from sqlalchemy import text, inspect

    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return  # table doesn't exist yet — create_all will handle it
    existing = {c["name"] for c in inspector.get_columns(table)}
    if column in existing:
        return
    with engine.begin() as conn:
        conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl_type}'))


def init_db() -> None:
    """Create all tables. Idempotent. Also applies lightweight column additions
    for schema evolutions that don't warrant a full migration tool.
    """
    from gpcg.domain.models import Base  # noqa: F401 — registers all models

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    # Schema evolutions (additive only — never drop/rename)
    _ensure_column(engine, "games", "camera_type", "VARCHAR(32) DEFAULT 'unknown'")
    # Multi-user: add user_id to all tenant-scoped tables
    _ensure_column(engine, "games", "user_id", "INTEGER")
    _ensure_column(engine, "gameplay_sources", "user_id", "INTEGER")
    _ensure_column(engine, "documents", "user_id", "INTEGER")
    _ensure_column(engine, "facts", "user_id", "INTEGER")
    _ensure_column(engine, "content_plans", "user_id", "INTEGER")
    _ensure_column(engine, "jobs", "user_id", "INTEGER")
    _ensure_column(engine, "videos", "user_id", "INTEGER")
    # SSO migration: add bi_identity_id column, make password_hash nullable
    _ensure_column(engine, "users", "bi_identity_id", "VARCHAR(100)")
    # Seed admin user if not exists (linked to BI Identity by email)
    _seed_admin_user()


def _seed_admin_user() -> None:
    """Create the admin user if it doesn't exist, linked to BI Identity by email.

    With SSO, BI Identity handles authentication. This just creates a local
    User row for the configured admin email so that data isolation works
    before the admin first logs in. No password is set (BI Identity manages
    credentials). The is_admin flag is set locally for backward compatibility,
    but actual admin authorization is determined by BI Identity roles.
    """
    import logging
    from gpcg.domain.models import User
    from gpcg.config import get_settings

    log = logging.getLogger(__name__)
    settings = get_settings()
    admin_email = settings.gpcg_admin_email
    if not admin_email:
        return

    with session_scope() as session:
        existing = session.query(User).filter(User.email == admin_email.lower()).first()
        if existing:
            # Ensure admin flag is set for backward compatibility
            if not existing.is_admin:
                existing.is_admin = True
                session.flush()
            return
        # Create admin linked to BI Identity (no local password)
        admin = User(
            email=admin_email.lower(),
            name="Admin",
            password_hash=None,
            is_admin=True,
            is_active=True,
        )
        session.add(admin)
        session.flush()
        log.info(f"Seeded admin user '{admin_email}' (BI Identity SSO — no local password)")
