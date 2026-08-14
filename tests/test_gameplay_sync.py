"""Tests for gameplay sync between workers (Fase 0.5: multi-worker).

Covers:
- GameplayDownload model: per-worker download tracking
- confirm_download: doesn't delete temp file until all workers confirm
- list-for-sync endpoint: returns gameplays a worker needs
- local_db_sync: GPCG_GAMEPLAY_SEARCH_DIRS env var
"""

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gpcg.domain.models import (
    GameplayDownload,
    GameplayProcessingStatus,
    GameplaySource,
    Job,
    JobStage,
    JobStatus,
    Worker,
    WorkerStatus,
)
from gpcg.infrastructure.database import init_db, session_scope


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("GPCG_DB_PATH", str(db_path))
    monkeypatch.setenv("GPCG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GPCG_WORKER_API_KEY", "test-secret")
    monkeypatch.setenv("GPCG_JOB_LEASE_TIMEOUT", "300")
    monkeypatch.setenv("GPCG_WORKER_HEARTBEAT_TIMEOUT", "30")
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


def _make_worker(s, worker_id, status=WorkerStatus.online.value, caps=None):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    w = Worker(
        worker_id=worker_id,
        hostname="test-host",
        status=status,
        last_heartbeat=now,
        capabilities=caps or ["mapping", "generation"],
    )
    s.add(w)
    s.flush()
    return w


def _make_gameplay_source(s, storage_key="user_1/test.mp4", status=GameplayProcessingStatus.uploaded.value):
    """Create a GameplaySource with a temp file on VPS."""
    gp = GameplaySource(
        filename="test.mp4",
        file_hash=hashlib.sha256(b"test").hexdigest(),
        file_size=100,
        storage_key=storage_key,
        processing_status=status,
        upload_token="test-token",
    )
    s.add(gp)
    s.flush()
    return gp


