"""Tests for ingestion service — idempotency, dedup, status tracking."""

from pathlib import Path
import shutil

import pytest

from gpcg.application.ingestion_service import IngestionService
from gpcg.domain.models import GameplaySource, IngestionStatus
from gpcg.infrastructure.database import init_db, session_scope
from sqlalchemy import select


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("GPCG_DB_PATH", str(db_path))
    monkeypatch.setenv("GPCG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GPCG_INBOX_STABLE_SECONDS", "1")
    monkeypatch.setenv("GPCG_INBOX_MIN_SIZE_MB", "0")
    from gpcg.config import get_settings
    get_settings.cache_clear()
    from gpcg.infrastructure import database
    database._engine = None
    database._SessionLocal = None
    init_db()
    yield
    get_settings.cache_clear()
    database._engine = None
    database._SessionLocal = None


class TestIngestion:
    def test_ingest_single_file(self, sample_video: Path, tmp_path: Path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        shutil.copy(sample_video, inbox / "Bully_2026-07-26_14-32-11.mp4")

        from gpcg.config import get_settings
        get_settings.cache_clear()
        import os
        os.environ["GAMEPLAY_INBOX_DIR"] = str(inbox)
        get_settings.cache_clear()

        svc = IngestionService(llm=None)
        n = svc.scan_once()
        assert n == 1

        with session_scope() as s:
            src = s.execute(select(GameplaySource)).scalar_one()
            assert src.filename == "Bully_2026-07-26_14-32-11.mp4"
            assert src.duration == pytest.approx(3.0, abs=0.5)
            assert src.width == 640

    def test_idempotent_rescan(self, sample_video: Path, tmp_path: Path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        shutil.copy(sample_video, inbox / "Bully_2026-07-26_14-32-11.mp4")

        import os
        os.environ["GAMEPLAY_INBOX_DIR"] = str(inbox)
        from gpcg.config import get_settings
        get_settings.cache_clear()

        svc = IngestionService(llm=None)
        assert svc.scan_once() == 1
        # Second scan should find nothing new
        assert svc.scan_once() == 0

        with session_scope() as s:
            count = s.execute(select(GameplaySource)).scalars().all()
            assert len(count) == 1

    def test_skips_non_video_files(self, tmp_path: Path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "readme.txt").write_text("not a video")
        (inbox / "notes.md").write_text("notes")

        import os
        os.environ["GAMEPLAY_INBOX_DIR"] = str(inbox)
        from gpcg.config import get_settings
        get_settings.cache_clear()

        svc = IngestionService(llm=None)
        assert svc.scan_once() == 0

    def test_duplicate_hash_skipped(self, sample_video: Path, tmp_path: Path):
        """Same file copied with different names should be detected as duplicate."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        shutil.copy(sample_video, inbox / "Bully_2026-07-26_14-32-11.mp4")
        shutil.copy(sample_video, inbox / "Bully_2026-07-26_15-00-00.mp4")

        import os
        os.environ["GAMEPLAY_INBOX_DIR"] = str(inbox)
        from gpcg.config import get_settings
        get_settings.cache_clear()

        svc = IngestionService(llm=None)
        n = svc.scan_once()
        # First file ingested, second detected as duplicate (same hash)
        assert n == 1
