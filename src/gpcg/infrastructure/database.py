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
    # Control Plane + Compute Plane: worker orchestration columns
    # GameplaySource: storage abstraction + processing lifecycle
    _ensure_column(engine, "gameplay_sources", "storage_key", "VARCHAR(500)")
    _ensure_column(engine, "gameplay_sources", "upload_token", "VARCHAR(64)")
    _ensure_column(engine, "gameplay_sources", "processing_status", "VARCHAR(30) DEFAULT 'uploaded'")
    _ensure_column(engine, "gameplay_sources", "downloaded_at", "DATETIME")
    _ensure_column(engine, "gameplay_sources", "downloaded_by_worker", "VARCHAR(100)")
    # Job: worker assignment + priority + capabilities + gameplay_source link
    _ensure_column(engine, "jobs", "worker_id", "INTEGER")
    _ensure_column(engine, "jobs", "priority", "VARCHAR(10) DEFAULT 'normal'")
    _ensure_column(engine, "jobs", "required_capabilities", "JSON")
    _ensure_column(engine, "jobs", "gameplay_source_id", "INTEGER")
    # Video: storage abstraction + YouTube publication
    _ensure_column(engine, "videos", "storage_key", "VARCHAR(500)")
    _ensure_column(engine, "videos", "youtube_url", "VARCHAR(500)")
    _ensure_column(engine, "videos", "youtube_video_id", "VARCHAR(20)")
    # V2 editorial architecture: curiosity scoring on facts
    _ensure_column(engine, "facts", "curiosity_score", "FLOAT DEFAULT 0.0")
    _ensure_column(engine, "facts", "curiosity_subscores", "JSON")
    # Channel knowledge architecture: knowledge processing on documents
    _ensure_column(engine, "documents", "knowledge_status", "VARCHAR(20) DEFAULT 'pending'")
    _ensure_column(engine, "documents", "chunk_count", "INTEGER DEFAULT 0")
    # KnowledgeChunk: game_id for game-specific knowledge isolation in RAG.
    # NULL = general channel knowledge, non-NULL = game-specific.
    _ensure_column(engine, "knowledge_chunks", "game_id", "INTEGER")
    # Document: file_hash + upload_token for worker download (Control/Compute Plane)
    _ensure_column(engine, "documents", "file_hash", "VARCHAR(64)")
    _ensure_column(engine, "documents", "upload_token", "VARCHAR(64)")
    # V2: Game Registry Canônico — new columns on games
    _ensure_column(engine, "games", "slug", "VARCHAR(200)")
    _ensure_column(engine, "games", "description", "TEXT")
    _ensure_column(engine, "games", "release_date", "DATETIME")
    _ensure_column(engine, "games", "developer", "VARCHAR(200)")
    _ensure_column(engine, "games", "publisher", "VARCHAR(200)")
    _ensure_column(engine, "games", "franchise", "VARCHAR(200)")
    _ensure_column(engine, "games", "genres", "JSON")
    _ensure_column(engine, "games", "themes", "JSON")
    _ensure_column(engine, "games", "lore_summary", "TEXT")
    _ensure_column(engine, "games", "external_ids", "JSON")
    _ensure_column(engine, "games", "enriched_at", "DATETIME")
    _ensure_column(engine, "games", "enrichment_error", "TEXT")
    # V2: Video.knowledge_item_id for traceability (D13)
    _ensure_column(engine, "videos", "knowledge_item_id", "INTEGER")
    # V2: GameplaySource.is_public — public gameplays available as fallback
    _ensure_column(engine, "gameplay_sources", "is_public", "BOOLEAN DEFAULT 0")
    # REFACTORY_V2: is_public on content tables for hybrid pool model.
    # NULL user_id = system-collected (shared). user_id set + is_public controls
    # visibility to other users. See docs/REFACTORY_V2_DIAGNOSTIC.md §I.1.
    _ensure_column(engine, "facts", "is_public", "BOOLEAN DEFAULT 0")
    _ensure_column(engine, "documents", "is_public", "BOOLEAN DEFAULT 0")
    _ensure_column(engine, "knowledge_items", "is_public", "BOOLEAN DEFAULT 0")
    # REFACTORY_V2: consumer_user_id on gameplay_clip_usage for per-consumer
    # usage history (public gameplay: A using a segment doesn't block B).
    _ensure_column(engine, "gameplay_clip_usage", "consumer_user_id", "INTEGER")
    # V2: gameplay_clip_usage table is created by create_all() above
    # V2: data migrations (slug generation, aliases JSON → game_aliases, user_id deprecation)
    _migrate_v2_game_registry(engine)
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
        # Create admin linked to BI Identity (no local password).
        # password_hash is NOT NULL in the legacy SQLite schema; use a sentinel
        # since SSO users never authenticate locally.
        admin = User(
            email=admin_email.lower(),
            name="Admin",
            password_hash="!sso-no-local-password",
            is_admin=True,
            is_active=True,
        )
        session.add(admin)
        session.flush()
        log.info(f"Seeded admin user '{admin_email}' (BI Identity SSO — no local password)")