class TestGameplayDownloadModel:
    """Tests for GameplayDownload model."""

    def test_create_download_record(self):
        with session_scope() as s:
            gp = _make_gameplay_source(s)
            dl = GameplayDownload(
                source_id=gp.id,
                worker_id="worker-1",
                downloaded_at=datetime.now(timezone.utc).replace(tzinfo=None),
                checksum_verified=True,
                file_size=100,
            )
            s.add(dl)
            s.flush()
            assert dl.id is not None
            assert dl.source_id == gp.id
            assert dl.worker_id == "worker-1"

    def test_unique_constraint_source_worker(self):
        with pytest.raises(Exception):  # IntegrityError on flush
            with session_scope() as s:
                gp = _make_gameplay_source(s)
                dl1 = GameplayDownload(
                    source_id=gp.id, worker_id="worker-1",
                    downloaded_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                s.add(dl1)
                s.flush()
                dl2 = GameplayDownload(
                    source_id=gp.id, worker_id="worker-1",
                    downloaded_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                s.add(dl2)
                s.flush()  # should raise IntegrityError


class TestConfirmDownloadMultiWorker:
    """Tests for confirm_download with multiple workers."""

    def test_confirm_download_creates_gameplay_download_record(self, tmp_path, monkeypatch):
        """confirm_download should create a GameplayDownload record."""
        from gpcg.api.worker_routes import _resolve_storage_path
        # Create a fake temp file on VPS
        storage_key = "user_1/test_confirm.mp4"
        file_path = _resolve_storage_path(storage_key)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"test content")

        with session_scope() as s:
            _make_worker(s, "worker-1")
            gp = _make_gameplay_source(s, storage_key=storage_key)
            # Override file_hash to match our content
            gp.file_hash = hashlib.sha256(b"test content").hexdigest()
            s.flush()

        from fastapi.testclient import TestClient
        from gpcg.api.app import create_app
        client = TestClient(create_app())
        headers = {"X-Worker-Key": "test-secret"}

        resp = client.post(f"/api/gameplays/{gp.id}/confirm-download", json={
            "worker_id": "worker-1",
            "checksum": hashlib.sha256(b"test content").hexdigest(),
        }, headers=headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        with session_scope() as s:
            dl = s.query(GameplayDownload).filter(
                GameplayDownload.source_id == gp.id,
                GameplayDownload.worker_id == "worker-1",
            ).first()
            assert dl is not None
            assert dl.checksum_verified is True

    def test_temp_file_kept_until_all_workers_confirm(self, tmp_path):
        """With 2 active workers, temp file should NOT be deleted after 1 confirms."""
        from gpcg.api.worker_routes import _resolve_storage_path
        storage_key = "user_1/test_multi.mp4"
        file_path = _resolve_storage_path(storage_key)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"multi worker test")

        with session_scope() as s:
            _make_worker(s, "worker-1")
            _make_worker(s, "worker-2")
            gp = _make_gameplay_source(s, storage_key=storage_key)
            gp.file_hash = hashlib.sha256(b"multi worker test").hexdigest()
            s.flush()
            gp_id = gp.id

        from fastapi.testclient import TestClient
        from gpcg.api.app import create_app
        client = TestClient(create_app())
        headers = {"X-Worker-Key": "test-secret"}

        # Worker 1 confirms
        resp = client.post(f"/api/gameplays/{gp_id}/confirm-download", json={
            "worker_id": "worker-1",
            "checksum": hashlib.sha256(b"multi worker test").hexdigest(),
        }, headers=headers)
        assert resp.status_code == 200

        # File should still exist (worker-2 hasn't confirmed)
        assert file_path.exists(), "Temp file deleted before all workers confirmed"

        # Worker 2 confirms
        resp = client.post(f"/api/gameplays/{gp_id}/confirm-download", json={
            "worker_id": "worker-2",
            "checksum": hashlib.sha256(b"multi worker test").hexdigest(),
        }, headers=headers)
        assert resp.status_code == 200

        # Now file should be deleted
        assert not file_path.exists(), "Temp file not deleted after all workers confirmed"

    def test_temp_file_deleted_single_worker(self, tmp_path):
        """With only 1 active worker, temp file should be deleted after confirm."""
        from gpcg.api.worker_routes import _resolve_storage_path
        storage_key = "user_1/test_single.mp4"
        file_path = _resolve_storage_path(storage_key)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"single worker")

        with session_scope() as s:
            _make_worker(s, "worker-1")
            gp = _make_gameplay_source(s, storage_key=storage_key)
            gp.file_hash = hashlib.sha256(b"single worker").hexdigest()
            s.flush()
            gp_id = gp.id

        from fastapi.testclient import TestClient
        from gpcg.api.app import create_app
        client = TestClient(create_app())
        headers = {"X-Worker-Key": "test-secret"}

        resp = client.post(f"/api/gameplays/{gp_id}/confirm-download", json={
            "worker_id": "worker-1",
            "checksum": hashlib.sha256(b"single worker").hexdigest(),
        }, headers=headers)
        assert resp.status_code == 200

        # File should be deleted (only 1 worker)
        assert not file_path.exists()

    def test_double_confirm_idempotent(self, tmp_path):
        """Same worker confirming twice should not create duplicate records."""
        from gpcg.api.worker_routes import _resolve_storage_path
        storage_key = "user_1/test_idempotent.mp4"
        file_path = _resolve_storage_path(storage_key)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"idempotent")

        with session_scope() as s:
            _make_worker(s, "worker-1")
            gp = _make_gameplay_source(s, storage_key=storage_key)
            gp.file_hash = hashlib.sha256(b"idempotent").hexdigest()
            s.flush()
            gp_id = gp.id

        from fastapi.testclient import TestClient
        from gpcg.api.app import create_app
        client = TestClient(create_app())
        headers = {"X-Worker-Key": "test-secret"}

        for _ in range(2):
            resp = client.post(f"/api/gameplays/{gp_id}/confirm-download", json={
                "worker_id": "worker-1",
                "checksum": hashlib.sha256(b"idempotent").hexdigest(),
            }, headers=headers)
            assert resp.status_code == 200

        with session_scope() as s:
            dls = s.query(GameplayDownload).filter(
                GameplayDownload.source_id == gp_id,
                GameplayDownload.worker_id == "worker-1",
            ).all()
            assert len(dls) == 1


