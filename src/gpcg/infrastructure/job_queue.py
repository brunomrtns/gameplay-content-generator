"""Redis Streams job queue — outbox pattern with SQLite as source of truth.

Architecture:
    1. Job creation: INSERT into SQLite (status=queued) + XADD to stream
    2. Worker claim: XREADGROUP → POST /api/jobs/claim-by-id (atomic SQL UPDATE)
    3. Stale recovery: XAUTOCLAIM + SQL requeue
    4. Reconciler: re-hydrates streams from SQLite if XADD failed

Streams are named by (priority, capability-set):
    jobs:{priority}:{capability_set}

    priority: high, normal, low
    capability_set: _, mapping, generation, content_intelligence, enrichment

    Jobs with multiple capabilities are published to ALL subset streams.
    Deduplication is handled by the atomic SQL claim (WHERE status='queued').

Fallback:
    If Redis is unavailable, the worker falls back to POST /api/jobs/claim
    (the original SQLite polling endpoint).
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from gpcg.config import get_settings
from gpcg.core.models import Job, JobPriority, JobStatus, JobType, WorkerCapability
from gpcg.infrastructure.redis_adapter import get_redis

log = logging.getLogger(__name__)

# Consumer group name (shared across all streams)
CONSUMER_GROUP = "gpcg-workers"

# Stream name components
PRIORITIES = ["high", "normal", "low"]
CAPABILITY_SETS = ["_", "mapping", "generation", "content_intelligence", "enrichment"]


def _priority_name(priority: int) -> str:
    """Convert numeric priority to stream name component."""
    if priority == JobPriority.high.value:
        return "high"
    elif priority == JobPriority.low.value:
        return "low"
    return "normal"


def _capability_set(capabilities: list[str]) -> str:
    """Convert capabilities list to stream name component.

    Single capability: use the name directly.
    Multiple capabilities: return the first one (job is published to all subset streams).
    No capabilities: return "_".
    """
    if not capabilities:
        return "_"
    if len(capabilities) == 1:
        return capabilities[0]
    # Multiple capabilities — return first for the primary stream
    # The job will be published to all single-capability subset streams
    return capabilities[0]


def _stream_name(priority: str, capability: str) -> str:
    """Build a stream name from priority and capability."""
    return f"jobs:{priority}:{capability}"


def _all_streams_for_job(priority: str, capabilities: list[str]) -> list[str]:
    """Get all streams a job should be published to.

    For jobs with multiple capabilities, publish to each single-capability stream
    so that workers with any subset of the capabilities can claim it.
    """
    if not capabilities:
        return [_stream_name(priority, "_")]
    streams = []
    for cap in capabilities:
        streams.append(_stream_name(priority, cap))
    return streams


def _streams_for_worker(worker_capabilities: list[str]) -> dict[str, str]:
    """Get streams a worker should read from, ordered by priority.

    Returns {stream_name: "0"} for XREADGROUP (0 = read new messages).
    """
    streams = {}
    # High priority first, then normal, then low
    for priority in PRIORITIES:
        if not worker_capabilities:
            streams[_stream_name(priority, "_")] = ">"
        else:
            for cap in worker_capabilities:
                streams[_stream_name(priority, cap)] = ">"
    return streams


def enqueue_job(job: Job) -> bool:
    """Publish a job to Redis Streams (outbox pattern).

    Called after INSERT + COMMIT in SQLite. If Redis is down, the job
    stays in SQLite as 'queued' and the reconciler will re-hydrate later.

    Returns True if published, False if Redis is unavailable.
    """
    redis = get_redis()
    if not redis.is_available():
        log.debug(f"enqueue_job: Redis unavailable, job #{job.id} stays in SQLite")
        return False

    priority = _priority_name(job.priority)
    capabilities = job.required_capabilities or []
    streams = _all_streams_for_job(priority, capabilities)

    fields = {
        "job_id": str(job.id),
        "type": job.type,
        "user_id": str(job.user_id) if job.user_id else "",
        "priority": priority,
        "required_capabilities": json.dumps(capabilities),
    }

    published = False
    for stream in streams:
        entry_id = redis.xadd(stream, fields)
        if entry_id:
            published = True

    if published:
        log.debug(f"enqueue_job: job #{job.id} published to {len(streams)} stream(s)")
    return published


def claim_job_via_redis(
    worker_id: str,
    worker_capabilities: list[str],
    block_ms: int = 5000,
) -> Optional[dict]:
    """Claim a job via Redis Streams.

    1. XREADGROUP from streams matching worker capabilities
    2. Return the first available job's fields

    Returns job fields dict or None if no job available.
    The caller must then POST /api/jobs/claim-by-id to atomically claim in SQLite.
    """
    redis = get_redis()
    if not redis.is_available():
        return None

    streams = _streams_for_worker(worker_capabilities)
    if not streams:
        return None

    # XAUTOCLAIM stale messages before reading new ones
    settings = get_settings()
    min_idle = settings.gpcg_job_lease_timeout * 1000  # convert to ms

    for stream_name in list(streams.keys()):
        try:
            next_id, claimed, _deleted = redis.xautoclaim(
                stream_name, CONSUMER_GROUP, worker_id, min_idle
            )
            if claimed:
                # Return the first claimed message
                for msg_id, fields in claimed:
                    return _parse_fields(fields)
        except Exception:
            pass  # Stream or group may not exist yet

    # Read new messages
    result = redis.xreadgroup(CONSUMER_GROUP, worker_id, streams, block_ms=block_ms, count=1)
    if not result:
        return None

    for stream_name, messages in result:
        for msg_id, fields in messages:
            return _parse_fields(fields)

    return None


def ack_job(stream: str, msg_id: str) -> bool:
    """Acknowledge a job message after successful processing."""
    redis = get_redis()
    if not redis.is_available():
        return False
    return redis.xack(stream, CONSUMER_GROUP, msg_id) > 0


def reconcile_streams() -> int:
    """Re-hydrate streams from SQLite.

    Finds jobs with status='queued' that are not in any stream and re-publishes them.
    Also finds stale 'running' jobs and requeues them.

    Returns number of jobs re-published.
    """
    redis = get_redis()
    if not redis.is_available():
        return 0

    from gpcg.infrastructure.database import session_scope
    from sqlalchemy.orm.attributes import flag_modified
    import time

    settings = get_settings()
    requeued = 0

    with session_scope() as db:
        # Re-hydrate queued jobs into streams
        queued_jobs = db.query(Job).filter(Job.status == JobStatus.queued.value).all()
        for job in queued_jobs:
            priority = _priority_name(job.priority)
            capabilities = job.required_capabilities or []
            streams = _all_streams_for_job(priority, capabilities)
            fields = {
                "job_id": str(job.id),
                "type": job.type,
                "user_id": str(job.user_id) if job.user_id else "",
                "priority": priority,
                "required_capabilities": json.dumps(capabilities),
            }
            for stream in streams:
                redis.xadd(stream, fields)

        # Requeue stale running jobs
        cutoff = time.time() - settings.gpcg_job_lease_timeout
        from datetime import datetime, timezone
        cutoff_dt = datetime.fromtimestamp(cutoff, tz=timezone.utc)

        stale_jobs = db.query(Job).filter(
            Job.status == JobStatus.running.value,
            Job.updated_at < cutoff_dt,
        ).all()

        for job in stale_jobs:
            if job.attempts < job.max_attempts:
                job.status = JobStatus.queued.value
                job.worker_id = None
                job.started_at = None
                job.error = f"Requeued by reconciler (stale after {settings.gpcg_job_lease_timeout}s)"
                flag_modified(job, "status")
                priority = _priority_name(job.priority)
                capabilities = job.required_capabilities or []
                streams = _all_streams_for_job(priority, capabilities)
                fields = {
                    "job_id": str(job.id),
                    "type": job.type,
                    "user_id": str(job.user_id) if job.user_id else "",
                    "priority": priority,
                    "required_capabilities": json.dumps(capabilities),
                }
                for stream in streams:
                    redis.xadd(stream, fields)
                requeued += 1
                log.info(f"reconciler: requeued stale job #{job.id}")
            else:
                job.status = JobStatus.failed.value
                job.error = f"Max attempts reached after stale timeout"
                job.completed_at = datetime.now(timezone.utc)
                flag_modified(job, "status")
                log.warning(f"reconciler: marked job #{job.id} as failed (max attempts)")

    return requeued


def _parse_fields(fields: dict) -> dict:
    """Parse Redis stream fields into a dict with proper types."""
    result = dict(fields)
    if "job_id" in result:
        try:
            result["job_id"] = int(result["job_id"])
        except (ValueError, TypeError):
            pass
    if "user_id" in result and result["user_id"]:
        try:
            result["user_id"] = int(result["user_id"])
        except (ValueError, TypeError):
            pass
    if "required_capabilities" in result:
        try:
            result["required_capabilities"] = json.loads(result["required_capabilities"])
        except (json.JSONDecodeError, TypeError):
            result["required_capabilities"] = []
    return result
