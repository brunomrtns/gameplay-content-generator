"""Worker registry endpoints — registration, heartbeat, status, listing.

These are called by the Compute Plane worker on startup and periodically.
Workers authenticate via the ``X-Worker-Key`` header (see ``_common.worker_auth``).
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.core.models import Worker, WorkerStatus
from gpcg.infrastructure.database import get_db

from gpcg.api.workers._common import (
    WorkerHeartbeatRequest,
    WorkerRegisterRequest,
    WorkerStatusRequest,
    _check_worker_offline,
    _utcnow,
    worker_auth,
)
from gpcg.api.workers._queue import _cleanup_orphan_gameplays

log = logging.getLogger(__name__)
router = APIRouter(tags=["workers"])


# ── Worker registration ──────────────────────────────────────────────────────


@router.post("/workers/register")
def register_worker(
    req: WorkerRegisterRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Register or update a worker. Called on worker startup.

    If the worker_id already exists, updates its info (capabilities, version, etc.).
    If new, creates a Worker row with status=online.
    """
    existing = db.query(Worker).filter(Worker.worker_id == req.worker_id).first()

    if existing:
        existing.hostname = req.hostname
        existing.capabilities = req.capabilities
        existing.worker_version = req.worker_version
        existing.git_commit = req.git_commit
        existing.build_number = req.build_number
        existing.gpu_name = req.gpu_name or existing.gpu_name
        existing.status = WorkerStatus.online.value
        existing.last_heartbeat = _utcnow()
        existing.last_status_at = _utcnow()
        db.flush()
        worker = existing
        log.info(f"Worker re-registered: {req.worker_id} (capabilities={req.capabilities})")
    else:
        worker = Worker(
            worker_id=req.worker_id,
            hostname=req.hostname,
            capabilities=req.capabilities,
            worker_version=req.worker_version,
            git_commit=req.git_commit,
            build_number=req.build_number,
            gpu_name=req.gpu_name,
            status=WorkerStatus.online.value,
            last_heartbeat=_utcnow(),
            last_status_at=_utcnow(),
        )
        db.add(worker)
        db.flush()
        log.info(f"New worker registered: {req.worker_id} (capabilities={req.capabilities})")

    db.commit()
    return {
        "worker_id": worker.worker_id,
        "status": worker.status,
        "registered": True,
    }


# ── Heartbeat (frequent, minimal) ────────────────────────────────────────────


@router.post("/workers/{worker_id}/heartbeat")
def heartbeat(
    worker_id: str,
    req: WorkerHeartbeatRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Minimal heartbeat — just 'I'm alive'. Updates last_heartbeat only.

    Does NOT update status, activity, or hardware info (use /status for that).
    Sent frequently (every 10s) to keep the worker marked as online.
    """
    worker = db.query(Worker).filter(Worker.worker_id == worker_id).first()
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not registered")

    worker.last_heartbeat = _utcnow()
    # If worker was offline, mark it online again
    if worker.status == WorkerStatus.offline.value:
        worker.status = WorkerStatus.online.value
    db.commit()
    from gpcg.infrastructure.events import publish_worker_status_changed
    publish_worker_status_changed(
        worker.worker_id, worker.status, worker.current_activity or "",
        worker.gpu_usage, worker.cpu_usage,
    )
    return {"ok": True, "heartbeat_at": worker.last_heartbeat.isoformat()}


# ── Status (less frequent, detailed) ─────────────────────────────────────────


@router.post("/workers/{worker_id}/status")
def update_status(
    worker_id: str,
    req: WorkerStatusRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Full status update — 'what I'm doing'. Updates activity, hardware, job.

    Sent on state changes (job started, stage changed) or periodically (30s).
    Separated from heartbeat to reduce payload on frequent calls.
    """
    worker = db.query(Worker).filter(Worker.worker_id == worker_id).first()
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not registered")

    worker.status = req.status
    worker.current_activity = req.current_activity
    worker.current_job_id = req.current_job_id
    worker.last_heartbeat = _utcnow()  # status also serves as heartbeat
    worker.last_status_at = _utcnow()
    if req.gpu_usage is not None:
        worker.gpu_usage = req.gpu_usage
    if req.cpu_usage is not None:
        worker.cpu_usage = req.cpu_usage
    if req.ram_usage is not None:
        worker.ram_usage = req.ram_usage
    db.commit()
    from gpcg.infrastructure.events import publish_worker_status_changed
    publish_worker_status_changed(
        worker.worker_id, worker.status, worker.current_activity or "",
        worker.gpu_usage, worker.cpu_usage,
    )
    return {"ok": True}


# ── List workers (for frontend) ──────────────────────────────────────────────


@router.get("/workers")
def list_workers(
    db: Session = Depends(get_db),
):
    """List all registered workers with their current status.

    Public endpoint (uses BI Identity SSO via frontend, not worker auth).
    Automatically marks workers as offline if heartbeat timeout exceeded.
    Also cleans up orphan gameplay files (uploaded but never downloaded).

    V3: Classifies workers as active/offline/stale based on heartbeat and
    last_activity. Does NOT delete stale workers — they may represent
    restarts, crashed processes, or machines that will come back.
    The UI should make the distinction clear.
    """
    # Clean up orphan gameplays (runs on every poll — cheap query)
    _cleanup_orphan_gameplays(db)

    workers = db.query(Worker).all()
    result = []
    for w in workers:
        # Auto-detect offline workers
        is_offline = _check_worker_offline(w)
        if is_offline and w.status != WorkerStatus.offline.value:
            w.status = WorkerStatus.offline.value
            db.flush()

        # V3: Classify staleness
        # stale = offline AND no heartbeat for a long time (10x timeout)
        # This is informational only — we do NOT delete stale workers.
        stale = False
        stale_seconds: Optional[int] = None
        if is_offline and w.last_heartbeat:
            settings = get_settings()
            stale_threshold = settings.gpcg_worker_heartbeat_timeout * 10
            now = _utcnow().replace(tzinfo=None)
            last = w.last_heartbeat
            if last.tzinfo:
                last = last.replace(tzinfo=None)
            elapsed = (now - last).total_seconds()
            stale_seconds = int(elapsed)
            if elapsed > stale_threshold:
                stale = True

        result.append({
            "worker_id": w.worker_id,
            "hostname": w.hostname,
            "status": w.status,
            "stale": stale,
            "stale_seconds": stale_seconds,
            "last_heartbeat": w.last_heartbeat.isoformat() if w.last_heartbeat else None,
            "last_status_at": w.last_status_at.isoformat() if w.last_status_at else None,
            "current_activity": w.current_activity,
            "current_job_id": w.current_job_id,
            "gpu_name": w.gpu_name,
            "gpu_usage": w.gpu_usage,
            "cpu_usage": w.cpu_usage,
            "ram_usage": w.ram_usage,
            "capabilities": w.capabilities,
            "worker_version": w.worker_version,
            "git_commit": w.git_commit,
            "registered_at": w.registered_at.isoformat() if w.registered_at else None,
        })
    db.commit()
    return {"workers": result}
