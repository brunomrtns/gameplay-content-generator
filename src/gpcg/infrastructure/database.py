"""Database engine, session, and table initialization."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from gpcg.config import get_settings
from gpcg.logging import get_logger

log = get_logger(__name__)

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


def make_session_scope(session_factory: sessionmaker) -> "contextmanager[Iterator[Session]]":
    """Create a session_scope context manager bound to a specific session factory.

    This allows callers (e.g. local_db_sync.py on the worker) to run the
    GenerationService against a temporary DB without mutating the global
    engine state (no os.environ changes, no database._engine = None).

    Usage:
        SessionLocal = sessionmaker(bind=temp_engine, ...)
        my_session_scope = make_session_scope(SessionLocal)
        gen = GenerationService(session_scope=my_session_scope)
        gen.run_job(job_id)
    """
    @contextmanager
    def _scoped() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return _scoped


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
    from gpcg.core.models import Base  # noqa: F401 — registers core models
    import gpcg.domains.games.models  # noqa: F401 — registers games models
    import gpcg.domains.kids.models  # noqa: F401 — registers kids models

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
    # REFACTORY_V2: rejection_reason for auto-rejected KIs (clickbait/rumor/promotion)
    _ensure_column(engine, "knowledge_items", "rejection_reason", "VARCHAR(500)")
    # REFACTORY_V2: consumer_user_id on gameplay_clip_usage for per-consumer
    # usage history (public gameplay: A using a segment doesn't block B).
    _ensure_column(engine, "gameplay_clip_usage", "consumer_user_id", "INTEGER")
    # ── Editorial Intelligence V2 — ChannelProfile structured fields ───────
    # See docs/EDITORIAL_INTELLIGENCE_V2_PROPOSAL.md §15.2.
    _ensure_column(engine, "channel_profiles", "content_type_affinity", "JSON")
    _ensure_column(engine, "channel_profiles", "editorial_keywords", "JSON")
    _ensure_column(engine, "channel_profiles", "custom_feeds", "JSON")
    _ensure_column(engine, "channel_profiles", "gameplay_driven_collection", "BOOLEAN DEFAULT 1")
    _ensure_column(engine, "channel_profiles", "diversity_strictness", "FLOAT DEFAULT 0.5")
    _ensure_column(engine, "channel_profiles", "learned_preferences", "JSON")
    _ensure_column(engine, "channel_profiles", "production_history_summary", "JSON")
    # Domain model: channel domain (games, kids, movies, etc.)
    _ensure_column(engine, "channel_profiles", "domain", "VARCHAR(30) DEFAULT 'games'")
    # Domain guard: job domain (set at creation, checked on result submission)
    _ensure_column(engine, "jobs", "domain", "VARCHAR(30) DEFAULT 'games'")
    # ── Editorial Intelligence V2 — KnowledgeItem lifecycle ────────────────
    _ensure_column(engine, "knowledge_items", "freshness_score", "FLOAT DEFAULT 1.0")
    _ensure_column(engine, "knowledge_items", "lifecycle_stage", "VARCHAR(20) DEFAULT 'fresh'")
    _ensure_column(engine, "knowledge_items", "feedback_adjustment", "FLOAT DEFAULT 0.0")
    # ── Kids Idea System — KidsTopic traceability + editorial metadata ────
    # kids_ideas table is created by create_all() above.
    # KidsTopic: link back to originating KidsIdea + editorial metadata.
    _ensure_column(engine, "kids_topics", "idea_id", "INTEGER")
    _ensure_column(engine, "kids_topics", "editorial_intent", "VARCHAR(50) DEFAULT 'curiosity'")
    _ensure_column(engine, "kids_topics", "educational_goal", "VARCHAR(50) DEFAULT 'general'")
    # ── Kids media: StoryAsset video support ──────────────────────────────
    # Videos need media_kind, duration, codec, has_audio, thumbnail, and
    # a processing lifecycle (queued → processing → ready).
    _ensure_column(engine, "story_assets", "media_kind", "VARCHAR(10) DEFAULT 'image'")
    _ensure_column(engine, "story_assets", "duration", "FLOAT DEFAULT 0.0")
    _ensure_column(engine, "story_assets", "codec", "VARCHAR(50) DEFAULT ''")
    _ensure_column(engine, "story_assets", "has_audio", "BOOLEAN DEFAULT 0")
    _ensure_column(engine, "story_assets", "thumbnail_key", "VARCHAR(500) DEFAULT ''")
    _ensure_column(engine, "story_assets", "process_error", "TEXT DEFAULT ''")
    # ── Kids media library: tags + description + is_public ───────────────
    # StoryAsset is now a channel library (topic_id is nullable via model
    # change). Tags + description enable semantic selection by
    # KidsMediaRetriever. is_public enables community sharing.
    _ensure_column(engine, "story_assets", "tags", "JSON DEFAULT '[]'")
    _ensure_column(engine, "story_assets", "description", "TEXT DEFAULT ''")
    _ensure_column(engine, "story_assets", "is_public", "BOOLEAN DEFAULT 0")
    # asset_clip_usage table is created by create_all() above (new table)
    # ── Kids media library: make topic_id nullable (SQLite migration) ────
    # SQLite doesn't support ALTER COLUMN; we recreate the table if topic_id
    # is still NOT NULL. New databases get nullable from create_all().
    _migrate_story_assets_topic_id_nullable(engine)
    # ── Fix story_assets primary key (broken by old CREATE TABLE AS SELECT) ──
    # An older version of _migrate_story_assets_topic_id_nullable used
    # CREATE TABLE AS SELECT, which does NOT preserve PRIMARY KEY. This
    # caused id to become a regular INT column (nullable, no auto-increment).
    # This migration detects and fixes that.
    _fix_story_assets_primary_key(engine)
    # V2: gameplay_clip_usage table is created by create_all() above
    # V2: data migrations (slug generation, aliases JSON → game_aliases, user_id deprecation)
    _migrate_v2_game_registry(engine)
    # Seed admin user if not exists (linked to BI Identity by email)
    _seed_admin_user()


def _migrate_story_assets_topic_id_nullable(engine) -> None:
    """Make story_assets.topic_id nullable (SQLite doesn't support ALTER COLUMN).

    SQLite enforces NOT NULL at the column level and doesn't support
    `ALTER TABLE ... ALTER COLUMN`. To change the constraint, we recreate
    the table: create a temp table with the correct schema, copy data,
    drop old, rename temp.

    This is idempotent: if topic_id is already nullable (new databases
    created by create_all()), it's a no-op.
    """
    from sqlalchemy import text, inspect

    inspector = inspect(engine)
    if "story_assets" not in inspector.get_table_names():
        return  # table doesn't exist yet — create_all handles it

    columns = inspector.get_columns("story_assets")
    topic_col = next((c for c in columns if c["name"] == "topic_id"), None)
    if topic_col is None:
        return  # column doesn't exist — will be created by create_all

    # Check if topic_id is already nullable
    if topic_col.get("nullable", True):
        return  # already nullable — nothing to do

    # SQLite: recreate table to drop NOT NULL constraint
    log.info("Migrating story_assets: making topic_id nullable (SQLite table rebuild)")
    with engine.begin() as conn:
        # Get all existing column names
        all_cols = [c["name"] for c in columns]
        col_list = ", ".join(f'"{c}"' for c in all_cols)

        # CRITICAL: CREATE TABLE AS SELECT does NOT preserve PRIMARY KEY.
        # We must rename the old table, create the new one with the correct
        # schema (including id INTEGER PRIMARY KEY AUTOINCREMENT), then
        # copy data back.
        conn.execute(text("ALTER TABLE story_assets RENAME TO _story_assets_backup"))
        # Recreate with proper schema from the SQLAlchemy model
        Base.metadata.create_all(bind=conn, tables=[__import__(
            "gpcg.domains.kids.models", fromlist=["StoryAsset"]
        ).StoryAsset.__table__])
        # Copy data back (id will auto-increment if NULL)
        conn.execute(text(
            f"INSERT INTO story_assets ({col_list}) "
            f"SELECT {col_list} FROM _story_assets_backup"
        ))
        conn.execute(text("DROP TABLE _story_assets_backup"))
    log.info("story_assets migration complete: topic_id is now nullable")


def _fix_story_assets_primary_key(engine) -> None:
    """Fix story_assets.id primary key (broken by old CREATE TABLE AS SELECT).

    An older version of _migrate_story_assets_topic_id_nullable used
    ``CREATE TABLE _new AS SELECT ...`` which does NOT preserve PRIMARY KEY.
    This caused ``id`` to become a regular INT column (nullable, no
    auto-increment), leading to rows with ``id=NULL`` and 500 errors.

    This migration detects if ``id`` is not a primary key and recreates
    the table with the correct schema, assigning auto-increment IDs to
    any rows that have ``id=NULL``.
    """
    from sqlalchemy import text, inspect

    inspector = inspect(engine)
    if "story_assets" not in inspector.get_table_names():
        return  # table doesn't exist yet — create_all handles it

    # Check if id is a primary key
    pk_cols = inspector.get_pk_constraint("story_assets").get("constrained_columns", [])
    if "id" in pk_cols:
        return  # already a primary key — nothing to do

    log.warning("story_assets.id is NOT a primary key — fixing (broken by old migration)")
    columns = inspector.get_columns("story_assets")
    all_cols = [c["name"] for c in columns]
    col_list = ", ".join(f'"{c}"' for c in all_cols)

    with engine.begin() as conn:
        # Rename old table
        conn.execute(text("ALTER TABLE story_assets RENAME TO _story_assets_broken"))
        # Recreate with proper schema from the SQLAlchemy model
        Base.metadata.create_all(bind=conn, tables=[__import__(
            "gpcg.domains.kids.models", fromlist=["StoryAsset"]
        ).StoryAsset.__table__])
        # Copy data back, assigning auto-increment IDs to NULL rows
        # SQLite will auto-assign IDs for NULL id values on INSERT
        conn.execute(text(
            f"INSERT INTO story_assets ({col_list}) "
            f"SELECT {col_list} FROM _story_assets_broken"
        ))
        conn.execute(text("DROP TABLE _story_assets_broken"))
    log.info("story_assets primary key fixed: id is now INTEGER PRIMARY KEY AUTOINCREMENT")


def _seed_admin_user() -> None:
    """Create the admin user if it doesn't exist, linked to BI Identity by email.

    With SSO, BI Identity handles authentication. This just creates a local
    User row for the configured admin email so that data isolation works
    before the admin first logs in. No password is set (BI Identity manages
    credentials). The is_admin flag is set locally for backward compatibility,
    but actual admin authorization is determined by BI Identity roles.
    """
    import logging
    from gpcg.core.models import User
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
    from gpcg.domains.games.models import Game, GameAlias
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
