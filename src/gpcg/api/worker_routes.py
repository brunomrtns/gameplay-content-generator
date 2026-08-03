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
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.domain.models import (
    Document,
    GameplayAsset,
    GameplayClipUsage,
    GameplayEvent,
    GameplayProcessingStatus,
    GameplaySource,
    IngestionStatus,
    Job,
    JobPriority,
    JobStage,
    JobStatus,
    JobType,
    KnowledgeChunk,
    User,
    Worker,
    WorkerCapability,
    WorkerStatus,
)
from gpcg.infrastructure.auth import get_current_user
from gpcg.infrastructure.database import get_db, session_scope

log = logging.getLogger(__name__)
router = APIRouter(tags=["workers"])


def _ensure_dict(value) -> dict:
    """Coerce a JSON column value to dict.

    SQLite/Postgres JSON columns normally return dicts, but older rows or
    rows written by other code paths may store a JSON string. This helper
    transparently parses strings so callers can always do dict operations.
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        import json
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            log.warning(f"_ensure_dict: could not parse JSON string: {value[:80]!r}")
            return {}
    return {}


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
    # Media metadata from ffprobe (synced back to GameplaySource so the VPS
    # has accurate duration/width/height without needing to probe the file)
    duration: Optional[float] = Field(None, description="File duration in seconds (from ffprobe)")
    width: Optional[int] = Field(None, description="Video width in pixels")
    height: Optional[int] = Field(None, description="Video height in pixels")
    fps: Optional[float] = Field(None, description="Frames per second")
    codec: Optional[str] = Field(None, description="Video codec name")
    has_audio: Optional[bool] = Field(None, description="Whether the file has an audio stream")


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


def _cleanup_orphan_gameplays(db: Session) -> int:
    """Delete gameplay files from VPS temp_uploads that were never downloaded.

    A gameplay is "orphan" if:
    - It has a storage_key (file exists on VPS)
    - processing_status is still 'uploaded' (worker never claimed/downloaded)
    - created_at is older than 1 hour

    Returns the number of files deleted.
    """
    from datetime import timedelta
    settings = get_settings()
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
    Also cleans up orphan gameplay files (uploaded but never downloaded).
    """
    # Clean up orphan gameplays (runs on every poll — cheap query)
    _cleanup_orphan_gameplays(db)

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


