"""Worker API routes — Control Plane ↔ Compute Plane communication.

The VPS (Control Plane) exposes these endpoints for workers (Compute Plane)
to register, send heartbeats, claim jobs, download/upload files, and report
results. Workers authenticate via X-Worker-Key header (shared secret), NOT
BI Identity SSO (which is for human users).

Architecture:
  - Worker registers → gets or creates Worker row
  - Worker sends heartbeats (frequent, just "I'm alive")
  - Worker sends status updates (less frequent, "what I'm doing")
  - Worker claims jobs (atomic, capability-matched)
  - Worker downloads gameplay files (streaming, token-authenticated)
  - Worker confirms download (checksum verification → VPS deletes temp file)
  - Worker reports mapping results (events only, no frames/crops)
  - Worker reports job results (video file or YouTube link)

All heavy processing (VLM, ASR, FFmpeg, rendering) happens on the worker.
The VPS only stores metadata and orchestrates.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.domain.models import (
    GameplayEvent,
    GameplayProcessingStatus,
    GameplaySource,
    Job,
    JobPriority,
    JobStage,
    JobStatus,
    JobType,
    User,
    Worker,
    WorkerCapability,
    WorkerStatus,
)
from gpcg.infrastructure.auth import get_current_user
from gpcg.infrastructure.database import get_db, session_scope

log = logging.getLogger(__name__)
router = APIRouter(tags=["workers"])


# ── Auth dependency ───────────────────────────────────────────────────────────


def _verify_worker_key(x_worker_key: Optional[str]) -> None:
    """Validate the worker API key. Raises 401 if missing or invalid."""
    settings = get_settings()
    expected = settings.gpcg_worker_api_key
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Worker API disabled — GPCG_WORKER_API_KEY not configured",
        )
    if not x_worker_key or not secrets.compare_digest(x_worker_key, expected):
        raise HTTPException(status_code=401, detail="Invalid worker key")


def worker_auth(x_worker_key: Optional[str] = Header(None)) -> None:
    """FastAPI dependency: verify X-Worker-Key header."""
    _verify_worker_key(x_worker_key)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Request/Response schemas ─────────────────────────────────────────────────


class WorkerRegisterRequest(BaseModel):
    worker_id: str = Field(..., description="Unique worker identifier (e.g., 'home-pc')")
    hostname: str = Field("", description="Machine hostname")
    capabilities: list[str] = Field(
        default_factory=list,
        description="Worker capabilities (e.g., ['mapping', 'generation'])",
    )
    worker_version: str = Field("", description="Worker software version")
    git_commit: str = Field("", description="Git commit hash")
    build_number: str = Field("", description="Build number")
    gpu_name: str = Field("", description="GPU name (e.g., 'RTX 3060')")


class WorkerHeartbeatRequest(BaseModel):
    """Minimal — just 'I'm alive'. Sent frequently (every 10s)."""
    pass


class WorkerStatusRequest(BaseModel):
    """Full status — 'what I'm doing'. Sent on state change or periodically."""
    status: str = Field(..., description="online | busy | error")
    current_activity: str = Field("", description="Human-readable activity (e.g., 'Mapeando Bully.mp4')")
    current_job_id: Optional[int] = Field(None, description="Job currently being processed")
    gpu_usage: Optional[float] = Field(None, description="GPU usage 0-100%")
    cpu_usage: Optional[float] = Field(None, description="CPU usage 0-100%")
    ram_usage: Optional[float] = Field(None, description="RAM usage in GB")


class JobClaimRequest(BaseModel):
    worker_id: str = Field(..., description="Worker requesting a job")
    capabilities: list[str] = Field(
        default_factory=list,
        description="Capabilities the worker currently has (for matching)",
    )


