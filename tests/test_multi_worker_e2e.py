"""End-to-end multi-worker integration tests (Fase 7).

These tests validate the complete multi-worker architecture:
- Two workers can register and claim different jobs
- Gameplay sync works across workers
- LLM/VLM/ASR providers can be switched via config
- Stale job recovery works with multiple workers
- Worker capabilities are respected

These are integration tests that use the FastAPI TestClient and
in-memory SQLite to simulate the full VPS + worker interaction.
"""

import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from gpcg.api.app import create_app
from gpcg.infrastructure.database import get_sessionmaker, init_db
from gpcg.infrastructure.llm import LLMClient, LLMError
from gpcg.infrastructure.asr_transcriber import (
    ASRTranscriber,
    RemoteASRTranscriber,
    get_asr_transcriber,
)


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Create a test app with in-memory SQLite."""
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
    app = create_app()
    yield app
    get_settings.cache_clear()
    database._engine = None
    database._SessionLocal = None


@pytest.fixture
def client(app):
    """TestClient with worker auth header."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers():
    """Worker auth headers."""
    return {"X-Worker-Key": "test-secret"}


class TestMultiWorkerRegistration:
    """Test that two workers can register independently."""

    def test_register_two_workers(self, client, auth_headers):
        """Both Bruno's PC and Flávio's VM should be able to register."""
        # Register worker 1 (Bruno's PC)
        resp1 = client.post("/api/workers/register", json={
            "worker_id": "home-pc",
            "hostname": "bruno-pc",
            "capabilities": ["mapping", "generation"],
            "gpu_name": "RTX 3060",
            "worker_version": "0.3.15",
        }, headers=auth_headers)
        assert resp1.status_code == 200
        assert resp1.json()["worker_id"] == "home-pc"

        # Register worker 2 (Flávio's VM)
        resp2 = client.post("/api/workers/register", json={
            "worker_id": "flavio-vm",
            "hostname": "flavio-vm",
            "capabilities": ["mapping", "generation"],
            "gpu_name": "CPU only",
            "worker_version": "0.3.15",
        }, headers=auth_headers)
        assert resp2.status_code == 200
        assert resp2.json()["worker_id"] == "flavio-vm"

        # List workers — both should be present
        resp = client.get("/api/workers", headers=auth_headers)
        assert resp.status_code == 200
        workers = resp.json().get("workers", resp.json())
        worker_ids = [w["worker_id"] for w in workers]
        assert "home-pc" in worker_ids
        assert "flavio-vm" in worker_ids

    def test_worker_heartbeat_independent(self, client, auth_headers):
        """Each worker sends heartbeats independently."""
        # Register both
        for wid in ["home-pc", "flavio-vm"]:
            client.post("/api/workers/register", json={
                "worker_id": wid,
                "hostname": wid,
                "capabilities": ["mapping", "generation"],
            }, headers=auth_headers)

        # Heartbeat from worker 1
        resp1 = client.post("/api/workers/home-pc/heartbeat", json={}, headers=auth_headers)
        assert resp1.status_code == 200

        # Heartbeat from worker 2
        resp2 = client.post("/api/workers/flavio-vm/heartbeat", json={}, headers=auth_headers)
        assert resp2.status_code == 200


