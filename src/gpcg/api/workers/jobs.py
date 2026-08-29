"""Job queue endpoints — atomic claim, status update, result submission.

These are the core job-lifecycle endpoints called by the Compute Plane worker.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.core.models import (
    Document,
    Job,
    JobPriority,
    JobStage,
    JobStatus,
    JobType,
    Worker,
)
from gpcg.domains.games.models import (
    GameplayAsset,
    GameplayProcessingStatus,
    GameplaySource,
    IngestionStatus,
)
from gpcg.infrastructure.database import get_db, session_scope

from gpcg.api.workers._common import (
    ClaimByIdRequest,
    JobClaimRequest,
    JobResultRequest,
    JobStatusUpdateRequest,
    _ensure_dict,
    _is_transient_error,
    _utcnow,
    worker_auth,
)
from gpcg.api.workers._queue import _requeue_stale_jobs_in_claim

log = logging.getLogger(__name__)
router = APIRouter(tags=["workers"])


# ── Atomic job claim ─────────────────────────────────────────────────────────


@router.post("/jobs/claim")
def claim_job(
    req: JobClaimRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Atomically claim the next available job for a worker.

    This endpoint executes the entire claim operation within a single
    transaction to prevent race conditions where two workers could claim
    the same job:

    1. Begin transaction
    2. Find worker by worker_id
    3. Select queued jobs ordered by priority (HIGH > NORMAL > LOW), then age
    4. Filter by capability matching (job.required_capabilities ⊆ worker.capabilities)
    5. Attempt atomic UPDATE (conditional on status still being 'queued')
    6. If rowcount=0, try next candidate (another worker claimed it)
    7. Commit

    Returns the first successfully claimed job, or null if none available.
    """
    worker = db.query(Worker).filter(Worker.worker_id == req.worker_id).first()
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not registered")

    # Recover stale jobs before claiming new ones
    requeued_count = _requeue_stale_jobs_in_claim(db)
    if requeued_count > 0:
        log.info(f"Recovered {requeued_count} stale job(s) before claim")

    worker_caps = set(req.capabilities or worker.capabilities)

    # Find candidate jobs ordered by priority then creation time
    priority_order = text(
        "CASE priority "
        "WHEN 'high' THEN 0 "
        "WHEN 'normal' THEN 1 "
        "WHEN 'low' THEN 2 "
        "ELSE 1 END"
    )

    candidates = (
        db.query(Job)
        .filter(Job.status == JobStatus.queued.value)
        .order_by(priority_order, Job.created_at.asc())
        .limit(20)
        .all()
    )

    # Filter by capability matching in Python (comma-separated string or JSON array)
    matched = []
    for job in candidates:
        raw = job.required_capabilities
        if not raw:
            required = set()
        elif isinstance(raw, list):
            required = set(raw)
        elif isinstance(raw, str):
            # Try JSON parse first, fall back to comma-separated
            import json as _json
            try:
                parsed = _json.loads(raw)
                required = set(parsed) if isinstance(parsed, list) else {raw}
            except (ValueError, TypeError):
                required = {c.strip() for c in raw.split(",") if c.strip()}
        else:
            required = set()
        if required.issubset(worker_caps):
            matched.append(job)

    # Atomically claim the first matchable job using conditional UPDATE
    now = _utcnow()
    for job in matched:
        result = db.execute(
            text(
                "UPDATE jobs SET status = :running, worker_id = :wid, "
                "started_at = :now, attempts = attempts + 1 "
                "WHERE id = :jid AND status = :queued"
            ),
            {
                "running": JobStatus.running.value,
                "wid": worker.id,
                "now": now.isoformat(),
                "jid": job.id,
                "queued": JobStatus.queued.value,
            },
        )
        if result.rowcount > 0:
            db.flush()
            # Refresh the job object to get updated fields
            db.refresh(job)
            db.commit()
            log.info(
                f"Job #{job.id} claimed by worker '{req.worker_id}' "
                f"(type={job.type}, priority={job.priority})"
            )
            return {
                "job": _serialize_job(job),
                "gameplay_source": _serialize_gameplay_source_for_job(job, db),
                "document": _serialize_document_for_job(job, db),
                "game": _serialize_game_for_job(job, db),
            }

    db.commit()
    return {"job": None}