class TestListForSync:
    """Tests for /gameplays/list-for-sync endpoint."""

    def test_returns_gameplays_not_yet_downloaded(self):
        with session_scope() as s:
            _make_worker(s, "worker-1")
            _make_gameplay_source(
                s, storage_key="user_1/gp1.mp4",
                status=GameplayProcessingStatus.downloaded.value,
            )
            _make_gameplay_source(
                s, storage_key="user_1/gp2.mp4",
                status=GameplayProcessingStatus.ready.value,
            )

        from fastapi.testclient import TestClient
        from gpcg.api.app import create_app
        client = TestClient(create_app())
        headers = {"X-Worker-Key": "test-secret"}

        resp = client.get("/api/gameplays/list-for-sync", params={
            "worker_id": "worker-1",
        }, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2

    def test_excludes_already_downloaded(self):
        with session_scope() as s:
            _make_worker(s, "worker-1")
            gp = _make_gameplay_source(
                s, storage_key="user_1/gp1.mp4",
                status=GameplayProcessingStatus.downloaded.value,
            )
            # Mark as already downloaded by worker-1
            dl = GameplayDownload(
                source_id=gp.id, worker_id="worker-1",
                downloaded_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            s.add(dl)

        from fastapi.testclient import TestClient
        from gpcg.api.app import create_app
        client = TestClient(create_app())
        headers = {"X-Worker-Key": "test-secret"}

        resp = client.get("/api/gameplays/list-for-sync", params={
            "worker_id": "worker-1",
        }, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0  # already downloaded

    def test_excludes_uploading_status(self):
        with session_scope() as s:
            _make_worker(s, "worker-1")
            _make_gameplay_source(
                s, storage_key="user_1/gp1.mp4",
                status=GameplayProcessingStatus.uploaded.value,
            )

        from fastapi.testclient import TestClient
        from gpcg.api.app import create_app
        client = TestClient(create_app())
        headers = {"X-Worker-Key": "test-secret"}

        resp = client.get("/api/gameplays/list-for-sync", params={
            "worker_id": "worker-1",
        }, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0  # uploaded = not ready for sync


class TestLocalDbSyncSearchDirs:
    """Tests for GPCG_GAMEPLAY_SEARCH_DIRS env var in local_db_sync.py."""

    def test_search_dirs_from_env(self, tmp_path, monkeypatch):
        """local_db_sync should find gameplays in GPCG_GAMEPLAY_SEARCH_DIRS."""
        from gpcg.worker.local_db_sync import _resolve_local_gameplay_path

        # Create a fake gameplay in a custom dir
        custom_dir = tmp_path / "custom_gameplays"
        custom_dir.mkdir()
        fake_gp = custom_dir / "my_gameplay.mp4"
        fake_gp.write_bytes(b"fake gameplay")

        monkeypatch.setenv("GPCG_GAMEPLAY_SEARCH_DIRS", str(custom_dir))

        result = _resolve_local_gameplay_path(
            vps_path="",
            filename="my_gameplay.mp4",
            storage_root=tmp_path / "storage",
        )
        assert result is not None
        assert "my_gameplay.mp4" in result

    def test_storage_root_gameplays_dir(self, tmp_path):
        """local_db_sync should find gameplays in {storage_root}/gameplays/."""
        from gpcg.worker.local_db_sync import _resolve_local_gameplay_path

        storage_root = tmp_path / "storage"
        gameplays_dir = storage_root / "gameplays"
        gameplays_dir.mkdir(parents=True)
        fake_gp = gameplays_dir / "downloaded.mp4"
        fake_gp.write_bytes(b"downloaded gameplay")

        result = _resolve_local_gameplay_path(
            vps_path="",
            filename="downloaded.mp4",
            storage_root=storage_root,
        )
        assert result is not None
        assert "downloaded.mp4" in result