class JobStatusUpdateRequest(BaseModel):
    status: str = Field(..., description="running | completed | failed | retrying")
    stage: str = Field("", description="Current pipeline stage")
    progress: float = Field(0.0, description="Progress 0.0-1.0")
    error: str = Field("", description="Error message if failed")
    artifacts: dict = Field(default_factory=dict, description="Updated artifacts")


class ConfirmDownloadRequest(BaseModel):
    worker_id: str = Field(..., description="Worker confirming the download")
    checksum: str = Field(..., description="SHA256 hash of the downloaded file")


class MappingResultRequest(BaseModel):
    """Worker sends gameplay analysis events (metadata only, no frames)."""
    events: list[dict] = Field(
        default_factory=list,
        description="GameplayEvent records (event_type, start_time, end_time, etc.)",
    )
    analysis_version: str = Field("v1", description="Analysis version tag")
    config_hash: str = Field("", description="Config hash for reprocessing detection")
    compatibility: dict = Field(
        default_factory=dict,
        description="Compatibility flags {game_related: bool, general_topic: bool}",
    )


class JobResultRequest(BaseModel):
    """Worker sends final job result (video metadata or YouTube link)."""
    status: str = Field(..., description="completed | failed")
    error: str = Field("", description="Error message if failed")
    artifacts: dict = Field(default_factory=dict, description="Final artifacts")
    # Video metadata (if a video was produced)
    video: Optional[dict] = Field(
        None,
        description="Video metadata {duration, width, height, qa_score, qa_report, storage_key, youtube_url, youtube_video_id}",
    )


# ── Helper functions ─────────────────────────────────────────────────────────


def _resolve_storage_path(storage_key: str) -> Path:
    """Resolve a storage_key to a physical path on the VPS filesystem."""
    settings = get_settings()
    # storage_key is relative to temp_uploads_dir (for gameplays) or videos_dir
    # We try temp_uploads first, then videos
    temp_path = settings.temp_uploads_dir / storage_key
    if temp_path.exists():
        return temp_path
    video_path = settings.videos_dir / storage_key
    if video_path.exists():
        return video_path
    # Fallback: treat as absolute path (legacy compat)
    p = Path(storage_key)
    if p.is_absolute() and p.exists():
        return p
    return temp_path  # return expected path even if not found


def _generate_upload_token() -> str:
    """Generate a one-time download token."""
    return secrets.token_urlsafe(32)


def _check_worker_offline(worker: Worker) -> bool:
    """Check if a worker should be marked offline based on heartbeat timeout."""
    if not worker.last_heartbeat:
        return True
    settings = get_settings()
    timeout = settings.gpcg_worker_heartbeat_timeout
    # SQLite stores datetimes as naive — handle both aware and naive
    now = _utcnow().replace(tzinfo=None)
    last = worker.last_heartbeat
    if last.tzinfo:
        last = last.replace(tzinfo=None)
    elapsed = (now - last).total_seconds()
    return elapsed > timeout


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
    return {"ok": True}


# ── List workers (for frontend) ──────────────────────────────────────────────


@router.get("/workers")
def list_workers(
    db: Session = Depends(get_db),
):
    """List all registered workers with their current status.

    Public endpoint (uses BI Identity SSO via frontend, not worker auth).
    Automatically marks workers as offline if heartbeat timeout exceeded.
    """
    workers = db.query(Worker).all()
    result = []
    for w in workers:
        # Auto-detect offline workers
        if _check_worker_offline(w) and w.status != WorkerStatus.offline.value:
            w.status = WorkerStatus.offline.value
            db.flush()
        result.append({
            "worker_id": w.worker_id,
            "hostname": w.hostname,
            "status": w.status,
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
            "registered_at": w.registered_at.isoformat() if w.registered_at else None,
        })
    db.commit()
    return {"workers": result}


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

    # Filter by capability matching in Python (JSON array subset check)
    matched = []
    for job in candidates:
        required = set(job.required_capabilities or [])
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
            }

    db.commit()
    return {"job": None}