def _serialize_job(job: Job) -> dict:
    """Serialize a Job for the worker API response."""
    return {
        "id": job.id,
        "job_uuid": job.job_uuid,
        "user_id": job.user_id,
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


def _serialize_game_for_job(job: Job, db: Session) -> Optional[dict]:
    """Serialize game info if the job has a game_id (enrichment, generation jobs)."""
    if not job.game_id:
        return None
    from gpcg.domain.models import Game
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
        merged = {**_ensure_dict(job.artifacts), **req.artifacts}
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
        merged = {**_ensure_dict(job.artifacts), **req.artifacts}
        job.artifacts = merged
        has_social = "social_title" in merged
        print(f"[RESULT] job #{job_id}: {len(req.artifacts)} artifacts received, social_title present: {has_social}", flush=True)

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

        # Remember to auto-publish after commit (outside the transaction)
        _pending_auto_publish = (vdata.get("storage_key") and not vdata.get("youtube_url"))
    else:
        _pending_auto_publish = False

    # Update gameplay source status for mapping jobs
    if job.gameplay_source_id and job.type == JobType.mapping.value:
        source = db.query(GameplaySource).filter(GameplaySource.id == job.gameplay_source_id).first()
        if source:
            if req.status == JobStatus.completed.value:
                source.processing_status = GameplayProcessingStatus.ready.value
                source.ingestion_status = IngestionStatus.ready.value
            elif req.status == JobStatus.failed.value:
                source.processing_status = GameplayProcessingStatus.failed.value

    db.commit()
    log.info(f"Job #{job.id} result: {req.status}")

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
    from gpcg.domain.models import Automation, Video as VideoModel, VideoStatus, ContentPlan
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

    def _stream_file(path: Path, chunk_size: int = 1024 * 1024):
        """Stream file in 1MB chunks to avoid loading entire file into RAM."""
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        _stream_file(file_path),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'attachment; filename="{source.filename}"',
            "Content-Length": str(file_path.stat().st_size),
        },
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

    # Sync media metadata from worker's ffprobe results. The worker probes
    # the file locally and sends duration/width/height/etc. back so the VPS
    # doesn't need to access the (now-deleted) temp file.
    if req.duration is not None:
        source.duration = req.duration
    if req.width is not None:
        source.width = req.width
    if req.height is not None:
        source.height = req.height
    if req.fps is not None:
        source.fps = req.fps
    if req.codec is not None:
        source.codec = req.codec
    if req.has_audio is not None:
        source.has_audio = req.has_audio

    # Auto-create a GameplayAsset covering the full duration of the source so
    # the GameplaySelector always has at least one asset to choose from.
    # Uses the (now-synced) source.duration.
    effective_duration = source.duration or req.duration or 0.0
    existing_asset = db.query(GameplayAsset).filter(
        GameplayAsset.source_id == source_id
    ).first()
    if not existing_asset:
        asset = GameplayAsset(
            source_id=source_id,
            label="full_gameplay",
            start_sec=0,
            end_sec=effective_duration,
            duration=effective_duration,
            used_count=0,
        )
        db.add(asset)
        log.info(f"Auto-created full_gameplay asset for source #{source_id} (dur={effective_duration}s)")
    elif existing_asset.duration == 0.0 and effective_duration > 0:
        # Fix asset that was created with duration=0 before duration sync
        existing_asset.end_sec = effective_duration
        existing_asset.duration = effective_duration

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

    # Validate the uploaded video file is a valid media file
    if file_size < 10000:
        log.error(f"Video file too small for job #{job_id}: {file_size} bytes — likely corrupted")
        dest_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded video file is too small — likely corrupted")

    try:
        from gpcg.infrastructure.media import probe
        info = probe(dest_path)
        if not info or info.duration is None or info.duration < 1.0:
            log.error(f"Invalid video for job #{job_id}: probe failed or duration < 1s")
            dest_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="Uploaded video is invalid or too short")
        log.info(f"Video validated for job #{job_id}: duration={info.duration:.1f}s {info.width}x{info.height}")
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Video validation failed for job #{job_id}: {e}")
        dest_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Video validation failed: {e}")

    # Generate thumbnail on the VPS from the uploaded video
    thumb_path: Optional[Path] = None
    try:
        from gpcg.infrastructure.media import generate_thumbnail, probe
        thumb_path = settings.videos_dir / f"{dest_path.stem}_thumb.jpg"
        info = probe(dest_path)
        at = min(1.0, max(0.1, (info.duration or 2.0) / 2))
        generate_thumbnail(dest_path, thumb_path, at=at)
        log.info(f"Thumbnail generated for job #{job_id}: {thumb_path.name}")
    except Exception as e:
        log.warning(f"Thumbnail generation failed for job #{job_id}: {e}")
        thumb_path = None

    # Update or create Video record
    from gpcg.domain.models import Video, VideoStatus

    video = db.query(Video).filter(Video.job_id == job.id).first()
    if video:
        video.storage_key = storage_key
        video.file_path = str(dest_path)
        video.status = VideoStatus.ready.value
        if thumb_path:
            video.thumbnail_path = str(thumb_path)
    else:
        video = Video(
            user_id=job.user_id,
            job_id=job.id,
            content_plan_id=job.content_plan_id,
            game_id=job.game_id,
            file_path=str(dest_path),
            storage_key=storage_key,
            status=VideoStatus.ready.value,
            thumbnail_path=str(thumb_path) if thumb_path else None,
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

    # Facts for the game — REFACTORY_V2: filter by visibility (own + shared + public)
    if job.game_id:
        from gpcg.domain.visibility import visible_to_user
        fact_vis = visible_to_user(Fact.user_id, Fact.is_public, job.user_id)
        facts = db.query(Fact).filter(
            Fact.game_id == job.game_id, fact_vis
        ).all()
        data["facts"] = [{
            "id": f.id, "game_id": f.game_id, "document_id": f.document_id,
            "category": f.category, "claim": f.claim,
            "source_ref": f.source_ref, "verification": f.verification,
            "quality_score": f.quality_score, "novelty_score": f.novelty_score,
            "used_count": f.used_count, "metadata_json": f.metadata_json,
        } for f in facts]

    # Knowledge items for the game (V2 content intelligence)
    # REFACTORY_V2: filter by visibility (own + shared + public)
    if job.game_id:
        try:
            from gpcg.domain.models import KnowledgeItem
            from gpcg.domain.visibility import visible_to_user as _ki_vis
            ki_vis = _ki_vis(KnowledgeItem.user_id, KnowledgeItem.is_public, job.user_id)
            ki_list = db.query(KnowledgeItem).filter(
                KnowledgeItem.game_id == job.game_id, ki_vis
            ).limit(50).all()
            data["knowledge_items"] = [{
                "id": ki.id, "user_id": ki.user_id, "game_id": ki.game_id,
                "source_type": ki.source_type, "title": ki.title,
                "summary": ki.summary, "content": ki.content,
                "url": ki.url, "published_at": ki.published_at.isoformat() if ki.published_at else None,
                "collected_at": ki.collected_at.isoformat() if ki.collected_at else None,
                "metadata_json": ki.metadata_json,
            } for ki in ki_list]
        except Exception:
            data["knowledge_items"] = []

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
        # Assets for this source (clips that the GameplaySelector uses)
        assets = db.query(GameplayAsset).filter(GameplayAsset.source_id == src.id).all()
        src_data["assets"] = [{
            "id": a.id, "source_id": a.source_id,
            "label": a.label, "start_sec": a.start_sec,
            "end_sec": a.end_sec, "duration": a.duration,
            "used_count": a.used_count,
            "metadata_json": a.metadata_json,
        } for a in assets]
        # V2: Clip usage records (so worker can avoid reusing segments)
        clip_usages = db.query(GameplayClipUsage).filter(GameplayClipUsage.source_id == src.id).all()
        src_data["clip_usages"] = [{
            "id": cu.id, "video_id": cu.video_id, "source_id": cu.source_id,
            "start_sec": cu.start_sec, "end_sec": cu.end_sec,
            "duration": cu.duration,
        } for cu in clip_usages]
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
    clip_usages: Optional[list] = None  # V2: clip usage records for cross-job avoidance


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
        # IMPORTANT: create a NEW dict (copy) so SQLAlchemy detects the change.
        # JSON columns don't track in-place mutations — assigning the same
        # object back is a no-op for the ORM.
        merged = {**_ensure_dict(job.artifacts), **req.artifacts}
        job.artifacts = merged
        has_social = "social_title" in merged
        print(f"[SYNC] job #{job_id}: {len(req.artifacts)} artifacts received, social_title present: {has_social}", flush=True)
        log.info(f"Sync job #{job_id}: merged {len(req.artifacts)} artifacts, keys={list(merged.keys())[:10]}")

    # Sync ContentPlan
    # NOTE: The content_plan id from the remote worker is the LOCAL DB id,
    # not the VPS DB id. The local DB id may collide with an existing VPS DB id.
    # So we NEVER look up by local id. Instead, we check if this job already
    # has a content_plan_id in the VPS DB, and if so, update that. Otherwise,
    # always create a new ContentPlan.
    print(f"[SYNC] job #{job_id}: content_plan={req.content_plan is not None}, script={req.script is not None}, video={req.video is not None}", flush=True)
    if req.content_plan:
        plan = None
        # Check if the job already has a content_plan_id in the VPS DB
        if job.content_plan_id:
            plan = db.query(ContentPlan).filter(ContentPlan.id == job.content_plan_id).first()
            if plan:
                for k, v in req.content_plan.items():
                    if k != "id" and hasattr(plan, k):
                        setattr(plan, k, v)
        if not plan:
            print(f"[SYNC] job #{job_id}: creating new ContentPlan in VPS DB (local id={req.content_plan.get('id')}, topic={req.content_plan.get('topic','')})", flush=True)
            # Create new ContentPlan in VPS DB (local DB id is irrelevant)
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
    # NOTE: Same as ContentPlan — the script id is from the LOCAL DB.
    # Never look up by local id; check if the job's content_plan already has a script.
    if req.script:
        script = None
        if job.content_plan_id:
            script = db.query(Script).filter(Script.content_plan_id == job.content_plan_id).first()
            if script:
                for k, v in req.script.items():
                    if k != "id" and hasattr(script, k):
                        setattr(script, k, v)
        if not script and job.content_plan_id:
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
    # NOTE: Same as ContentPlan — the video id is from the LOCAL DB.
    # Never look up by local id; check if the job already has a video.
    if req.video:
        video = db.query(VideoModel).filter(VideoModel.job_id == job.id).first()
        if video:
            for k, v in req.video.items():
                if k != "id" and hasattr(video, k):
                    setattr(video, k, v)
        if not video:
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
        db.flush()

    # V2: Sync clip usage records (so future jobs avoid same gameplay segments)
    if req.clip_usages:
        video = db.query(VideoModel).filter(VideoModel.job_id == job.id).first()
        if video:
            for cu_data in req.clip_usages:
                source_id = cu_data.get("source_id")
                start_sec = cu_data.get("start_sec", 0.0)
                end_sec = cu_data.get("end_sec", 0.0)
                if source_id and end_sec > start_sec:
                    # Check if already exists (avoid duplicates on re-sync)
                    existing = db.query(GameplayClipUsage).filter(
                        GameplayClipUsage.video_id == video.id,
                        GameplayClipUsage.source_id == source_id,
                        GameplayClipUsage.start_sec == start_sec,
                    ).first()
                    if not existing:
                        db.add(GameplayClipUsage(
                            video_id=video.id,
                            source_id=source_id,
                            start_sec=start_sec,
                            end_sec=end_sec,
                            duration=end_sec - start_sec,
                        ))
            log.info(f"Synced {len(req.clip_usages)} clip usage records for job #{job_id}")

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


# ── Knowledge document endpoints (worker downloads + indexes documents) ──────


@router.get("/documents/{doc_id}/download")
def download_document(
    doc_id: int,
    token: str,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Stream a knowledge document file from VPS to the worker.

    Requires a valid upload_token (generated when the knowledge_index job
    was created). The token is invalidated after download is confirmed.
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if not doc.upload_token or doc.upload_token != token:
        raise HTTPException(status_code=403, detail="Invalid or expired download token")

    file_path = Path(doc.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Document file not found on VPS")

    log.info(f"Worker downloading document {doc_id}: {doc.filename} ({doc.file_size} bytes)")
    return FileResponse(
        path=str(file_path),
        media_type="application/octet-stream",
        filename=doc.filename,
    )


class ConfirmDocumentDownloadRequest(BaseModel):
    checksum: str
    worker_id: str


@router.post("/documents/{doc_id}/confirm-download")
def confirm_document_download(
    doc_id: int,
    req: ConfirmDocumentDownloadRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Worker confirms document download with checksum verification.

    Verifies SHA256 checksum, invalidates the download token, and optionally
    deletes the file from VPS (the worker has its own copy now).
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if not doc.file_hash:
        raise HTTPException(status_code=400, detail="No file_hash stored for this document")

    if req.checksum != doc.file_hash:
        log.warning(f"Checksum mismatch for document {doc_id}: expected {doc.file_hash[:16]}..., got {req.checksum[:16]}...")
        raise HTTPException(status_code=400, detail="Checksum mismatch — file may be corrupted")

    # Invalidate token
    doc.upload_token = None
    db.commit()

    log.info(f"Document {doc_id} download confirmed by worker '{req.worker_id}'")
    return {"ok": True, "doc_id": doc_id}


class IndexingResultRequest(BaseModel):
    """Worker sends knowledge chunks back to VPS after indexing."""
    chunks: list[dict] = Field(default_factory=list)
    chunk_count: int = 0
    error: str = ""


@router.post("/documents/{doc_id}/indexing-result")
def submit_indexing_result(
    doc_id: int,
    req: IndexingResultRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Worker sends indexed knowledge chunks back to VPS.

    The worker has parsed the document (possibly with OCR), chunked it,
    generated embeddings (via Ollama), and now sends the chunks to VPS
    for storage. The VPS stores them in the knowledge_chunks table for
    retrieval during video generation.
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if req.error:
        doc.knowledge_status = "error"
        db.commit()
        log.warning(f"Document {doc_id} indexing failed: {req.error}")
        return {"ok": False, "error": req.error}

    # Delete existing chunks for this document (re-indexing case)
    db.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == doc_id).delete()

    # Insert new chunks
    for chunk_data in req.chunks:
        chunk = KnowledgeChunk(
            user_id=doc.user_id,
            document_id=doc_id,
            game_id=doc.game_id,
            content=chunk_data["content"],
            embedding=chunk_data.get("embedding", []),
            chunk_index=chunk_data.get("chunk_index", 0),
            section=chunk_data.get("section"),
            char_start=chunk_data.get("char_start", 0),
            char_end=chunk_data.get("char_end", 0),
            embedding_model=chunk_data.get("embedding_model"),
        )
        db.add(chunk)

    # Update document status
    doc.knowledge_status = "indexed"
    doc.chunk_count = len(req.chunks)
    doc.text_extracted = True
    db.commit()

    log.info(f"Document {doc_id} indexed: {len(req.chunks)} chunks stored on VPS")
    return {"ok": True, "chunk_count": len(req.chunks)}


# ── V2: Enrichment + Content Collection sync endpoints ──────────────────────
# These endpoints receive results from the remote worker (PC local) after
# processing game_enrich and content_collect jobs locally (where Ollama
# and Wikidata/Wikipedia are accessible without VPS IP blocks).


class EnrichmentResultRequest(BaseModel):
    """Worker sends enriched Game data back to VPS."""
    description: Optional[str] = None
    developer: Optional[str] = None
    publisher: Optional[str] = None
    franchise: Optional[str] = None
    genres: list = []
    themes: list = []
    lore_summary: Optional[str] = None
    release_date: Optional[str] = None
    external_ids: dict = {}
    aliases: list[str] = []
    enrichment_error: Optional[str] = None


@router.post("/jobs/{job_id}/sync-enrichment")
def sync_enrichment_result(
    job_id: int,
    req: EnrichmentResultRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Worker sends enriched Game data back to VPS.

    The worker has fetched Wikidata/Wikipedia data and generated lore
    with the local LLM. Now it sends the structured fields to VPS for
    storage in the games table.
    """
    from gpcg.domain.models import Game
    from gpcg.domain.game_registry import add_alias

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.game_id:
        raise HTTPException(status_code=400, detail="Job has no game_id")

    game = db.get(Game, job.game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    if req.enrichment_error:
        game.enrichment_error = req.enrichment_error
        db.commit()
        log.warning(f"Enrichment failed for game '{game.canonical_name}': {req.enrichment_error}")
        return {"ok": False, "error": req.enrichment_error}

    # Apply enriched fields
    if req.description is not None:
        game.description = req.description
    if req.developer is not None:
        game.developer = req.developer
    if req.publisher is not None:
        game.publisher = req.publisher
    if req.franchise is not None:
        game.franchise = req.franchise
    if req.genres:
        game.genres = req.genres
    if req.themes:
        game.themes = req.themes
    if req.lore_summary is not None:
        game.lore_summary = req.lore_summary
    if req.release_date:
        try:
            from datetime import datetime
            game.release_date = datetime.fromisoformat(req.release_date)
        except (ValueError, TypeError):
            pass
    if req.external_ids:
        game.external_ids = req.external_ids

    game.enriched_at = datetime.now(timezone.utc)
    game.enrichment_error = None

    # Add new aliases discovered during enrichment
    for alias in req.aliases:
        try:
            add_alias(db, game.id, alias, alias_type="alternative", source="enrichment")
        except Exception:
            pass  # duplicate alias — skip

    db.commit()
    log.info(f"Game '{game.canonical_name}' enriched: developer={game.developer}, franchise={game.franchise}")
    return {"ok": True, "game_id": game.id, "enriched_at": game.enriched_at.isoformat()}


class KnowledgeItemSyncItem(BaseModel):
    """A single KnowledgeItem to sync from worker to VPS."""
    title: str
    content: str
    item_type: str
    source_type: str
    source_url: Optional[str] = None
    source_name: Optional[str] = None
    published_at: Optional[str] = None
    editorial_score: float = 0.0
    franchise: Optional[str] = None
    developer: Optional[str] = None
    game_id: Optional[int] = None
    content_hash: str = ""
    tags: list = []


class ContentCollectionResultRequest(BaseModel):
    """Worker sends collected KnowledgeItems back to VPS."""
    items: list[KnowledgeItemSyncItem] = []
    cleaned_count: int = 0
    error: Optional[str] = None


@router.post("/jobs/{job_id}/sync-knowledge-items")
def sync_knowledge_items(
    job_id: int,
    req: ContentCollectionResultRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Worker sends collected KnowledgeItems back to VPS.

    The worker has collected RSS feeds, scored items with the local LLM,
    and now sends the structured items to VPS for storage.
    """
    from gpcg.domain.models import KnowledgeItem, KnowledgeItemStatus
    from sqlalchemy import select
    import hashlib as _hashlib

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if req.error:
        log.warning(f"Content collection job {job_id} failed: {req.error}")
        return {"ok": False, "error": req.error}

    # Dedup by content_hash — skip items that already exist
    existing_hashes = set()
    if req.items:
        hashes = [item.content_hash for item in req.items if item.content_hash]
        if hashes:
            existing = db.execute(
                select(KnowledgeItem.content_hash).where(
                    KnowledgeItem.content_hash.in_(hashes)
                )
            ).scalars().all()
            existing_hashes = set(existing)

    inserted = 0
    skipped = 0
    for item in req.items:
        if item.content_hash and item.content_hash in existing_hashes:
            skipped += 1
            continue
        ki = KnowledgeItem(
            game_id=item.game_id,
            title=item.title,
            content=item.content,
            item_type=item.item_type,
            source_type=item.source_type,
            source_url=item.source_url,
            source_name=item.source_name,
            editorial_score=item.editorial_score,
            status=KnowledgeItemStatus.fresh.value,
            franchise=item.franchise,
            developer=item.developer,
            content_hash=item.content_hash or _hashlib.sha256(
                f"{item.title}:{item.content}".encode()
            ).hexdigest()[:16],
            tags=item.tags,
        )
        if item.published_at:
            try:
                from datetime import datetime
                ki.published_at = datetime.fromisoformat(item.published_at)
            except (ValueError, TypeError):
                pass
        db.add(ki)
        inserted += 1

    db.commit()
    log.info(
        f"Content collection synced: {inserted} new items, {skipped} duplicates skipped, "
        f"{req.cleaned_count} old news cleaned (job #{job_id})"
    )
    return {"ok": True, "inserted": inserted, "skipped": skipped, "cleaned": req.cleaned_count}
