"""Tests for stale job recovery and requeue logic (Fase 0: multi-worker).

Covers:
- _requeue_stale_jobs_in_claim: recovers jobs from offline workers
- Jobs with attempts >= max_attempts are marked as failed
- Jobs with attempts < max_attempts are requeued
- Attempts are NOT incremented on requeue (claim does that)
- Worker heartbeat timeout detection
"""

from datetime import datetime, timedelta, timezone
import uuid

import pytest

from gpcg.core.models import (
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


def _make_worker(s, worker_id="test-worker", status=WorkerStatus.online.value,
                 heartbeat_recent=True):
    """Create a worker in the DB."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    worker = Worker(
        worker_id=worker_id,
        hostname="test-host",
        status=status,
        last_heartbeat=now if heartbeat_recent else None,
        capabilities=["mapping", "generation"],
    )
    s.add(worker)
    s.flush()
    return worker


def _make_job(s, worker_id=None, status=JobStatus.running.value,
              attempts=1, max_attempts=3, updated_at=None):
    """Create a job in the DB."""
    job = Job(
        job_uuid=str(uuid.uuid4()),
        type="generate_short",
        status=status,
        stage=JobStage.render.value,
        progress=0.5,
        attempts=attempts,
        max_attempts=max_attempts,
        worker_id=worker_id,
        started_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    s.add(job)
    s.flush()
    if updated_at:
        # Manually set updated_at (SQLAlchemy onupdate won't fire on flush)
        job.updated_at = updated_at
        s.flush()
    return job


class TestRequeueStaleJobsInClaim:
    """Tests for _requeue_stale_jobs_in_claim in worker_routes.py."""

    def test_requeue_job_with_offline_worker(self):
        """Job with worker whose heartbeat is old → requeued."""
        from gpcg.api.worker_routes import _requeue_stale_jobs_in_claim
        from gpcg.infrastructure.database import get_sessionmaker

        old_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)

        with session_scope() as s:
            worker = _make_worker(s, "dead-worker", heartbeat_recent=False)
            worker.last_heartbeat = old_time
            s.flush()
            _make_job(s, worker_id=worker.id, attempts=1, max_attempts=3)

        db = get_sessionmaker()()
        count = _requeue_stale_jobs_in_claim(db)
        assert count == 1

        job = db.query(Job).first()
        assert job.status == JobStatus.queued.value
        assert job.worker_id is None
        assert job.started_at is None
        # attempts NOT incremented (claim will do it)
        assert job.attempts == 1
        db.close()

    def test_fail_job_when_max_attempts_reached(self):
        """Job with attempts >= max_attempts → marked as failed, not requeued."""
        from gpcg.api.worker_routes import _requeue_stale_jobs_in_claim
        from gpcg.infrastructure.database import get_sessionmaker

        old_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)

        with session_scope() as s:
            worker = _make_worker(s, "dead-worker", heartbeat_recent=False)
            worker.last_heartbeat = old_time
            s.flush()
            _make_job(s, worker_id=worker.id, attempts=3, max_attempts=3)

        db = get_sessionmaker()()
        count = _requeue_stale_jobs_in_claim(db)
        assert count == 0  # not requeued, but failed

        job = db.query(Job).first()
        assert job.status == JobStatus.failed.value
        assert "Max attempts" in (job.error or "")
        db.close()

    def test_no_requeue_for_active_worker(self):
        """Job with worker whose heartbeat is recent → NOT requeued."""
        from gpcg.api.worker_routes import _requeue_stale_jobs_in_claim
        from gpcg.infrastructure.database import get_sessionmaker

        with session_scope() as s:
            worker = _make_worker(s, "alive-worker", heartbeat_recent=True)
            _make_job(s, worker_id=worker.id, attempts=1, max_attempts=3)

        db = get_sessionmaker()()
        count = _requeue_stale_jobs_in_claim(db)
        assert count == 0

        job = db.query(Job).first()
        assert job.status == JobStatus.running.value
        db.close()

    def test_requeue_vps_worker_job_with_old_updated_at(self):
        """Job with worker_id=NULL and old updated_at → requeued (VPS worker)."""
        from gpcg.api.worker_routes import _requeue_stale_jobs_in_claim
        from gpcg.infrastructure.database import get_sessionmaker

        old_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)

        with session_scope() as s:
            _make_job(s, worker_id=None, attempts=1, max_attempts=3, updated_at=old_time)

        db = get_sessionmaker()()
        count = _requeue_stale_jobs_in_claim(db)
        assert count == 1

        job = db.query(Job).first()
        assert job.status == JobStatus.queued.value
        db.close()

    def test_no_requeue_vps_worker_job_with_recent_updated_at(self):
        """Job with worker_id=NULL and recent updated_at → NOT requeued."""
        from gpcg.api.worker_routes import _requeue_stale_jobs_in_claim
        from gpcg.infrastructure.database import get_sessionmaker

        with session_scope() as s:
            _make_job(s, worker_id=None, attempts=1, max_attempts=3)

        db = get_sessionmaker()()
        count = _requeue_stale_jobs_in_claim(db)
        assert count == 0

        job = db.query(Job).first()
        assert job.status == JobStatus.running.value
        db.close()

    def test_multiple_stale_jobs_requeued(self):
        """Multiple stale jobs → all requeued."""
        from gpcg.api.worker_routes import _requeue_stale_jobs_in_claim
        from gpcg.infrastructure.database import get_sessionmaker

        old_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)

        with session_scope() as s:
            worker = _make_worker(s, "dead-worker", heartbeat_recent=False)
            worker.last_heartbeat = old_time
            s.flush()
            _make_job(s, worker_id=worker.id, attempts=1, max_attempts=3)
            _make_job(s, worker_id=worker.id, attempts=2, max_attempts=3)

        db = get_sessionmaker()()
        count = _requeue_stale_jobs_in_claim(db)
        assert count == 2
        db.close()

    def test_mixed_stale_and_active_jobs(self):
        """Mix of stale and active jobs → only stale requeued."""
        from gpcg.api.worker_routes import _requeue_stale_jobs_in_claim
        from gpcg.infrastructure.database import get_sessionmaker

        old_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)

        with session_scope() as s:
            dead_worker = _make_worker(s, "dead-worker", heartbeat_recent=False)
            dead_worker.last_heartbeat = old_time
            s.flush()
            alive_worker = _make_worker(s, "alive-worker", heartbeat_recent=True)

            _make_job(s, worker_id=dead_worker.id, attempts=1, max_attempts=3)
            _make_job(s, worker_id=alive_worker.id, attempts=1, max_attempts=3)

        db = get_sessionmaker()()
        count = _requeue_stale_jobs_in_claim(db)
        assert count == 1

        jobs = db.query(Job).all()
        queued = [j for j in jobs if j.status == JobStatus.queued.value]
        running = [j for j in jobs if j.status == JobStatus.running.value]
        assert len(queued) == 1
        assert len(running) == 1
        db.close()

    def test_queued_jobs_not_affected(self):
        """Jobs already in 'queued' status → not touched by stale recovery."""
        from gpcg.api.worker_routes import _requeue_stale_jobs_in_claim
        from gpcg.infrastructure.database import get_sessionmaker

        with session_scope() as s:
            _make_job(s, status=JobStatus.queued.value, attempts=0, max_attempts=3)

        db = get_sessionmaker()()
        count = _requeue_stale_jobs_in_claim(db)
        assert count == 0

        job = db.query(Job).first()
        assert job.status == JobStatus.queued.value
        db.close()


class TestJobClaimWithStaleRecovery:
    """Integration test: /jobs/claim recovers stale jobs before claiming."""

    def test_claim_recovers_stale_then_claims(self):
        """Worker calls /jobs/claim → stale job from dead worker is requeued,
        then the calling worker can claim it (or another queued job)."""
        from fastapi.testclient import TestClient
        from gpcg.api.app import create_app

        old_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)

        with session_scope() as s:
            dead_worker = _make_worker(s, "dead-worker", heartbeat_recent=False)
            dead_worker.last_heartbeat = old_time
            s.flush()
            # Stale job on dead worker
            _make_job(s, worker_id=dead_worker.id, attempts=1, max_attempts=3)

        # Register a new worker and claim
        client = TestClient(create_app())
        headers = {"X-Worker-Key": "test-secret"}

        # Register the new worker
        client.post("/api/workers/register", json={
            "worker_id": "new-worker",
            "hostname": "new-host",
            "capabilities": ["mapping", "generation"],
        }, headers=headers)

        # Claim — should recover stale job first, then claim it
        resp = client.post("/api/jobs/claim", json={
            "worker_id": "new-worker",
            "capabilities": ["mapping", "generation"],
        }, headers=headers)

        assert resp.status_code == 200
        data = resp.json()
        # The stale job should have been requeued and then claimed by new-worker
        assert data["job"] is not None
        assert data["job"]["status"] == JobStatus.running.value
        # attempts should be 2 (was 1, requeued without increment, claim +1)
        assert data["job"]["attempts"] == 2