def _migrate_v2_game_registry(engine) -> None:
    """V2 data migration: generate slugs, migrate JSON aliases to game_aliases table,
    deprecate user_id on games.

    Idempotent — guarded by metadata_json.schema_migrations flag. Safe to run
    on every startup. See ARCHITECTURE_V2.md §4.4.
    """
    import logging
    from sqlalchemy import text, inspect
    from gpcg.domain.models import Game, GameAlias
    from gpcg.domain.slug_utils import slugify

    log = logging.getLogger(__name__)

    # Check if games table exists (might not on first init)
    inspector = inspect(engine)
    if "games" not in inspector.get_table_names():
        return
    if "game_aliases" not in inspector.get_table_names():
        return  # create_all hasn't run yet or failed

    with session_scope() as session:
        # Check if migration already ran
        result = session.execute(text(
            "SELECT value FROM json_each("
            "(SELECT metadata_json FROM games LIMIT 1)) WHERE key = 'v2_game_registry_migrated'"
        )).first()
        # Simpler: check if any game has a slug — if yes, migration ran
        any_with_slug = session.execute(
            text("SELECT COUNT(*) FROM games WHERE slug IS NOT NULL")
        ).scalar()
        if any_with_slug and any_with_slug > 0:
            # Migration already ran — just ensure any new games without slug get one
            games_without_slug = session.execute(
                text("SELECT id, canonical_name FROM games WHERE slug IS NULL")
            ).all()
            for row in games_without_slug:
                slug = _generate_unique_slug(session, slugify(row[1]))
                session.execute(
                    text("UPDATE games SET slug = :slug WHERE id = :id"),
                    {"slug": slug, "id": row[0]},
                )
            return

        # Generate slugs for all existing games
        games = session.execute(text("SELECT id, canonical_name, aliases, user_id, metadata_json FROM games")).all()
        for row in games:
            game_id = row[0]
            canonical_name = row[1] or f"game-{game_id}"
            aliases_json = row[2] if row[2] else "[]"
            user_id = row[3]
            metadata_json = row[4] if row[4] else "{}"

            # Generate unique slug
            slug = _generate_unique_slug(session, slugify(canonical_name), exclude_id=game_id)
            session.execute(
                text("UPDATE games SET slug = :slug WHERE id = :id"),
                {"slug": slug, "id": game_id},
            )

            # Migrate JSON aliases to game_aliases table
            import json
            try:
                aliases = json.loads(aliases_json) if isinstance(aliases_json, str) else (aliases_json or [])
            except (json.JSONDecodeError, TypeError):
                aliases = []
            for alias in aliases:
                if alias and alias.strip():
                    # Check if alias already exists for this game
                    existing = session.execute(
                        text("SELECT id FROM game_aliases WHERE game_id = :gid AND alias = :alias"),
                        {"gid": game_id, "alias": alias.strip()},
                    ).first()
                    if not existing:
                        session.execute(
                            text(
                                "INSERT INTO game_aliases (game_id, alias, alias_type, source, created_at) "
                                "VALUES (:gid, :alias, 'alternative', 'migration', datetime('now'))"
                            ),
                            {"gid": game_id, "alias": alias.strip()},
                        )

            # Deprecate user_id: move to metadata_json.legacy_user_id
            if user_id is not None:
                try:
                    meta = json.loads(metadata_json) if isinstance(metadata_json, str) else (metadata_json or {})
                except (json.JSONDecodeError, TypeError):
                    meta = {}
                if "legacy_user_id" not in meta:
                    meta["legacy_user_id"] = user_id
                    session.execute(
                        text("UPDATE games SET user_id = NULL, metadata_json = :meta WHERE id = :id"),
                        {"meta": json.dumps(meta), "id": game_id},
                    )

        log.info(f"V2 game registry migration: processed {len(games)} games (slug + aliases + user_id deprecation)")


def _generate_unique_slug(session, base_slug: str, exclude_id: int = None) -> str:
    """Generate a unique slug, appending -2, -3, etc. on collisions."""
    from sqlalchemy import text

    if not base_slug:
        base_slug = "game"
    slug = base_slug
    suffix = 2
    while True:
        params = {"slug": slug}
        query = "SELECT id FROM games WHERE slug = :slug"
        if exclude_id is not None:
            query += " AND id != :exclude"
            params["exclude"] = exclude_id
        existing = session.execute(text(query), params).first()
        if not existing:
            return slug
        slug = f"{base_slug}-{suffix}"
        suffix += 1
