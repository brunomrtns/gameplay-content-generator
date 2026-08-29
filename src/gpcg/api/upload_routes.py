"""Resumable chunked upload endpoints for large gameplay recordings.

Architecture:
  - Client splits the file into fixed-size chunks (default 8 MiB) and
    uploads them one at a time (or with limited parallelism).
  - Server stores each chunk as a separate file in a per-upload temp dir.
  - When all chunks arrive, the server assembles them into the final file,
    computes the SHA-256 hash, deduplicates, and creates the GameplaySource.
  - If the connection drops, the client can query /status to learn which
    chunks are missing and resume only those — no re-upload from scratch.
  - Each chunk request is small (~8 MiB), so:
      * RAM usage is bounded (no 400 MiB read()).
      * No single long-running request that can timeout.
      * Multiple users can upload concurrently without blocking.

Endpoints:
  POST   /api/gameplays/upload/init    — start a session, returns upload_id
  GET    /api/gameplays/upload/{id}/status — which chunks have arrived
  POST   /api/gameplays/upload/{id}/chunk  — upload one chunk (multipart)
  POST   /api/gameplays/upload/{id}/complete — assemble + create record
  DELETE /api/gameplays/upload/{id}        — cancel + cleanup

All endpoints require authentication (get_current_user).
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.core.models import (
    Job,
    JobPriority,
    JobStage,
    JobStatus,
    JobType,
    User,
    WorkerCapability,
)
from gpcg.domains.games.models import GameplayProcessingStatus, GameplaySource, IngestionStatus
from gpcg.infrastructure.auth import get_current_user
from gpcg.infrastructure.database import get_db, session_scope

log = logging.getLogger(__name__)
router = APIRouter(prefix="/gameplays/upload", tags=["upload"])

# ── Config ────────────────────────────────────────────────────────────────────
CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB per chunk — balances throughput vs RAM
# Upload sessions expire after this many seconds of inactivity. The cleanup
# sweep (triggered on init) removes stale dirs to avoid filling the disk.
SESSION_TTL_SECONDS = 2 * 60 * 60  # 2 hours
# Meta file stored alongside chunks to track upload state.
META_FILENAME = ".upload-meta.json"


def _uploads_root() -> Path:
    """Root directory for all chunked uploads (created on demand)."""
    p = get_settings().temp_uploads_dir / "chunked"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _session_dir(upload_id: str) -> Path:
    return _uploads_root() / upload_id


def _meta_path(upload_id: str) -> Path:
    return _session_dir(upload_id) / META_FILENAME


def _read_meta(upload_id: str) -> dict:
    p = _meta_path(upload_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Upload session not found")
    return json.loads(p.read_text())


def _write_meta(upload_id: str, meta: dict) -> None:
    _meta_path(upload_id).write_text(json.dumps(meta))


def _cleanup_stale() -> None:
    """Remove upload sessions older than SESSION_TTL_SECONDS.

    Called opportunistically on each init — not a background thread, to keep
    the architecture simple. Under high load this is a cheap directory scan.
    """
    root = _uploads_root()
    now = time.time()
    for d in root.iterdir():
        if not d.is_dir():
            continue
        meta_file = d / META_FILENAME
        if not meta_file.exists():
            # No meta → very stale or orphaned. Remove if older than TTL.
            if d.stat().st_mtime < now - SESSION_TTL_SECONDS:
                shutil.rmtree(d, ignore_errors=True)
            continue
        meta = json.loads(meta_file.read_text())
        last_activity = meta.get("updated_at", meta.get("created_at", 0))
        if last_activity < now - SESSION_TTL_SECONDS:
            shutil.rmtree(d, ignore_errors=True)
            log.info(f"Cleaned stale upload session {d.name}")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/init")
def init_upload(
    filename: str = Form(...),
    file_size: int = Form(...),
    file_hash: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
):
    """Start a resumable upload session.

    Client provides the filename, total size in bytes, and optionally the
    SHA-256 hash (if known client-side via the SubtleCrypto API). The server
    returns an upload_id and the chunk size to use.

    If a file_hash is provided and matches an existing GameplaySource, the
    server short-circuits and returns {duplicate: true, source_id: ...}
    without creating a session — saving bandwidth.
    """
    _cleanup_stale()

    # Early dedup check if client provided a hash
    if file_hash:
        with session_scope() as session:
            existing = session.query(GameplaySource).filter(
                GameplaySource.user_id == user.id,
                GameplaySource.file_hash == file_hash,
            ).first()
            if existing:
                return {"duplicate": True, "source_id": existing.id}

    import secrets
    upload_id = secrets.token_urlsafe(16)
    total_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE

    _session_dir(upload_id).mkdir(parents=True, exist_ok=True)
    _write_meta(upload_id, {
        "filename": filename,
        "file_size": file_size,
        "file_hash": file_hash,  # may be None — computed on complete
        "total_chunks": total_chunks,
        "received_chunks": [],
        "user_id": user.id,
        "created_at": time.time(),
        "updated_at": time.time(),
    })

    return {
        "upload_id": upload_id,
        "chunk_size": CHUNK_SIZE,
        "total_chunks": total_chunks,
    }


@router.get("/{upload_id}/status")
def upload_status(
    upload_id: str,
    user: User = Depends(get_current_user),
):
    """Return which chunks have been received — for resume after a drop."""
    meta = _read_meta(upload_id)
    if meta["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Not your upload")

    received = set(meta["received_chunks"])
    total = meta["total_chunks"]
    missing = [i for i in range(total) if i not in received]

    return {
        "upload_id": upload_id,
        "total_chunks": total,
        "received_chunks": sorted(received),
        "missing_chunks": missing,
        "is_complete": len(missing) == 0,
    }


@router.post("/{upload_id}/chunk")
def upload_chunk(
    upload_id: str,
    index: int = Form(...),
    chunk: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """Receive a single chunk of the file.

    The chunk is written directly to disk (streaming, not buffered in RAM).
    The received_chunks list in the meta file is updated atomically.
    """
    meta = _read_meta(upload_id)
    if meta["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Not your upload")

    if index < 0 or index >= meta["total_chunks"]:
        raise HTTPException(status_code=400, detail="Invalid chunk index")

    if index in meta["received_chunks"]:
        # Already received — idempotent (client may retry after a timeout)
        return {"ok": True, "index": index, "duplicate": True}

    # Stream the chunk to disk — 1 MiB at a time, never loading the whole
    # chunk into RAM (though at 8 MiB it's fine, this is extra safety).
    chunk_path = _session_dir(upload_id) / f"chunk_{index:06d}.part"
    hasher = hashlib.sha256()
    bytes_written = 0
    with open(chunk_path, "wb") as out:
        while True:
            block = chunk.file.read(1024 * 1024)
            if not block:
                break
            hasher.update(block)
            out.write(block)
            bytes_written += len(block)

    # Update meta atomically
    meta["received_chunks"].append(index)
    meta["updated_at"] = time.time()
    _write_meta(upload_id, meta)

    return {
        "ok": True,
        "index": index,
        "size": bytes_written,
        "chunk_hash": hasher.hexdigest(),
    }


@router.post("/{upload_id}/complete")
def complete_upload(
    upload_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Assemble all chunks into the final file and create the GameplaySource.

    This is the "commit" step — only called once all chunks are uploaded.
    The assembled file is hashed for dedup, then moved to the user's upload
    directory. The chunked session dir is cleaned up.
    """
    meta = _read_meta(upload_id)
    if meta["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Not your upload")

    total = meta["total_chunks"]
    received = set(meta["received_chunks"])
    missing = [i for i in range(total) if i not in received]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Missing {len(missing)} chunks: {missing[:20]}...",
        )

    settings = get_settings()
    upload_dir = settings.temp_uploads_dir / f"user_{user.id}"
    upload_dir.mkdir(parents=True, exist_ok=True)

    filename = meta["filename"]
    file_hash = meta.get("file_hash")
    hasher = hashlib.sha256() if not file_hash else None
    file_size = 0

    # Assemble: concatenate chunks in order, computing hash if not provided.
    # We write to a .assembling file first, then rename — so a crash or
    # interrupt doesn't leave a half-written file that looks complete.
    assembling_path = upload_dir / f".{filename}.assembling"
    with open(assembling_path, "wb") as out:
        for i in range(total):
            chunk_path = _session_dir(upload_id) / f"chunk_{i:06d}.part"
            with open(chunk_path, "rb") as chunk_file:
                while True:
                    block = chunk_file.read(1024 * 1024)
                    if not block:
                        break
                    if hasher:
                        hasher.update(block)
                    out.write(block)
                    file_size += len(block)

    if hasher:
        file_hash = hasher.hexdigest()

    # Dedup check (server-side hash is authoritative)
    existing = db.query(GameplaySource).filter(
        GameplaySource.user_id == user.id,
        GameplaySource.file_hash == file_hash,
    ).first()
    if existing:
        assembling_path.unlink(missing_ok=True)
        _cleanup_session(upload_id)
        return {"duplicate": True, "source_id": existing.id}

    # Rename to final name
    safe_name = f"{file_hash[:8]}_{filename}"
    file_path = upload_dir / safe_name
    assembling_path.rename(file_path)

    storage_key = f"user_{user.id}/{safe_name}"

    # Create the GameplaySource record and auto-create a mapping job so the
    # worker picks it up immediately — no need for the user to click
    # "Solicitar mapeamento" manually.
    with session_scope() as session:
        token = secrets.token_urlsafe(32)
        source = GameplaySource(
            user_id=user.id,
            file_path=str(file_path),
            storage_key=storage_key,
            filename=filename,
            file_hash=file_hash,
            file_size=file_size,
            ingestion_status=IngestionStatus.discovered.value,
            processing_status=GameplayProcessingStatus.waiting_worker.value,
            upload_token=token,
        )
        session.add(source)
        session.flush()
        source_id = source.id

        # Create mapping job (replicates create_mapping_job logic inline)
        from gpcg.core.models import ChannelProfile, ContentDomain
        _domain = ContentDomain.games.value
        if source.user_id:
            _profile = session.query(ChannelProfile).filter(
                ChannelProfile.user_id == source.user_id
            ).first()
            if _profile:
                _domain = _profile.domain
        job = Job(
            job_uuid=str(uuid.uuid4()),
            type=JobType.mapping.value,
            status=JobStatus.queued.value,
            stage=JobStage.download.value,
            domain=_domain,
            gameplay_source_id=source.id,
            user_id=source.user_id,
            game_id=source.game_id,
            priority=JobPriority.normal.value,
            required_capabilities=[WorkerCapability.mapping.value],
            artifacts={},
        )
        session.add(job)
        session.flush()
        job_id = job.id
        _source = source
        _job = job

    # Publish events after commit (session_scope commits on exit)
    from gpcg.infrastructure.events import (
        publish_gameplay_status_changed,
        publish_job_created,
    )
    publish_gameplay_status_changed(
        user.id, _source.id, _source.processing_status, _source.filename,
    )
    publish_job_created(user.id, _job.id, _job.type, _job.priority)
    from gpcg.infrastructure.job_queue import enqueue_job
    enqueue_job(_job)

    # Clean up the chunked session
    _cleanup_session(upload_id)

    # NOTE: Game resolution is NOT done on the VPS. The VPS just stores the
    # upload and creates a mapping job. The worker (which has GPU + Ollama)
    # runs the full L1→L2→L3 resolution locally and reports back via
    # POST /api/gameplays/{source_id}/resolve-game.

    log.info(
        f"Chunked upload complete: {filename} ({file_size} bytes, "
        f"{total} chunks) → source_id={source_id}, mapping job_id={job_id}"
    )

    return {
        "id": source_id,
        "filename": filename,
        "file_hash": file_hash,
        "file_size": file_size,
        "processing_status": GameplayProcessingStatus.waiting_worker.value,
        "ingestion_status": IngestionStatus.discovered.value,
        "job_id": job_id,
    }


@router.delete("/{upload_id}")
def cancel_upload(
    upload_id: str,
    user: User = Depends(get_current_user),
):
    """Cancel an upload session and clean up all stored chunks."""
    meta = _read_meta(upload_id)
    if meta["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Not your upload")
    _cleanup_session(upload_id)
    return {"ok": True}


def _cleanup_session(upload_id: str) -> None:
    """Remove the chunked upload session directory."""
    d = _session_dir(upload_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
