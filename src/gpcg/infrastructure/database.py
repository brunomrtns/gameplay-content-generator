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
        )
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
    # Seed admin user if not exists
    _seed_admin_user()


def _seed_admin_user() -> None:
    """Create the admin user if it doesn't exist. The admin email comes from
    GPCG_ADMIN_EMAIL config. The admin must set their password via the UI
    on first login (the account is created with a random password that is
    logged once).
    """
    import secrets
    import logging
    from gpcg.domain.models import User
    from gpcg.config import get_settings

    log = logging.getLogger(__name__)
    settings = get_settings()
    admin_email = settings.gpcg_admin_email
    if not admin_email:
        return

    with session_scope() as session:
        existing = session.query(User).filter(User.email == admin_email).first()
        if existing:
            # Ensure admin flag is set
            if not existing.is_admin:
                existing.is_admin = True
                session.flush()
            return
        # Create admin with a random password (must be reset via UI)
        random_pw = secrets.token_urlsafe(24)
        from gpcg.infrastructure.auth import hash_password
        admin = User(
            email=admin_email,
            name="Admin",
            password_hash=hash_password(random_pw),
            is_admin=True,
            is_active=True,
        )
        session.add(admin)
        session.flush()
        log.info(f"Seeded admin user '{admin_email}' with temporary password (reset via UI)")
        # Store temp password in metadata for first-login flow
        admin.metadata_json = {"temp_password": random_pw, "must_reset": True}
        session.flush()
