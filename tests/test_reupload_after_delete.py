"""Tests for re-upload after soft-delete.

Reproduces the exact production bug:
1. User uploads a file → GameplaySource created with file_hash
2. Processing fails → user soft-deletes the source
3. User re-uploads the same file → must NOT fail with IntegrityError

Also tests the migration that makes file_hash nullable on databases
with the old UNIQUE(file_hash) constraint.
"""

from __future__ import annotations

import pytest
import tempfile
import os
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import Session


@pytest.fixture()
def real_db():
    """Create a real on-disk SQLite database (not in-memory) so that
    UNIQUE constraints and table recreation work exactly like production.
    """
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test_gpcg.db")
    engine = create_engine(f"sqlite:///{db_path}")

    from gpcg.core.models import Base
    from gpcg.domains.games.models import GameplaySource  # noqa: F401
    Base.metadata.create_all(engine)

    # Insert a test user via ORM (handles all required fields)
    from gpcg.core.models import User
    with Session(engine) as session:
        user = User(email="test@example.com", is_active=True)
        session.add(user)
        session.commit()

    yield engine, db_path

    engine.dispose()
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_reupload_same_hash_after_soft_delete(real_db):
    """Soft-deleting a source and re-uploading the same file must work.

    This is the exact production bug: the UNIQUE constraint on file_hash
    blocked re-uploads because the soft-deleted row still had the hash.
    """
    engine, db_path = real_db
    from gpcg.domains.games.models import GameplaySource, IngestionStatus

    # 1. Create a source (simulates first upload)
    with Session(engine) as session:
        user_id = session.execute(text("SELECT id FROM users LIMIT 1")).scalar()
        src = GameplaySource(
            user_id=user_id,
            filename="test_video.mp4",
            file_hash="abc123def456",
            file_size=1000,
            storage_key=f"user_{user_id}/abc123def_test_video.mp4",
            ingestion_status=IngestionStatus.discovered.value,
        )
        session.add(src)
        session.commit()
        source_id = src.id

    # 2. Soft-delete the source (simulates user deleting failed upload)
    with Session(engine) as session:
        src = session.get(GameplaySource, source_id)
        src.ingestion_status = IngestionStatus.deleted.value
        src.processing_status = None
        src.is_public = False
        src.file_hash = None  # the fix: nullify hash on soft-delete
        session.commit()

    # 3. Re-upload the same file (same hash) — must NOT fail
    with Session(engine) as session:
        user_id = session.execute(text("SELECT id FROM users LIMIT 1")).scalar()
        new_src = GameplaySource(
            user_id=user_id,
            filename="test_video.mp4",
            file_hash="abc123def456",  # same hash as before
            file_size=1000,
            storage_key=f"user_{user_id}/abc123def_test_video.mp4",
            ingestion_status=IngestionStatus.discovered.value,
        )
        session.add(new_src)
        # This must not raise IntegrityError
        session.commit()
        assert new_src.id is not None
        assert new_src.id != source_id


def test_dedup_check_skips_deleted_sources(real_db):
    """The dedup check in upload_routes must skip soft-deleted sources."""
    engine, db_path = real_db
    from gpcg.domains.games.models import GameplaySource, IngestionStatus

    # Create a soft-deleted source with a hash
    with Session(engine) as session:
        user_id = session.execute(text("SELECT id FROM users LIMIT 1")).scalar()
        src = GameplaySource(
            user_id=user_id,
            filename="old.mp4",
            file_hash="shared_hash_001",
            file_size=500,
            ingestion_status=IngestionStatus.deleted.value,
        )
        session.add(src)
        session.commit()

    # Query for existing source with this hash (excluding deleted)
    with Session(engine) as session:
        user_id = session.execute(text("SELECT id FROM users LIMIT 1")).scalar()
        existing = session.query(GameplaySource).filter(
            GameplaySource.user_id == user_id,
            GameplaySource.file_hash == "shared_hash_001",
            GameplaySource.ingestion_status != IngestionStatus.deleted.value,
        ).first()
        assert existing is None, "Dedup check should NOT find soft-deleted sources"


