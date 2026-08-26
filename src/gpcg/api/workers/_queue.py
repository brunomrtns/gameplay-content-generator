"""Queue maintenance helpers used by the job-claim endpoint.

These run inside the claim transaction to recover stale jobs (workers that
died or vanished) and clean up orphan gameplay files (uploaded but never
downloaded). Kept separate from the routes so they can be unit-tested in
isolation (see tests/test_job_requeue.py).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.core.models import Job, JobStatus, Worker
from gpcg.domains.games.models import (
    GameplayProcessingStatus,
    GameplaySource,
)

from gpcg.api.workers._common import (
    _resolve_storage_path,
    _utcnow,
)

log = logging.getLogger(__name__)


def _requeue_stale_jobs_in_claim(db: Session) -> int:
    """Re-queue jobs stuck in 'running' whose worker is offline or vanished.

    Called at the start of /jobs/claim so any worker triggering a claim
    also recovers stale jobs from dead workers.

    A job is stale if:
    - status='running' AND worker_id is set but worker heartbeat is older
      than gpcg_job_lease_timeout
    - status='running' AND worker_id IS NULL AND updated_at older than
      gpcg_job_lease_timeout (VPS worker jobs)

    If attempts >= max_attempts, mark as 'failed' instead of requeuing.
    Does NOT increment attempts — the subsequent /jobs/claim does that.

    Returns the number of jobs requeued.
    """
    settings = get_settings()
    lease_timeout = timedelta(seconds=settings.gpcg_job_lease_timeout)
    now = _utcnow().replace(tzinfo=None)
    cutoff = now - lease_timeout

    # Find running jobs with offline workers
    stale_jobs = (
        db.query(Job)
        .outerjoin(Worker, Job.worker_id == Worker.id)
        .filter(Job.status == JobStatus.running.value)
        .filter(
            (
                # worker_id is NULL (VPS worker) and updated_at is old
                (Job.worker_id.is_(None))
                & (Job.updated_at < cutoff)
            )
            |
            (
                # worker heartbeat is old or NULL
                (Job.worker_id.isnot(None))
                & (
                    (Worker.last_heartbeat.is_(None))
                    | (Worker.last_heartbeat < cutoff)
                )
            )
            |
            (
                # worker is online but not processing this job (orphaned
                # after a deploy/restart interrupted the API mid-job).
                # The worker recovered but the job was left in 'running'.
                (Job.worker_id.isnot(None))
                & (Worker.status == "online")
                & (
                    (Worker.current_job_id.is_(None))
                    | (Worker.current_job_id != Job.id)
                )
                & (Job.updated_at < cutoff)
            )
        )
        .all()
    )

    requeued = 0
    for job in stale_jobs:
        # Never requeue a cancelled job — it was intentionally cancelled
        # (e.g., by domain reset) and must not be revived.
        if job.status == JobStatus.cancelled.value:
            continue
        if job.attempts >= job.max_attempts:
            job.status = JobStatus.failed.value
            job.error = f"Max attempts ({job.max_attempts}) reached after stale worker recovery"
            log.warning(
                f"Job #{job.id} marked as failed (attempts={job.attempts} "
                f">= max_attempts={job.max_attempts})"
            )
        else:
            job.status = JobStatus.queued.value
            job.worker_id = None
            job.started_at = None
            requeued += 1
            log.info(
                f"Job #{job.id} requeued (stale worker, attempts={job.attempts}/"
                f"{job.max_attempts})"
            )

    if stale_jobs:
        db.flush()
    return requeued


def _cleanup_orphan_gameplays(db: Session) -> int:
    """Delete gameplay files from VPS temp_uploads that were never downloaded.

    A gameplay is "orphan" if:
    - It has a storage_key (file exists on VPS)
    - processing_status is still 'uploaded' (worker never claimed/downloaded)
    - created_at is older than 1 hour

    Returns the number of files deleted.
    """
    cutoff = _utcnow().replace(tzinfo=None) - timedelta(hours=1)

    orphans = db.query(GameplaySource).filter(
        GameplaySource.storage_key.isnot(None),
        GameplaySource.processing_status == GameplayProcessingStatus.uploaded.value,
        GameplaySource.created_at < cutoff,
    ).all()

    deleted = 0
    for src in orphans:
        file_path = _resolve_storage_path(src.storage_key)
        if file_path.exists():
            try:
                file_path.unlink()
                log.info(f"Cleaned orphan gameplay: {src.storage_key} (never downloaded, age > 1h)")
                deleted += 1
            except OSError as e:
                log.warning(f"Failed to delete orphan {file_path}: {e}")
        # Clear storage_key so we don't keep trying
        src.storage_key = None

    if deleted:
        db.flush()
    return deleted