def _serialize_job(job: Job) -> dict:
    """Serialize a Job for the worker API response."""
    return {
        "id": job.id,
        "job_uuid": job.job_uuid,
        "type": job.type,
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
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job.status = req.status
    if req.stage:
        job.stage = req.stage
    job.progress = req.progress
    if req.error:
        job.error = req.error
    if req.artifacts:
        merged = dict(job.artifacts or {})
        merged.update(req.artifacts)
        job.artifacts = merged

    if req.status == JobStatus.completed.value:
        job.completed_at = _utcnow()
    elif req.status == JobStatus.failed.value:
        job.completed_at = _utcnow()
        job.error = req.error

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
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job.status = req.status
    job.completed_at = _utcnow()
    if req.error:
        job.error = req.error
    if req.artifacts:
        merged = dict(job.artifacts or {})
        merged.update(req.artifacts)
        job.artifacts = merged

    # Create/update Video record if video metadata provided
    if req.status == JobStatus.completed.value and req.video:
        from gpcg.domain.models import Video, VideoStatus

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
                file_path=vdata.get("file_path", ""),
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

    # Update gameplay source status for mapping jobs
    if job.gameplay_source_id and job.type == JobType.mapping.value:
        source = db.query(GameplaySource).filter(GameplaySource.id == job.gameplay_source_id).first()
        if source:
            if req.status == JobStatus.completed.value:
                source.processing_status = GameplayProcessingStatus.ready.value
            elif req.status == JobStatus.failed.value:
                source.processing_status = GameplayProcessingStatus.failed.value

    db.commit()
    log.info(f"Job #{job.id} result: {req.status}")
    return {"ok": True}


# ── Gameplay download (streaming) ────────────────────────────────────────────


@router.get("/gameplays/{source_id}/download")
def download_gameplay(
    source_id: int,
    token: str,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Stream a gameplay file from VPS temp storage to the worker.

    Requires a valid upload_token (generated when the mapping job was claimed).
    The token is one-time use — invalidated after download is confirmed.
    """
    source = db.query(GameplaySource).filter(GameplaySource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Gameplay source not found")

    if not source.upload_token or not secrets.compare_digest(source.upload_token, token):
        raise HTTPException(status_code=403, detail="Invalid or expired download token")

    if not source.storage_key:
        raise HTTPException(status_code=404, detail="No file available for download")

    file_path = _resolve_storage_path(source.storage_key)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Temp file no longer available on VPS")

    log.info(f"Worker downloading gameplay #{source_id} ({source.filename})")
    return FileResponse(
        path=str(file_path),
        media_type="video/mp4",
        filename=source.filename,
    )


# ── Confirm download (checksum verification + cleanup) ───────────────────────


@router.post("/gameplays/{source_id}/confirm-download")
def confirm_download(
    source_id: int,
    req: ConfirmDownloadRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Worker confirms download with checksum verification.

    Flow:
    1. Worker computes SHA256 of downloaded file
    2. Sends checksum to VPS
    3. VPS compares against stored file_hash
    4. If match: mark as DOWNLOADED, invalidate token, delete temp file
    5. If mismatch: return error, worker should retry

    The temp file is ONLY deleted after successful checksum verification.
    """
    source = db.query(GameplaySource).filter(GameplaySource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Gameplay source not found")

    # Verify checksum
    if not secrets.compare_digest(req.checksum.lower(), source.file_hash.lower()):
        log.warning(
            f"Download confirmation failed for #{source_id}: "
            f"checksum mismatch (expected={source.file_hash[:16]}..., got={req.checksum[:16]}...)"
        )
        return JSONResponse(
            status_code=422,
            content={"ok": False, "error": "checksum_mismatch"},
        )

    # Mark as downloaded
    source.processing_status = GameplayProcessingStatus.downloaded.value
    source.downloaded_at = _utcnow()
    source.downloaded_by_worker = req.worker_id
    source.upload_token = None  # invalidate token

    # Delete temp file from VPS
    if source.storage_key:
        file_path = _resolve_storage_path(source.storage_key)
        if file_path.exists():
            try:
                file_path.unlink()
                log.info(f"Deleted temp file {source.storage_key} from VPS (download confirmed)")
            except OSError as e:
                log.warning(f"Failed to delete temp file {file_path}: {e}")

    db.commit()
    log.info(f"Gameplay #{source_id} download confirmed by '{req.worker_id}'")
    return {"ok": True, "processing_status": source.processing_status}


# ── Mapping result (events only, no frames) ──────────────────────────────────


@router.post("/gameplays/{source_id}/mapping-result")
def submit_mapping_result(
    source_id: int,
    req: MappingResultRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Worker sends gameplay analysis results (events/metadata only).

    The worker runs GameplayAnalyzer locally (VLM + ASR + merge + score).
    It sends ONLY the structured event data — never frames, crops, caches,
    embeddings, or intermediate files. Those stay on the worker's local disk.

    Events are persisted to the GameplayEvent table. The GameplaySource's
    metadata_json is updated with analysis status and compatibility flags.
    """
    source = db.query(GameplaySource).filter(GameplaySource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Gameplay source not found")

    # Delete existing events for this source (re-mapping replaces)
    db.query(GameplayEvent).filter(GameplayEvent.source_id == source_id).delete()
    db.flush()

    # Insert new events
    for evt_data in req.events:
        evt = GameplayEvent(
            source_id=source_id,
            start_time=evt_data.get("start_time", 0.0),
            end_time=evt_data.get("end_time", 0.0),
            event_type=evt_data.get("event_type", "unknown"),
            description=evt_data.get("description", ""),
            characters=evt_data.get("characters", []),
            location=evt_data.get("location"),
            actions=evt_data.get("actions", []),
            tags=evt_data.get("tags", []),
            transcript=evt_data.get("transcript", ""),
            visual_confidence=evt_data.get("visual_confidence", 0.0),
            interesting_score=evt_data.get("interesting_score", 0.0),
            analysis_version=req.analysis_version,
            metadata_json=evt_data.get("metadata_json", {}),
        )
        db.add(evt)

    # Update source metadata with analysis info
    meta = dict(source.metadata_json or {})
    meta["analysis"] = {
        "status": "ready",
        "version": req.analysis_version,
        "config_hash": req.config_hash,
        "event_count": len(req.events),
        "analyzed_at": _utcnow().isoformat(),
        "error": None,
    }
    if req.compatibility:
        meta["compatibility"] = req.compatibility
    source.metadata_json = meta
    source.processing_status = GameplayProcessingStatus.mapped.value

    db.commit()
    log.info(
        f"Mapping result for #{source_id}: {len(req.events)} events persisted "
        f"(version={req.analysis_version})"
    )
    return {"ok": True, "events_persisted": len(req.events)}


# ── Video upload (worker → VPS) ──────────────────────────────────────────────


@router.post("/jobs/{job_id}/upload-video")
async def upload_video(
    job_id: int,
    file: UploadFile = File(...),
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Worker uploads the final rendered video to the VPS.

    The video is stored in the VPS videos directory. In the future, this
    may be replaced by direct YouTube upload (worker → YouTube, VPS only
    stores the URL).
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    settings = get_settings()
    storage_key = f"job_{job_id}_{file.filename or 'output.mp4'}"
    dest_path = settings.videos_dir / storage_key

    # Stream upload to disk
    with open(dest_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):  # 1MB chunks
            f.write(chunk)

    file_size = dest_path.stat().st_size
    log.info(f"Video uploaded for job #{job_id}: {storage_key} ({file_size} bytes)")

    # Update or create Video record
    from gpcg.domain.models import Video, VideoStatus

    video = db.query(Video).filter(Video.job_id == job.id).first()
    if video:
        video.storage_key = storage_key
        video.file_path = str(dest_path)
        video.status = VideoStatus.ready.value
    else:
        video = Video(
            user_id=job.user_id,
            job_id=job.id,
            content_plan_id=job.content_plan_id,
            game_id=job.game_id,
            file_path=str(dest_path),
            storage_key=storage_key,
            status=VideoStatus.ready.value,
        )
        db.add(video)

    db.commit()
    return {"ok": True, "storage_key": storage_key, "file_size": file_size}


# ── Create mapping job (called by frontend when user uploads gameplay) ───────


@router.post("/gameplays/{source_id}/create-mapping-job")
def create_mapping_job(
    source_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a mapping job for a gameplay source.

    Called by the frontend after a gameplay is uploaded. The job will be
    picked up by a worker with 'mapping' capability.

    Uses BI Identity SSO auth (human user), not worker auth.
    """
    source = db.query(GameplaySource).filter(GameplaySource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Gameplay source not found")

    # Verify ownership
    if source.user_id and source.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your gameplay")

    if source.processing_status in (
        GameplayProcessingStatus.mapping.value,
        GameplayProcessingStatus.mapped.value,
        GameplayProcessingStatus.ready.value,
    ):
        raise HTTPException(
            status_code=409,
            detail=f"Gameplay already processed or processing (status={source.processing_status})",
        )

    # Generate download token for the worker
    token = _generate_upload_token()
    source.upload_token = token
    source.processing_status = GameplayProcessingStatus.waiting_worker.value

    # Create mapping job
    job = Job(
        job_uuid=str(uuid.uuid4()),
        type=JobType.mapping.value,
        status=JobStatus.queued.value,
        stage=JobStage.download.value,
        gameplay_source_id=source.id,
        user_id=source.user_id,
        game_id=source.game_id,
        priority=JobPriority.normal.value,
        required_capabilities=[WorkerCapability.mapping.value],
        artifacts={},
    )
    db.add(job)
    db.flush()

    db.commit()
    log.info(f"Created mapping job #{job.id} for gameplay #{source_id}")
    return {
        "job_id": job.id,
        "job_uuid": job.job_uuid,
        "processing_status": source.processing_status,
    }


# ── Generation job data (worker fetches all data needed for generation) ──────


@router.get("/jobs/{job_id}/data")
def get_job_data(
    job_id: int,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Fetch all data the worker needs to run a generation job.

    Returns the job + all related records (game, facts, gameplay sources,
    gameplay events, content plans, scripts, automation config) in a single
    payload. The worker uses this to populate a local temp DB and run
    GenerationService locally.
    """
    from gpcg.domain.models import (
        Game, Fact, ContentPlan, Script, GameplayAsset, Automation,
    )

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    data: dict = {"job": _serialize_job(job)}

    # Game
    if job.game_id:
        game = db.query(Game).filter(Game.id == job.game_id).first()
        if game:
            data["game"] = {
                "id": game.id, "canonical_name": game.canonical_name,
                "aliases": game.aliases, "camera_type": game.camera_type,
                "platforms": game.platforms, "capture_sources": game.capture_sources,
                "metadata_json": game.metadata_json,
            }

    # Content plan (if exists)
    if job.content_plan_id:
        plan = db.query(ContentPlan).filter(ContentPlan.id == job.content_plan_id).first()
        if plan:
            data["content_plan"] = {
                "id": plan.id, "game_id": plan.game_id,
                "fact_id": plan.fact_id, "background_game_id": plan.background_game_id,
                "format": plan.format, "target_duration": plan.target_duration,
                "topic": plan.topic, "hook": plan.hook, "tone": plan.tone,
                "energy": plan.energy, "music_mood": plan.music_mood,
                "visual_strategy": plan.visual_strategy,
                "metadata_json": plan.metadata_json,
            }
            # Scripts for this plan
            scripts = db.query(Script).filter(Script.content_plan_id == plan.id).all()
            data["scripts"] = [{
                "id": s.id, "content_plan_id": s.content_plan_id,
                "draft": s.draft, "optimized": s.optimized, "final": s.final,
                "status": s.status, "char_count": s.char_count,
                "originality_score": s.originality_score,
                "originality_report": s.originality_report,
                "rewrite_count": s.rewrite_count,
            } for s in scripts]

    # Facts for the game
    if job.game_id:
        facts = db.query(Fact).filter(Fact.game_id == job.game_id).all()
        data["facts"] = [{
            "id": f.id, "game_id": f.game_id, "document_id": f.document_id,
            "category": f.category, "claim": f.claim,
            "source_ref": f.source_ref, "verification": f.verification,
            "quality_score": f.quality_score, "novelty_score": f.novelty_score,
            "used_count": f.used_count, "metadata_json": f.metadata_json,
        } for f in facts]

    # Gameplay sources + events for the game (and general-topic sources)
    sources_query = db.query(GameplaySource).filter(
        GameplaySource.user_id == job.user_id
    )
    if job.game_id:
        sources_query = sources_query.filter(
            (GameplaySource.game_id == job.game_id) | (GameplaySource.game_id.is_(None))
        )
    sources = sources_query.all()
    data["gameplay_sources"] = []
    for src in sources:
        src_data = {
            "id": src.id, "game_id": src.game_id,
            "filename": src.filename, "file_hash": src.file_hash,
            "file_size": src.file_size, "duration": src.duration,
            "width": src.width, "height": src.height, "fps": src.fps,
            "codec": src.codec, "has_audio": src.has_audio,
            "processing_status": src.processing_status,
            "metadata_json": src.metadata_json,
            "file_path": src.file_path,  # worker resolves to local path
        }
        # Events for this source
        events = db.query(GameplayEvent).filter(GameplayEvent.source_id == src.id).all()
        src_data["events"] = [{
            "id": e.id, "source_id": e.source_id,
            "start_time": e.start_time, "end_time": e.end_time,
            "event_type": e.event_type, "description": e.description,
            "characters": e.characters, "location": e.location,
            "actions": e.actions, "tags": e.tags,
            "transcript": e.transcript,
            "visual_confidence": e.visual_confidence,
            "interesting_score": e.interesting_score,
            "analysis_version": e.analysis_version,
            "metadata_json": e.metadata_json,
        } for e in events]
        data["gameplay_sources"].append(src_data)

    # Automation config (for video customization settings)
    automation = db.query(Automation).filter(Automation.user_id == job.user_id).first()
    if automation:
        data["automation"] = {
            "id": automation.id, "user_id": automation.user_id,
            "name": automation.name, "status": automation.status,
            "config": automation.config, "upload_config": automation.upload_config,
        }

    return data


class SyncResultRequest(BaseModel):
    """Worker sends back records created/updated during generation."""
    content_plan: Optional[dict] = None
    script: Optional[dict] = None
    video: Optional[dict] = None
    artifacts: dict = Field(default_factory=dict)


@router.post("/jobs/{job_id}/sync")
def sync_job_result(
    job_id: int,
    req: SyncResultRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Sync records created/updated by the worker during generation.

    The worker runs GenerationService locally (against a temp DB) and then
    sends back the records it created/updated: ContentPlan, Script, Video,
    and updated Job artifacts.
    """
    from gpcg.domain.models import ContentPlan, Script, Video as VideoModel, VideoStatus

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Update job artifacts
    if req.artifacts:
        merged = dict(job.artifacts or {})
        merged.update(req.artifacts)
        job.artifacts = merged

    # Sync ContentPlan
    if req.content_plan:
        plan_id = req.content_plan.get("id")
        if plan_id:
            plan = db.query(ContentPlan).filter(ContentPlan.id == plan_id).first()
            if plan:
                for k, v in req.content_plan.items():
                    if k != "id" and hasattr(plan, k):
                        setattr(plan, k, v)
        else:
            plan = ContentPlan(
                user_id=job.user_id,
                game_id=req.content_plan.get("game_id", job.game_id),
                fact_id=req.content_plan.get("fact_id"),
                background_game_id=req.content_plan.get("background_game_id"),
                format=req.content_plan.get("format", "youtube_short"),
                target_duration=req.content_plan.get("target_duration", 60),
                topic=req.content_plan.get("topic", ""),
                hook=req.content_plan.get("hook", ""),
                tone=req.content_plan.get("tone", "curious"),
                energy=req.content_plan.get("energy", 0.7),
                music_mood=req.content_plan.get("music_mood", "neutral"),
                visual_strategy=req.content_plan.get("visual_strategy", "gameplay_compilation"),
                metadata_json=req.content_plan.get("metadata_json", {}),
            )
            db.add(plan)
            db.flush()
            job.content_plan_id = plan.id

    # Sync Script
    if req.script:
        script_id = req.script.get("id")
        if script_id:
            script = db.query(Script).filter(Script.id == script_id).first()
            if script:
                for k, v in req.script.items():
                    if k != "id" and hasattr(script, k):
                        setattr(script, k, v)
        elif job.content_plan_id:
            script = Script(
                content_plan_id=job.content_plan_id,
                draft=req.script.get("draft", ""),
                optimized=req.script.get("optimized", ""),
                final=req.script.get("final", ""),
                status=req.script.get("status", "final"),
                char_count=req.script.get("char_count", 0),
                originality_score=req.script.get("originality_score"),
                originality_report=req.script.get("originality_report"),
                rewrite_count=req.script.get("rewrite_count", 0),
            )
            db.add(script)

    # Sync Video
    if req.video:
        video_id = req.video.get("id")
        if video_id:
            video = db.query(VideoModel).filter(VideoModel.id == video_id).first()
            if video:
                for k, v in req.video.items():
                    if k != "id" and hasattr(video, k):
                        setattr(video, k, v)
        else:
            video = VideoModel(
                user_id=job.user_id,
                job_id=job.id,
                content_plan_id=job.content_plan_id,
                game_id=job.game_id,
                file_path=req.video.get("file_path", ""),
                storage_key=req.video.get("storage_key"),
                duration=req.video.get("duration", 0.0),
                width=req.video.get("width", 0),
                height=req.video.get("height", 0),
                qa_score=req.video.get("qa_score", 0.0),
                qa_report=req.video.get("qa_report", {}),
                status=req.video.get("status", VideoStatus.ready.value),
                youtube_url=req.video.get("youtube_url"),
                youtube_video_id=req.video.get("youtube_video_id"),
            )
            db.add(video)

    db.commit()
    return {"ok": True}


# ── Gameplay events query (for generation: semantic clip selection) ──────────


@router.get("/gameplays/{source_id}/events")
def get_gameplay_events(
    source_id: int,
    event_type: Optional[str] = None,
    min_interesting: float = 0.0,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Query gameplay events for a source (used by generation pipeline).

    The worker's GameplayRetriever uses this to find semantically matching
    clips for video generation.
    """
    query = db.query(GameplayEvent).filter(GameplayEvent.source_id == source_id)
    if event_type:
        query = query.filter(GameplayEvent.event_type == event_type)
    if min_interesting > 0:
        query = query.filter(GameplayEvent.interesting_score >= min_interesting)
    events = query.order_by(GameplayEvent.start_time.asc()).all()
    return {
        "source_id": source_id,
        "events": [{
            "id": e.id, "start_time": e.start_time, "end_time": e.end_time,
            "event_type": e.event_type, "description": e.description,
            "characters": e.characters, "location": e.location,
            "actions": e.actions, "tags": e.tags,
            "transcript": e.transcript,
            "visual_confidence": e.visual_confidence,
            "interesting_score": e.interesting_score,
        } for e in events]
    }