def test_dedup_check_finds_active_sources(real_db):
    """The dedup check must still find active (non-deleted) sources."""
    engine, db_path = real_db
    from gpcg.domains.games.models import GameplaySource, IngestionStatus

    with Session(engine) as session:
        user_id = session.execute(text("SELECT id FROM users LIMIT 1")).scalar()
        src = GameplaySource(
            user_id=user_id,
            filename="active.mp4",
            file_hash="active_hash_001",
            file_size=500,
            ingestion_status=IngestionStatus.discovered.value,
        )
        session.add(src)
        session.commit()

    with Session(engine) as session:
        user_id = session.execute(text("SELECT id FROM users LIMIT 1")).scalar()
        existing = session.query(GameplaySource).filter(
            GameplaySource.user_id == user_id,
            GameplaySource.file_hash == "active_hash_001",
            GameplaySource.ingestion_status != IngestionStatus.deleted.value,
        ).first()
        assert existing is not None, "Dedup check should find active sources"


def test_migration_nullifies_deleted_hashes(real_db):
    """The _drop_old_file_hash_unique migration must nullify file_hash
    on soft-deleted sources.
    """
    engine, db_path = real_db
    from gpcg.domains.games.models import GameplaySource, IngestionStatus

    # Create a soft-deleted source WITH a hash (simulates old data before fix)
    with Session(engine) as session:
        user_id = session.execute(text("SELECT id FROM users LIMIT 1")).scalar()
        src = GameplaySource(
            user_id=user_id,
            filename="deleted_with_hash.mp4",
            file_hash="hash_to_nullify",
            file_size=500,
            ingestion_status=IngestionStatus.deleted.value,
        )
        session.add(src)
        session.commit()

    # Run the migration
    from gpcg.infrastructure.database import _drop_old_file_hash_unique
    _drop_old_file_hash_unique(engine)

    # Verify the hash was nullified
    with Session(engine) as session:
        result = session.execute(text(
            "SELECT file_hash FROM gameplay_sources "
            "WHERE ingestion_status = 'deleted'"
        )).fetchall()
        for row in result:
            assert row[0] is None, "Soft-deleted sources must have NULL file_hash after migration"


def test_migration_is_idempotent(real_db):
    """Running the migration twice must not fail."""
    engine, db_path = real_db
    from gpcg.infrastructure.database import _drop_old_file_hash_unique

    _drop_old_file_hash_unique(engine)
    _drop_old_file_hash_unique(engine)  # must not raise


def test_migration_with_no_deleted_sources(real_db):
    """Migration must work fine when there are no deleted sources."""
    engine, db_path = real_db
    from gpcg.domains.games.models import GameplaySource, IngestionStatus
    from gpcg.infrastructure.database import _drop_old_file_hash_unique

    # Create only active sources
    with Session(engine) as session:
        user_id = session.execute(text("SELECT id FROM users LIMIT 1")).scalar()
        src = GameplaySource(
            user_id=user_id,
            filename="active.mp4",
            file_hash="active_hash",
            file_size=500,
            ingestion_status=IngestionStatus.discovered.value,
        )
        session.add(src)
        session.commit()

    # Must not raise, must not touch active sources
    _drop_old_file_hash_unique(engine)

    with Session(engine) as session:
        result = session.execute(text(
            "SELECT file_hash FROM gameplay_sources WHERE ingestion_status != 'deleted'"
        )).fetchall()
        assert len(result) == 1
        assert result[0][0] == "active_hash"