@router.post("/jobs/claim-by-id")
def claim_job_by_id(
    req: ClaimByIdRequest,
    db: Session = Depends(get_db),
):
    """Claim a specific job by ID (used with Redis Streams).

    The worker gets a job_id from Redis XREADGROUP, then calls this endpoint
    to atomically claim it in SQLite. If the job was already claimed by
    another worker (race condition), returns 409.
    """
    job = db.query(Job).filter(Job.id == req.job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Atomic conditional claim: only if status is still 'queued'
    result = db.execute(text(
        "UPDATE jobs SET status = :running, worker_id = :wid, "
        "started_at = :now, attempts = attempts + 1 "
        "WHERE id = :jid AND status = :queued"
    ), {
        "running": JobStatus.running.value,
        "wid": req.worker_id,
        "now": _utcnow(),
        "jid": req.job_id,
        "queued": JobStatus.queued.value,
    })
    if result.rowcount == 0:
        # Job was already claimed or is no longer queued
        raise HTTPException(status_code=409, detail="Job already claimed")

    db.flush()
    db.refresh(job)
    db.commit()
    log.info(
        f"Job #{job.id} claimed by-id by worker '{req.worker_id}' "
        f"(type={job.type})"
    )
    return {
        "job": _serialize_job(job),
        "gameplay_source": _serialize_gameplay_source_for_job(job, db),
        "document": _serialize_document_for_job(job, db),
        "game": _serialize_game_for_job(job, db),
    }


@router.post("/jobs/recover-my-stale")
def recover_my_stale_jobs(
    req: JobClaimRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Requeue all 'running' jobs assigned to this worker_id.

    Called by the worker on startup to recover jobs that were interrupted
    when the worker was shut down (e.g., PC turned off for the night).

    This is faster than waiting for the reconciler — the worker proactively
    releases its own stale jobs as soon as it starts.
    """
    stale_jobs = db.query(Job).filter(
        Job.status == JobStatus.running.value,
        Job.worker_id == req.worker_id,
    ).all()

    requeued = 0
    for job in stale_jobs:
        if job.status == JobStatus.cancelled.value:
            continue
        if job.attempts >= job.max_attempts:
            job.status = JobStatus.failed.value
            job.error = f"Max attempts reached (recovered on worker restart)"
            log.warning(f"Job #{job.id} marked failed on recovery by '{req.worker_id}'")
        else:
            job.status = JobStatus.queued.value
            job.worker_id = None
            job.started_at = None
            requeued += 1
            log.info(f"Job #{job.id} requeued by worker '{req.worker_id}' on startup recovery")

            # Re-publish to Redis Streams if available
            try:
                from gpcg.infrastructure.job_queue import enqueue_job
                enqueue_job(job)
            except Exception:
                pass  # Redis down — job stays in SQLite as queued

    if stale_jobs:
        db.flush()
        db.commit()

    return {"requeued": requeued, "checked": len(stale_jobs)}


@router.post("/jobs/{job_id}/release")
def release_job(
    job_id: int,
    req: JobClaimRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Release a job back to the queue (graceful shutdown / interruption).

    Called by the worker when it's shutting down with a job in progress.
    Puts the job back in 'queued' so another worker (or the same worker
    on next startup) can pick it up.

    Only works if:
    - The job belongs to the requesting worker
    - The job is currently 'running'
    - The job is not cancelled
    - attempts < max_attempts (otherwise marks as failed)
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status == JobStatus.cancelled.value:
        return {"ok": True, "status": "cancelled", "message": "Job was cancelled"}

    if job.status == JobStatus.completed.value:
        return {"ok": True, "status": "completed", "message": "Job already completed"}

    if job.worker_id != req.worker_id:
        log.warning(
            f"Job #{job.id} release rejected: belongs to '{job.worker_id}' "
            f"but requested by '{req.worker_id}'"
        )
        raise HTTPException(status_code=409, detail="Job belongs to another worker")

    if job.status != JobStatus.running.value:
        # Already queued or in another state — nothing to do
        return {"ok": True, "status": job.status, "message": "Job not running"}

    if job.attempts >= job.max_attempts:
        job.status = JobStatus.failed.value
        job.error = f"Max attempts reached (released on worker shutdown)"
        job.completed_at = _utcnow()
        db.commit()
        log.warning(f"Job #{job.id} marked failed (max attempts on release by '{req.worker_id}')")
        return {"ok": True, "status": "failed", "message": "Max attempts reached"}

    job.status = JobStatus.queued.value
    job.worker_id = None
    job.started_at = None
    job.error = f"Released by worker '{req.worker_id}' on shutdown"
    db.commit()

    # Re-publish to Redis Streams if available
    try:
        from gpcg.infrastructure.job_queue import enqueue_job
        enqueue_job(job)
    except Exception:
        pass  # Redis down — job stays in SQLite as queued

    log.info(f"Job #{job.id} released back to queue by '{req.worker_id}'")
    return {"ok": True, "status": "queued", "message": "Job released back to queue"}


def _serialize_job(job: Job) -> dict:
    """Serialize a Job for the worker API response."""
    return {
        "id": job.id,
        "job_uuid": job.job_uuid,
        "user_id": job.user_id,
        "type": job.type,
        "domain": job.domain,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "priority": job.priority,
        "game_id": job.game_id,
        "content_plan_id": job.content_plan_id,
        "gameplay_source_id": job.gameplay_source_id,
        "artifacts": job.artifacts or {},
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
    }


def _serialize_game_for_job(job: Job, db: Session) -> Optional[dict]:
    """Serialize game info if the job has a game_id (enrichment, generation jobs)."""
    if not job.game_id:
        return None
    from gpcg.domains.games.models import Game
    game = db.get(Game, job.game_id)
    if not game:
        return None
    return {
        "id": game.id,
        "canonical_name": game.canonical_name,
        "slug": game.slug or "",
        "franchise": game.franchise,
        "developer": game.developer,
    }


def _serialize_gameplay_source_for_job(job: Job, db: Session) -> Optional[dict]:
    """Serialize gameplay source info if the job has one (mapping jobs)."""
    if not job.gameplay_source_id:
        return None
    source = db.query(GameplaySource).filter(GameplaySource.id == job.gameplay_source_id).first()
    if not source:
        return None
    return {
        "id": source.id,
        "filename": source.filename,
        "file_hash": source.file_hash,
        "file_size": source.file_size,
        "duration": source.duration,
        "width": source.width,
        "height": source.height,
        "fps": source.fps,
        "codec": source.codec,
        "has_audio": source.has_audio,
        "game_id": source.game_id,
        "storage_key": source.storage_key,
        "upload_token": source.upload_token,
        "capture_source": source.capture_source,
        "resolution_method": source.resolution_method,
        "resolution_confidence": source.resolution_confidence,
    }


def _serialize_document_for_job(job: Job, db: Session) -> Optional[dict]:
    """Serialize document info if the job is a knowledge_index job."""
    if job.type != JobType.knowledge_index.value:
        return None
    doc_id = job.artifacts.get("document_id") if job.artifacts else None
    if not doc_id:
        return None
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        return None
    return {
        "id": doc.id,
        "filename": doc.filename,
        "file_path": doc.file_path,
        "file_type": doc.file_type,
        "file_size": doc.file_size,
        "file_hash": doc.file_hash,
        "upload_token": doc.upload_token,
        "game_id": doc.game_id,
        "user_id": doc.user_id,
    }


# ── Job status update ────────────────────────────────────────────────────────


@router.post("/jobs/{job_id}/status")
def update_job_status(
    job_id: int,
    req: JobStatusUpdateRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Worker reports job progress (stage, progress %, status).

    Called periodically during job execution. Updates the Job record and
    the associated GameplaySource processing_status if applicable.

    GUARD: If the job has been cancelled (by domain reset or otherwise),
    status updates are rejected with 409. This tells the worker to stop
    processing — the job is no longer valid.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Cancellation guard: if the job was cancelled, reject the status update.
    # The worker should detect this and abort processing.
    if job.status == JobStatus.cancelled.value:
        raise HTTPException(
            status_code=409,
            detail="Job has been cancelled. Stop processing.",
        )

    job.status = req.status
    if req.stage:
        job.stage = req.stage
    job.progress = req.progress
    if req.error:
        job.error = req.error
    if req.artifacts:
        merged = {**_ensure_dict(job.artifacts), **req.artifacts}
        job.artifacts = merged

    if req.status == JobStatus.completed.value:
        job.completed_at = _utcnow()
    elif req.status == JobStatus.failed.value:
        job.completed_at = _utcnow()
        job.error = req.error
        # Rollback: re-queue the KnowledgeItem if this job consumed one from
        # the idea queue. The KI stays fresh (not marked used) until a Video
        # is persisted, so we just need to put it back at the top of the queue.
        queued_ki_id = (_ensure_dict(job.artifacts) or {}).get("queued_knowledge_item_id")
        if queued_ki_id:
            try:
                from gpcg.core.models import Automation, KnowledgeItem, KnowledgeItemStatus
                from gpcg.application.knowledge_item_service import release_usage
                from sqlalchemy.orm.attributes import flag_modified
                ki = db.get(KnowledgeItem, queued_ki_id)
                if ki and ki.status == KnowledgeItemStatus.fresh.value:
                    # Release per-consumer usage record so is_used_by_consumer
                    # returns False — otherwise the re-queued KI would be
                    # silently skipped on the next poll cycle.
                    released = release_usage(db, queued_ki_id, job.user_id)
                    auto = db.query(Automation).filter(
                        Automation.user_id == job.user_id
                    ).first()
                    if auto:
                        cfg = dict(auto.config or {})
                        q = list(cfg.get("idea_queue", []))
                        if queued_ki_id not in q:
                            q.insert(0, queued_ki_id)
                            cfg["idea_queue"] = q
                            auto.config = cfg
                            flag_modified(auto, "config")
                            log.info(f"job #{job_id} failed: re-queued KI #{queued_ki_id} at top of idea queue (usage released: {released})")
            except Exception as e:
                log.warning(f"job #{job_id} failed: could not re-queue KI #{queued_ki_id}: {e}")

    # Sync gameplay processing_status for mapping jobs
    if job.gameplay_source_id and job.type == JobType.mapping.value:
        source = db.query(GameplaySource).filter(GameplaySource.id == job.gameplay_source_id).first()
        if source:
            stage_to_status = {
                JobStage.download.value: GameplayProcessingStatus.downloading.value,
                JobStage.confirm_download.value: GameplayProcessingStatus.downloaded.value,
                JobStage.mapping.value: GameplayProcessingStatus.mapping.value,
            }
            if req.stage in stage_to_status:
                source.processing_status = stage_to_status[req.stage]
            if req.status == JobStatus.completed.value:
                source.processing_status = GameplayProcessingStatus.mapped.value
            elif req.status == JobStatus.failed.value:
                source.processing_status = GameplayProcessingStatus.failed.value

    db.commit()

    # Publish event (Redis pub/sub — no-op if Redis is down)
    from gpcg.infrastructure.events import publish_job_status_changed, publish_gameplay_status_changed
    publish_job_status_changed(
        user_id=job.user_id,
        job_id=job.id,
        status=job.status,
        stage=job.stage or "",
        progress=job.progress,
        job_type=job.type,
    )
    if job.gameplay_source_id and job.type == JobType.mapping.value:
        source = db.query(GameplaySource).filter(GameplaySource.id == job.gameplay_source_id).first()
        if source:
            publish_gameplay_status_changed(
                user_id=source.user_id,
                source_id=source.id,
                processing_status=source.processing_status or "",
                filename=source.filename or "",
            )

    return {"ok": True}


# ── Job result ───────────────────────────────────────────────────────────────


@router.post("/jobs/{job_id}/result")
def submit_job_result(
    job_id: int,
    req: JobResultRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Worker sends final job result.

    For generation jobs: includes video metadata (duration, dimensions, QA).
    The actual video file is uploaded separately via /gameplays/{id}/upload-video.
    For mapping jobs: events are sent via /gameplays/{id}/mapping-result.

    If status=completed, marks the job as completed and creates/updates a Video record.
    If status=failed, marks the job as failed with the error message.

    GUARDS:
    - Rejects results for cancelled jobs (409).
    - Rejects results for jobs whose domain no longer matches the channel's
      current domain (409) — prevents old-domain jobs from producing content
      after a domain switch.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # ── Cancellation guard: reject results for already-cancelled jobs ─────────
    if job.status == JobStatus.cancelled.value:
        log.warning(f"Job #{job.id} result rejected: job is already cancelled")
        raise HTTPException(
            status_code=409,
            detail="Job has been cancelled. Result rejected.",
        )

    # ── Idempotency guard: ignore duplicate results for already-completed jobs ─
    # (prevents duplication if XAUTOCLAIM reprocesses a job after worker crash)
    if job.status == JobStatus.completed.value:
        log.info(f"Job #{job.id} result ignored: already completed (duplicate)")
        return {"ok": True, "job_id": job.id, "status": "completed", "message": "Already completed"}

    # ── Domain guard: reject results from jobs belonging to a previous domain ─
    if job.user_id:
        from gpcg.core.models import ChannelProfile, ContentDomain
        profile = db.query(ChannelProfile).filter(
            ChannelProfile.user_id == job.user_id
        ).first()
        current_domain = profile.domain if profile else ContentDomain.games.value
        if job.domain and job.domain != current_domain:
            log.warning(
                f"Job #{job.id} result rejected: job domain='{job.domain}' "
                f"but channel domain='{current_domain}' (domain switch occurred)"
            )
            if job.status != JobStatus.cancelled.value:
                job.status = JobStatus.cancelled.value
                job.error = "Cancelled: channel domain changed"
                job.completed_at = _utcnow()
                db.commit()
            raise HTTPException(
                status_code=409,
                detail="Job belongs to a previous domain. Result rejected.",
            )

    job.status = req.status
    job.completed_at = _utcnow()
    if req.error:
        job.error = req.error
    if req.artifacts:
        merged = {**_ensure_dict(job.artifacts), **req.artifacts}
        job.artifacts = merged
        has_social = "social_title" in merged
        print(f"[RESULT] job #{job_id}: {len(req.artifacts)} artifacts received, social_title present: {has_social}", flush=True)

    # Create/update Video record if video metadata provided
    if req.status == JobStatus.completed.value and req.video:
        from gpcg.core.models import Video, VideoStatus

        video = db.query(Video).filter(Video.job_id == job.id).first()
        vdata = req.video
        if video:
            # Update existing
            if vdata.get("duration"):
                video.duration = vdata["duration"]
            if vdata.get("width"):
                video.width = vdata["width"]
            if vdata.get("height"):
                video.height = vdata["height"]
            if vdata.get("qa_score") is not None:
                video.qa_score = vdata["qa_score"]
            if vdata.get("qa_report"):
                video.qa_report = vdata["qa_report"]
            if vdata.get("storage_key"):
                video.storage_key = vdata["storage_key"]
            if vdata.get("youtube_url"):
                video.youtube_url = vdata["youtube_url"]
                video.youtube_video_id = vdata.get("youtube_video_id")
                video.status = VideoStatus.published.value
            elif vdata.get("qa_score", 0) >= 70:
                video.status = VideoStatus.qa_passed.value
            else:
                video.status = VideoStatus.ready.value
        else:
            video = Video(
                user_id=job.user_id,
                job_id=job.id,
                content_plan_id=job.content_plan_id,
                game_id=job.game_id,
                # REFACTORY_V2: don't use worker's file_path (it points to the
                # worker's local filesystem, not the VPS). Use storage_key instead.
                file_path="",  # will be set by upload-video endpoint
                storage_key=vdata.get("storage_key"),
                duration=vdata.get("duration", 0.0),
                width=vdata.get("width", 0),
                height=vdata.get("height", 0),
                qa_score=vdata.get("qa_score", 0.0),
                qa_report=vdata.get("qa_report", {}),
                status=VideoStatus.published.value if vdata.get("youtube_url") else VideoStatus.ready.value,
                youtube_url=vdata.get("youtube_url"),
                youtube_video_id=vdata.get("youtube_video_id"),
            )
            db.add(video)
        db.flush()

        # Remember to auto-publish after commit (outside the transaction)
        _pending_auto_publish = (vdata.get("storage_key") and not vdata.get("youtube_url"))
        _pending_video = {
            "id": video.id,
            "title": video.title or "",
            "status": video.status or "",
        }
    else:
        _pending_auto_publish = False
        _pending_video = None

    # Update gameplay source status for mapping jobs
    if job.gameplay_source_id and job.type == JobType.mapping.value:
        source = db.query(GameplaySource).filter(GameplaySource.id == job.gameplay_source_id).first()
        if source:
            if req.status == JobStatus.completed.value:
                source.processing_status = GameplayProcessingStatus.ready.value
                source.ingestion_status = IngestionStatus.ready.value
            elif req.status == JobStatus.failed.value:
                # Don't mark as failed if we're going to retry — keep it
                # in the previous state so the retried job can proceed.
                if job.attempts < job.max_attempts and _is_transient_error(req.error):
                    source.processing_status = GameplayProcessingStatus.uploaded.value
                else:
                    source.processing_status = GameplayProcessingStatus.failed.value

    # Rollback: re-queue the KnowledgeItem if this job consumed one from
    # the idea queue and failed. The KI stays fresh (not marked used) until
    # a Video is persisted, so we just put it back at the top of the queue.
    if req.status == JobStatus.failed.value:
        queued_ki_id = (_ensure_dict(job.artifacts) or {}).get("queued_knowledge_item_id")
        if queued_ki_id:
            try:
                from gpcg.core.models import Automation, KnowledgeItem, KnowledgeItemStatus
                from sqlalchemy.orm.attributes import flag_modified
                from gpcg.application.knowledge_item_service import release_usage
                ki = db.get(KnowledgeItem, queued_ki_id)
                if ki and ki.status == KnowledgeItemStatus.fresh.value:
                    released = release_usage(db, queued_ki_id, job.user_id)
                    auto = db.query(Automation).filter(
                        Automation.user_id == job.user_id
                    ).first()
                    if auto:
                        cfg = dict(auto.config or {})
                        q = list(cfg.get("idea_queue", []))
                        if queued_ki_id not in q:
                            q.insert(0, queued_ki_id)
                            cfg["idea_queue"] = q
                            auto.config = cfg
                            flag_modified(auto, "config")
                            log.info(f"job #{job_id} failed: re-queued KI #{queued_ki_id} at top of idea queue (usage released: {released})")
            except Exception as e:
                log.warning(f"job #{job_id} failed: could not re-queue KI #{queued_ki_id}: {e}")

    # ── Auto-retry for transient failures ──────────────────────────────────
    # Instead of marking the job as failed immediately, re-queue it if the
    # error looks transient (timeout, 502, connection reset) and we haven't
    # exhausted max_attempts. This prevents jobs from dying just because the
    # VPS was briefly unavailable during a deploy.
    if req.status == JobStatus.failed.value and job.attempts < job.max_attempts and _is_transient_error(req.error):
        job.status = JobStatus.queued.value
        job.worker_id = None
        job.started_at = None
        job.error = f"Auto-retry (attempt {job.attempts + 1}/{job.max_attempts}): {req.error}"
        log.info(
            f"Job #{job.id} auto-requeued (transient error, attempt "
            f"{job.attempts}/{job.max_attempts}): {req.error}"
        )
        db.commit()
        from gpcg.infrastructure.job_queue import enqueue_job
        enqueue_job(job)
        return {
            "ok": True,
            "job_id": job.id,
            "status": "queued",
            "message": "Auto-requeued due to transient error",
        }

    db.commit()
    log.info(f"Job #{job.id} result: {req.status}")

    # Publish events (Redis pub/sub — no-op if Redis is down)
    from gpcg.infrastructure.events import (
        publish_job_status_changed,
        publish_video_created,
        publish_video_updated,
        publish_gameplay_status_changed,
    )
    publish_job_status_changed(
        user_id=job.user_id,
        job_id=job.id,
        status=job.status,
        stage=job.stage or "",
        progress=job.progress,
        job_type=job.type,
    )
    if _pending_video:
        publish_video_created(
            user_id=job.user_id,
            video_id=_pending_video.get("id"),
            title=_pending_video.get("title", ""),
            status=_pending_video.get("status", ""),
        )
    if job.gameplay_source_id and job.type == JobType.mapping.value:
        source = db.query(GameplaySource).filter(GameplaySource.id == job.gameplay_source_id).first()
        if source:
            publish_gameplay_status_changed(
                user_id=source.user_id,
                source_id=source.id,
                processing_status=source.processing_status or "",
                filename=source.filename or "",
            )

    # Auto-publish OUTSIDE the request transaction to avoid DB lock.
    # Uses a fresh session so the (potentially slow) YouTube upload doesn't
    # block other requests.
    if _pending_auto_publish:
        _maybe_auto_publish(job_id)

    return {"ok": True}


def _maybe_auto_publish(job_id: int) -> None:
    """Auto-publish a video to YouTube if the user's automation has auto_publish=true.

    Runs OUTSIDE the request transaction (uses its own session) so the
    potentially slow YouTube upload doesn't block other DB operations.

    Reads the user's Automation config. If auto_publish=true, resolves the
    storage_key to a file path and calls the google-integration service.
    On success, updates the Video with YouTube URL/ID and status=published.
    On failure, sets status=publish_failed and logs the error (non-fatal).
    If auto_publish=false, sets status=pending_approval for manual review.
    """
    from gpcg.core.models import Automation, VideoStatus, ContentPlan
    from gpcg.core.models import Video as VideoModel
    from gpcg.infrastructure.google_integration_adapter import GoogleIntegrationAdapter

    settings = get_settings()

    # ── Phase 1: read job/video/config, resolve file path, mark as publishing ─
    video_path = None
    title = ""
    description = ""
    tags: list = []
    privacy = settings.gpcg_youtube_privacy
    category_id = settings.gpcg_youtube_category_id

    with session_scope() as db:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            log.error(f"_maybe_auto_publish: job #{job_id} not found")
            return

        job_user_id = job.user_id

        video = db.query(VideoModel).filter(VideoModel.job_id == job.id).first()
        if not video or not video.storage_key:
            log.info(f"_maybe_auto_publish: no video/storage_key for job #{job_id}")
            return

        auto = db.query(Automation).filter(Automation.user_id == job.user_id).first()
        if not auto:
            log.info(f"No automation config for user #{job.user_id}, skipping auto-publish")
            return

        config = _ensure_dict(auto.config)
        auto_publish = config.get("auto_publish", False)

        if not auto_publish:
            video.status = VideoStatus.pending_approval.value
            log.info(f"Video #{video.id} set to pending_approval (auto_publish=false)")
            return

        # Resolve video file path (videos_dir is the primary location after upload)
        storage_key = video.storage_key
        video_path = settings.videos_dir / storage_key
        log.info(f"Auto-publish: checking {video_path} (exists={video_path.exists()})")
        if not video_path.exists():
            video_path = settings.temp_uploads_dir / storage_key
            log.info(f"Auto-publish: checking fallback {video_path} (exists={video_path.exists()})")
        if not video_path.exists():
            log.error(f"Auto-publish: video file not found: {storage_key}")
            video.status = VideoStatus.publish_failed.value
            return

        # Get metadata from job artifacts
        artifacts = _ensure_dict(job.artifacts)
        title = artifacts.get("social_title", "")
        description = artifacts.get("social_description", "")
        tags = list(artifacts.get("social_tags", []))

        if not title:
            cp = db.get(ContentPlan, video.content_plan_id) if video.content_plan_id else None
            title = cp.topic if cp else f"Video #{video.id}"

        privacy = config.get("youtube_privacy", settings.gpcg_youtube_privacy)
        category_id = int(config.get("youtube_category_id", settings.gpcg_youtube_category_id))

        # Mark as publishing (commit before the slow upload)
        video.status = VideoStatus.pending_approval.value
        db.commit()

        log.info(f"Auto-publishing video #{video.id} to YouTube: {title}")

    if video_path is None:
        return  # auto_publish=false or file not found — already handled above

    # ── Phase 2: upload to YouTube (OUTSIDE the DB session — can take minutes) ─
    adapter = GoogleIntegrationAdapter(settings=settings)
    result = adapter.upload_to_youtube(
        video_path,
        title=title,
        description=description,
        tags=tags,
        user_id=job_user_id,
        privacy=privacy,
        category_id=category_id,
    )

    # ── Phase 3: update video status in a fresh session ───────────────────────
    with session_scope() as db:
        v = db.query(VideoModel).filter(VideoModel.job_id == job_id).first()
        if not v:
            return
        if result.success:
            v.youtube_url = result.youtube_url
            v.youtube_video_id = result.youtube_video_id
            v.status = VideoStatus.published.value
            log.info(f"Video #{v.id} published: {result.youtube_url}")
        else:
            v.status = VideoStatus.publish_failed.value
            log.error(f"Video #{v.id} publish failed: {result.error}")