class TestJobClaimingMultiWorker:
    """Test that jobs are not double-claimed by workers."""

    def test_different_workers_claim_different_jobs(self, client, auth_headers):
        """Two workers should claim different jobs, not the same one."""
        # Register both workers
        for wid in ["home-pc", "flavio-vm"]:
            client.post("/api/workers/register", json={
                "worker_id": wid,
                "hostname": wid,
                "capabilities": ["mapping", "generation"],
            }, headers=auth_headers)

        # Create two jobs directly in the DB
        from gpcg.infrastructure.database import get_sessionmaker
        from gpcg.core.models import Job, JobStatus, JobType
        Session = get_sessionmaker()
        with Session() as session:
            for i in range(2):
                job = Job(
                    type=JobType.mapping.value,
                    status=JobStatus.queued.value,
                    priority="normal",
                    required_capabilities=["mapping"],
                )
                session.add(job)
            session.commit()

        # Worker 1 claims a job
        resp1 = client.post("/api/jobs/claim", json={
            "worker_id": "home-pc",
            "capabilities": ["mapping", "generation"],
        }, headers=auth_headers)
        assert resp1.status_code == 200
        job1 = resp1.json()["job"]
        assert job1 is not None
        assert job1["status"] == "running"

        # Worker 2 claims a job — should get the OTHER one
        resp2 = client.post("/api/jobs/claim", json={
            "worker_id": "flavio-vm",
            "capabilities": ["mapping", "generation"],
        }, headers=auth_headers)
        assert resp2.status_code == 200
        job2 = resp2.json()["job"]
        assert job2 is not None
        assert job2["status"] == "running"

        # They should be different jobs
        assert job1["id"] != job2["id"]

        # Verify worker assignment in DB
        from gpcg.infrastructure.database import get_sessionmaker
        from gpcg.core.models import Job as JobModel
        Session = get_sessionmaker()
        with Session() as session:
            j1 = session.query(JobModel).filter_by(id=job1["id"]).first()
            j2 = session.query(JobModel).filter_by(id=job2["id"]).first()
            # worker_id is the DB integer ID, not the string worker_id
            assert j1.worker_id is not None
            assert j2.worker_id is not None
            assert j1.worker_id != j2.worker_id

    def test_no_job_when_queue_empty(self, client, auth_headers):
        """Worker should get job=None when no jobs available."""
        client.post("/api/workers/register", json={
            "worker_id": "home-pc",
            "hostname": "home-pc",
            "capabilities": ["mapping", "generation"],
        }, headers=auth_headers)

        resp = client.post("/api/jobs/claim", json={
            "worker_id": "home-pc",
            "capabilities": ["mapping", "generation"],
        }, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["job"] is None


class TestProviderSwitching:
    """Test that LLM and ASR providers can be switched via config."""

    def test_llm_provider_ollama_default(self, monkeypatch):
        """Default provider should be Ollama."""
        monkeypatch.setenv("GPCG_LLM_PROVIDER", "ollama")
        monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
        from gpcg.config import get_settings
        get_settings.cache_clear()
        client = LLMClient()
        assert client.provider == "ollama"
        assert client._is_litellm is False
        get_settings.cache_clear()

    def test_llm_provider_litellm(self, monkeypatch):
        """LiteLLM provider should use remote endpoint."""
        monkeypatch.setenv("GPCG_LLM_PROVIDER", "litellm")
        monkeypatch.setenv("GPCG_LITELLM_BASE_URL", "http://10.0.0.5:4000/v1")
        monkeypatch.setenv("GPCG_LITELLM_API_KEY", "test")
        from gpcg.config import get_settings
        get_settings.cache_clear()
        client = LLMClient()
        assert client.provider == "litellm"
        assert client._is_litellm is True
        get_settings.cache_clear()

    def test_asr_provider_local_default(self, monkeypatch):
        """Default ASR provider should be local."""
        monkeypatch.setenv("GPCG_ASR_PROVIDER", "local")
        from gpcg.config import get_settings
        get_settings.cache_clear()
        transcriber = get_asr_transcriber()
        assert isinstance(transcriber, ASRTranscriber)
        get_settings.cache_clear()

    def test_asr_provider_litellm(self, monkeypatch):
        """LiteLLM ASR provider should return RemoteASRTranscriber."""
        monkeypatch.setenv("GPCG_ASR_PROVIDER", "litellm")
        monkeypatch.setenv("GPCG_LITELLM_BASE_URL", "http://10.0.0.5:4000/v1")
        from gpcg.config import get_settings
        get_settings.cache_clear()
        transcriber = get_asr_transcriber()
        assert isinstance(transcriber, RemoteASRTranscriber)
        get_settings.cache_clear()


class TestStaleJobRecoveryMultiWorker:
    """Test stale job recovery with multiple workers."""

    def test_offline_worker_job_reclaimed_by_other(self, client, auth_headers):
        """When a worker goes offline, another worker should reclaim its job."""
        # Register both workers
        for wid in ["home-pc", "flavio-vm"]:
            client.post("/api/workers/register", json={
                "worker_id": wid,
                "hostname": wid,
                "capabilities": ["mapping", "generation"],
            }, headers=auth_headers)

        # Create a job directly in the DB
        from gpcg.infrastructure.database import get_sessionmaker
        from gpcg.core.models import Job, JobStatus, JobType
        Session = get_sessionmaker()
        with Session() as session:
            job = Job(
                type=JobType.mapping.value,
                status=JobStatus.queued.value,
                priority="normal",
                required_capabilities=["mapping"],
            )
            session.add(job)
            session.commit()

        # Worker 1 claims the job
        resp1 = client.post("/api/jobs/claim", json={
            "worker_id": "home-pc",
            "capabilities": ["mapping", "generation"],
        }, headers=auth_headers)
        job1 = resp1.json()["job"]
        assert job1 is not None
        assert job1["status"] == "running"

        # Worker 1 goes offline (no heartbeat for a long time)
        # Simulate by not sending heartbeats and setting stale timestamp
        from gpcg.infrastructure.database import get_sessionmaker
        from gpcg.infrastructure import database as db_mod
        from gpcg.core.models import Worker, Job, JobStatus
        from datetime import datetime, timedelta, timezone

        Session = get_sessionmaker()
        with Session() as session:
            worker = session.query(Worker).filter_by(worker_id="home-pc").first()
            worker.last_heartbeat = datetime.now(timezone.utc) - timedelta(minutes=10)
            worker.status = "offline"
            session.commit()

        # Worker 2 claims — should recover the stale job
        resp2 = client.post("/api/jobs/claim", json={
            "worker_id": "flavio-vm",
            "capabilities": ["mapping", "generation"],
        }, headers=auth_headers)
        assert resp2.status_code == 200
        job2 = resp2.json()["job"]
        # The stale job should be requeued and claimed by worker 2
        assert job2 is not None
        assert job2["status"] == "running"
        # It should be the same job that was previously claimed by worker 1
        assert job2["id"] == job1["id"]


class TestGameplaySyncMultiWorker:
    """Test gameplay sync between workers (Phase 0.5 integration)."""

    def test_list_for_sync_returns_unconfirmed(self, client, auth_headers, tmp_path):
        """list-for-sync should return gameplays not yet confirmed by the worker."""
        # Register workers
        for wid in ["home-pc", "flavio-vm"]:
            client.post("/api/workers/register", json={
                "worker_id": wid,
                "hostname": wid,
                "capabilities": ["mapping", "generation"],
            }, headers=auth_headers)

        # Create a gameplay source with a temp file
        from gpcg.infrastructure.database import get_sessionmaker
        from gpcg.domains.games.models import GameplaySource, GameplayProcessingStatus

        # Create temp file
        temp_file = tmp_path / "gameplay.mp4"
        temp_file.write_bytes(b"fake gameplay")

        Session = get_sessionmaker()
        with Session() as session:
            source = GameplaySource(
                filename="gameplay.mp4",
                storage_key=str(temp_file),
                file_hash="abc123",
                file_size=100,
                processing_status=GameplayProcessingStatus.downloaded,
            )
            session.add(source)
            session.commit()
            source_id = source.id

        # Worker 1 confirms download
        resp = client.post(f"/api/gameplays/{source_id}/confirm-download", json={
            "worker_id": "home-pc",
            "checksum": "abc123",
        }, headers=auth_headers)
        assert resp.status_code == 200

        # list-for-sync for worker 1 should be empty (already confirmed)
        resp1 = client.get("/api/gameplays/list-for-sync?worker_id=home-pc", headers=auth_headers)
        assert resp1.status_code == 200
        data1 = resp1.json()
        sources1 = data1.get("gameplays", data1) if isinstance(data1, dict) else data1
        assert len(sources1) == 0

        # list-for-sync for worker 2 should include the gameplay
        resp2 = client.get("/api/gameplays/list-for-sync?worker_id=flavio-vm", headers=auth_headers)
        assert resp2.status_code == 200
        data2 = resp2.json()
        sources2 = data2.get("gameplays", data2) if isinstance(data2, dict) else data2
        assert len(sources2) == 1
        assert sources2[0]["id"] == source_id


class TestWorkerIndependence:
    """Test that the VM worker can operate independently of Bruno's PC."""

    def test_vm_worker_config_all_remote(self, monkeypatch):
        """VM worker should be fully configured with remote providers."""
        # Simulate VM environment
        monkeypatch.setenv("GPCG_LLM_PROVIDER", "litellm")
        monkeypatch.setenv("GPCG_LITELLM_BASE_URL", "http://10.0.0.1:4000/v1")
        monkeypatch.setenv("GPCG_LITELLM_API_KEY", "vm-key")
        monkeypatch.setenv("GPCG_ASR_PROVIDER", "litellm")
        monkeypatch.setenv("GPCG_YOLO_DEVICE", "cpu")
        monkeypatch.setenv("TTS_ENGINE", "kokoro")
        monkeypatch.setenv("KOKORO_DEFAULT_VOICE", "pm_alex")

        from gpcg.config import get_settings
        get_settings.cache_clear()
        settings = get_settings()

        # All providers should be remote/CPU
        assert settings.gpcg_llm_provider == "litellm"
        assert settings.gpcg_asr_provider == "litellm"
        assert settings.gpcg_yolo_device == "cpu"

        get_settings.cache_clear()

    def test_bruno_pc_config_all_local(self, monkeypatch):
        """Bruno's PC should be configured with local providers."""
        monkeypatch.setenv("GPCG_LLM_PROVIDER", "ollama")
        monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
        monkeypatch.setenv("GPCG_ASR_PROVIDER", "local")
        monkeypatch.setenv("GPCG_GAMEPLAY_ASR_DEVICE", "cuda")
        monkeypatch.setenv("GPCG_YOLO_DEVICE", "cuda")
        monkeypatch.setenv("TTS_ENGINE", "xtts")

        from gpcg.config import get_settings
        get_settings.cache_clear()
        settings = get_settings()

        # All providers should be local/GPU
        assert settings.gpcg_llm_provider == "ollama"
        assert settings.gpcg_asr_provider == "local"
        assert settings.gpcg_yolo_device == "cuda"

        get_settings.cache_clear()