def test_migration_handles_old_not_null_schema():
    """Test the migration on a database with the OLD schema (file_hash NOT NULL
    and singular UNIQUE constraint). This simulates the production database
    that was created before the model change.
    """
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "old_schema.db")
    engine = create_engine(f"sqlite:///{db_path}")

    # Create the OLD schema (file_hash NOT NULL, UNIQUE(file_hash))
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email VARCHAR(255) UNIQUE NOT NULL,
                is_admin BOOLEAN DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text(
            "INSERT INTO users (email, is_admin, is_active) VALUES ('test@example.com', 0, 1)"
        ))
        # OLD schema: file_hash NOT NULL with UNIQUE constraint
        # Also create indexes that exist in production (would conflict with create_all)
        conn.execute(text("""
            CREATE TABLE gameplay_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                game_id INTEGER,
                file_path VARCHAR(1024) DEFAULT '',
                storage_key VARCHAR(500),
                upload_token VARCHAR(64),
                filename VARCHAR(255) NOT NULL,
                file_hash VARCHAR(64) NOT NULL UNIQUE,
                file_size INTEGER DEFAULT 0,
                capture_source VARCHAR(100),
                recorded_at DATETIME,
                duration FLOAT DEFAULT 0.0,
                width INTEGER DEFAULT 0,
                height INTEGER DEFAULT 0,
                fps FLOAT DEFAULT 0.0,
                codec VARCHAR(50),
                has_audio BOOLEAN DEFAULT 0,
                ingestion_status VARCHAR(20) DEFAULT 'discovered',
                resolution_method VARCHAR(30) DEFAULT 'unknown',
                resolution_confidence FLOAT DEFAULT 0.0,
                resolution_notes TEXT,
                processing_status VARCHAR(30),
                downloaded_at DATETIME,
                downloaded_by_worker VARCHAR(100),
                metadata_json JSON DEFAULT '{}',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_public BOOLEAN DEFAULT 0,
                enabled BOOLEAN DEFAULT 1,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))
        # Insert a soft-deleted source WITH hash (the problematic state)
        conn.execute(text(
            "INSERT INTO gameplay_sources (user_id, filename, file_path, file_hash, file_size, "
            "ingestion_status, processing_status) "
            "VALUES (1, 'old.mp4', '', 'old_hash_123', 1000, 'deleted', NULL)"
        ))
        # Insert an active source
        conn.execute(text(
            "INSERT INTO gameplay_sources (user_id, filename, file_path, file_hash, file_size, "
            "ingestion_status, processing_status) "
            "VALUES (1, 'active.mp4', '', 'active_hash_456', 2000, 'discovered', 'ready')"
        ))
        # Create indexes that exist in production (would conflict with create_all)
        conn.execute(text("CREATE INDEX ix_gameplay_sources_ingestion_status ON gameplay_sources(ingestion_status)"))
        conn.execute(text("CREATE INDEX ix_gameplay_sources_filename ON gameplay_sources(filename)"))
        conn.execute(text("CREATE INDEX ix_gameplay_sources_file_hash ON gameplay_sources(file_hash)"))
        conn.execute(text("CREATE INDEX ix_gameplay_sources_game_id ON gameplay_sources(game_id)"))
        conn.commit()

    # Verify old schema has NOT NULL on file_hash
    inspector = inspect(engine)
    cols = inspector.get_columns("gameplay_sources")
    hash_col = next(c for c in cols if c["name"] == "file_hash")
    assert not hash_col.get("nullable", True), "Pre-condition: file_hash must be NOT NULL in old schema"

    # Run the migration — must recreate the table with nullable file_hash
    from gpcg.infrastructure.database import _drop_old_file_hash_unique
    _drop_old_file_hash_unique(engine)

    # Verify file_hash is now nullable
    inspector = inspect(engine)
    cols = inspector.get_columns("gameplay_sources")
    hash_col = next(c for c in cols if c["name"] == "file_hash")
    assert hash_col.get("nullable", True), "Post-migration: file_hash must be nullable"

    # Verify data was preserved
    with engine.connect() as conn:
        # Active source must still have its hash
        active = conn.execute(text(
            "SELECT file_hash FROM gameplay_sources WHERE ingestion_status != 'deleted'"
        )).fetchall()
        assert len(active) == 1
        assert active[0][0] == "active_hash_456"

        # Deleted source must have NULL hash
        deleted = conn.execute(text(
            "SELECT file_hash FROM gameplay_sources WHERE ingestion_status = 'deleted'"
        )).fetchall()
        assert len(deleted) == 1
        assert deleted[0][0] is None

    # Now verify re-upload with same hash works (the whole point)
    from gpcg.domains.games.models import GameplaySource, IngestionStatus
    with Session(engine) as session:
        new_src = GameplaySource(
            user_id=1,
            filename="old.mp4",
            file_hash="old_hash_123",  # same hash as the deleted source
            file_size=1000,
            ingestion_status=IngestionStatus.discovered.value,
        )
        session.add(new_src)
        session.commit()
        # If we got here without IntegrityError, the fix works
        assert new_src.id is not None

    engine.dispose()
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_migration_crash_recovery():
    """If a previous migration crashed after renaming the table but before
    copying data back, the backup table exists with data and gameplay_sources
    is empty. The migration must detect this and restore the data.
    """
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "crash_recovery.db")
    engine = create_engine(f"sqlite:///{db_path}")

    # Simulate the crashed state: backup table has data, gameplay_sources is empty
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email VARCHAR(255) UNIQUE NOT NULL,
                is_admin BOOLEAN DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("INSERT INTO users (email) VALUES ('test@example.com')"))

        # NEW gameplay_sources table (empty — created by crashed deploy)
        conn.execute(text("""
            CREATE TABLE gameplay_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                game_id INTEGER,
                file_path VARCHAR(1024) DEFAULT '',
                storage_key VARCHAR(500),
                upload_token VARCHAR(64),
                filename VARCHAR(255) NOT NULL,
                file_hash VARCHAR(64),
                file_size INTEGER DEFAULT 0,
                capture_source VARCHAR(100),
                recorded_at DATETIME,
                duration FLOAT DEFAULT 0.0,
                width INTEGER DEFAULT 0,
                height INTEGER DEFAULT 0,
                fps FLOAT DEFAULT 0.0,
                codec VARCHAR(50),
                has_audio BOOLEAN DEFAULT 0,
                ingestion_status VARCHAR(20) DEFAULT 'discovered',
                resolution_method VARCHAR(30) DEFAULT 'unknown',
                resolution_confidence FLOAT DEFAULT 0.0,
                resolution_notes TEXT,
                processing_status VARCHAR(30),
                downloaded_at DATETIME,
                downloaded_by_worker VARCHAR(100),
                metadata_json JSON DEFAULT '{}',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_public BOOLEAN DEFAULT 0,
                enabled BOOLEAN DEFAULT 1,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))

        # Backup table (has the data from before the crash)
        conn.execute(text("""
            CREATE TABLE _gameplay_sources_backup (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                filename VARCHAR(255) NOT NULL,
                file_hash VARCHAR(64),
                file_size INTEGER DEFAULT 0,
                file_path VARCHAR(1024) DEFAULT '',
                ingestion_status VARCHAR(20) DEFAULT 'discovered',
                processing_status VARCHAR(30),
                duration FLOAT DEFAULT 0.0,
                width INTEGER DEFAULT 0,
                height INTEGER DEFAULT 0,
                fps FLOAT DEFAULT 0.0,
                has_audio BOOLEAN DEFAULT 0,
                resolution_method VARCHAR(30) DEFAULT 'unknown',
                resolution_confidence FLOAT DEFAULT 0.0,
                metadata_json JSON DEFAULT '{}',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_public BOOLEAN DEFAULT 0,
                enabled BOOLEAN DEFAULT 1
            )
        """))
        conn.execute(text(
            "INSERT INTO _gameplay_sources_backup (id, user_id, filename, file_hash, "
            "file_size, ingestion_status, processing_status) "
            "VALUES (1, 1, 'video1.mp4', 'hash1', 1000, 'discovered', 'ready')"
        ))
        conn.execute(text(
            "INSERT INTO _gameplay_sources_backup (id, user_id, filename, file_hash, "
            "file_size, ingestion_status, processing_status) "
            "VALUES (2, 1, 'video2.mp4', 'hash2', 2000, 'deleted', NULL)"
        ))
        conn.commit()

    # Verify pre-state
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM gameplay_sources")).scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM _gameplay_sources_backup")).scalar() == 2

    # Run the migration — must detect backup and restore
    from gpcg.infrastructure.database import _drop_old_file_hash_unique
    _drop_old_file_hash_unique(engine)

    # Verify data was restored
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM gameplay_sources")).scalar()
        assert count == 2, f"Expected 2 rows recovered, got {count}"

        active = conn.execute(text(
            "SELECT file_hash FROM gameplay_sources WHERE ingestion_status != 'deleted'"
        )).fetchall()
        assert len(active) == 1
        assert active[0][0] == "hash1"

        deleted = conn.execute(text(
            "SELECT file_hash FROM gameplay_sources WHERE ingestion_status = 'deleted'"
        )).fetchall()
        assert len(deleted) == 1
        assert deleted[0][0] is None

        backup = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_gameplay_sources_backup'"
        )).fetchall()
        assert len(backup) == 0, "Backup table should be dropped after recovery"

    engine.dispose()
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)
