"""File transfer endpoints — downloads (VPS → worker) and uploads (worker → VPS).

Covers:
  - Game registry listing (for the worker's game resolver)
  - Gameplay download (streaming, token-authenticated)
  - Gameplay download confirmation (checksum verification + multi-worker cleanup)
  - Voice reference download
  - Kids asset download
  - Video upload (worker → VPS, with validation + thumbnail generation)
  - Gameplay sync listing (multi-worker: which gameplays a worker still needs)
  - Document download + confirmation (knowledge indexing pipeline)
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.core.models import Document, Job, Worker, WorkerStatus
from gpcg.domains.games.models import (
    Game,
    GameAlias,
    GameplayDownload,
    GameplayProcessingStatus,
    GameplaySource,
)
from gpcg.infrastructure.database import get_db

from gpcg.api.workers._common import (
    ConfirmDownloadRequest,
    _resolve_storage_path,
    _utcnow,
    worker_auth,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["workers"])


# ── Game registry (worker builds local candidate catalog) ────────────────────


@router.get("/games/registry")
def worker_list_games(
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """List all games + aliases for the worker's game resolver.

    The worker uses this to build a local candidate catalog for L1 (slug/alias
    matching) and L3 (VLM candidate list).
    """
    games = db.query(Game).all()
    aliases = db.query(GameAlias).all()
    return {
        "games": [
            {
                "id": g.id,
                "canonical_name": g.canonical_name,
                "slug": g.slug,
                "camera_type": g.camera_type,
            }
            for g in games
        ],
        "aliases": [
            {"game_id": a.game_id, "alias": a.alias}
            for a in aliases
        ],
    }


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


# ── Voice download (worker) ──────────────────────────────────────────────────


@router.get("/voices/{filename}/download")
def download_voice_worker(
    filename: str,
    user_id: int,
    _: None = Depends(worker_auth),
):
    """Stream a voice reference file from VPS to the worker.

    Used by the remote worker to download the user's uploaded voice file
    so that TTS (XTTS) can use it locally. The worker resolves the voice
    path locally when processing generation jobs.
    """
    from gpcg.config import get_settings
    # Prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "invalid filename")
    settings = get_settings()
    # Try user-specific dir first, then shared/legacy dir
    user_dir = settings.voices_dir / f"user_{user_id}"
    candidates = [
        user_dir / filename,
        settings.voices_dir / filename,
    ]
    for p in candidates:
        if p.exists():
            log.info(f"Worker downloading voice: {filename} (user {user_id})")

            def _stream(path: Path, chunk_size: int = 1024 * 1024):
                with open(path, "rb") as f:
                    while True:
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        yield chunk

            return StreamingResponse(
                _stream(p),
                media_type="audio/wav",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Content-Length": str(p.stat().st_size),
                },
            )
    raise HTTPException(404, "voice not found")


# ── Kids asset download (worker) ─────────────────────────────────────────────


@router.get("/kids/assets/{asset_id}/download")
def download_kids_asset(
    asset_id: int,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Stream a Kids story asset (image or video) from VPS to the worker.

    Used by the remote worker during:
    - ``kids_asset_process`` jobs: download video → FFprobe → thumbnail
    - generation jobs: download assets so the Kids pipeline can use them
      locally (images for Ken Burns, videos for background/overlay).
    """
    from gpcg.domains.kids.models import StoryAsset
    from gpcg.config import get_settings

    asset = db.query(StoryAsset).filter(StoryAsset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Story asset not found")

    if not asset.storage_key:
        raise HTTPException(status_code=404, detail="No file available for download")

    settings = get_settings()
    assets_dir = settings.data_dir / "kids_assets"
    file_path = assets_dir / asset.storage_key
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Asset file not found on VPS")

    # Determine media type for Content-Type header
    if asset.media_kind == "video":
        media_type = "video/mp4"
    else:
        media_type = "image/png"

    log.info(f"Worker downloading Kids asset #{asset_id} ({asset.media_kind}: {asset.filename})")

    def _stream(path: Path, chunk_size: int = 1024 * 1024):
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        _stream(file_path),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{asset.filename}"',
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
    # Keep downloaded_by_worker for backward compat (first downloader)
    if not source.downloaded_by_worker:
        source.downloaded_by_worker = req.worker_id
    source.upload_token = None  # invalidate token

    # Track per-worker download (multi-worker)
    existing_dl = db.query(GameplayDownload).filter(
        GameplayDownload.source_id == source_id,
        GameplayDownload.worker_id == req.worker_id,
    ).first()
    if not existing_dl:
        dl = GameplayDownload(
            source_id=source_id,
            worker_id=req.worker_id,
            downloaded_at=_utcnow(),
            checksum_verified=True,
            file_size=source.file_size,
        )
        db.add(dl)
        db.flush()  # ensure the new record is visible in the query below

    # Delete temp file from VPS only if ALL active workers have confirmed
    # (or if there's only one worker — backward compat single-worker mode)
    if source.storage_key:
        # Find all active workers with mapping or generation capability.
        # Filter in Python because SQLite JSON .contains() is unreliable.
        active_workers = (
            db.query(Worker)
            .filter(Worker.status.in_([
                WorkerStatus.online.value, WorkerStatus.busy.value
            ]))
            .all()
        )
        all_active = set(
            w.worker_id for w in active_workers
            if "mapping" in (w.capabilities or []) or "generation" in (w.capabilities or [])
        )
        confirmed = set(
            dl.worker_id for dl in
            db.query(GameplayDownload).filter(
                GameplayDownload.source_id == source_id
            ).all()
        )

        if all_active.issubset(confirmed) or len(all_active) <= 0:
            file_path = _resolve_storage_path(source.storage_key)
            if file_path.exists():
                try:
                    file_path.unlink()
                    log.info(f"Deleted temp file {source.storage_key} from VPS (all workers confirmed)")
                except OSError as e:
                    log.warning(f"Failed to delete temp file {file_path}: {e}")
        else:
            log.info(
                f"Keeping temp file {source.storage_key} on VPS "
                f"({len(confirmed)}/{len(all_active)} workers confirmed)"
            )

    db.commit()
    from gpcg.infrastructure.events import publish_gameplay_status_changed
    publish_gameplay_status_changed(
        source.user_id, source.id, source.processing_status, source.filename,
    )
    log.info(f"Gameplay #{source_id} download confirmed by '{req.worker_id}'")
    return {"ok": True, "processing_status": source.processing_status}


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
    # Presentation Layer: if the job has a presentation thumbnail, use it
    # instead of the default mid-video frame extraction.
    thumb_path: Optional[Path] = None
    presentation_thumb = (job.artifacts or {}).get("presentation_thumbnail_path")
    if presentation_thumb:
        # The worker uploads the presentation thumbnail separately via
        # the upload-video endpoint's multipart (it sends the thumbnail
        # as part of the result sync). Check if the file exists on the VPS.
        p_thumb = Path(presentation_thumb)
        if p_thumb.exists():
            # Copy to videos_dir with consistent naming
            thumb_path = settings.videos_dir / f"{dest_path.stem}_thumb.jpg"
            import shutil as _shutil
            _shutil.copy2(p_thumb, thumb_path)
            log.info(f"Using Presentation Layer thumbnail for job #{job_id}: {thumb_path.name}")
        else:
            log.warning(f"Presentation thumbnail not found at {presentation_thumb}, falling back to auto-generation")
            presentation_thumb = None

    if not thumb_path:
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
    from gpcg.core.models import Video, VideoStatus

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


# ── Gameplay sync (multi-worker: list gameplays a worker needs to download) ──


@router.get("/gameplays/list-for-sync")
def list_gameplays_for_sync(
    worker_id: str,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """List gameplays that a worker should download on startup.

    Returns gameplays that:
    - Have a storage_key (file exists on VPS temp)
    - Are in 'downloaded', 'mapped', or 'ready' processing_status
    - Have NOT been confirmed by this worker yet (no GameplayDownload record)

    The worker downloads these via SCP/HTTP and calls confirm-download for each.
    This ensures a worker can generate jobs even if another worker did the mapping.
    """
    # Find gameplays with temp files still on VPS
    sources_with_temp = (
        db.query(GameplaySource)
        .filter(GameplaySource.storage_key.isnot(None))
        .filter(GameplaySource.processing_status.in_([
            GameplayProcessingStatus.downloaded.value,
            GameplayProcessingStatus.mapped.value,
            GameplayProcessingStatus.ready.value,
        ]))
        .all()
    )

    # Filter out ones this worker already confirmed
    result = []
    for source in sources_with_temp:
        already = db.query(GameplayDownload).filter(
            GameplayDownload.source_id == source.id,
            GameplayDownload.worker_id == worker_id,
        ).first()
        if not already:
            result.append({
                "id": source.id,
                "filename": source.filename,
                "file_hash": source.file_hash,
                "file_size": source.file_size,
                "storage_key": source.storage_key,
                "processing_status": source.processing_status,
            })

    return {"gameplays": result, "count": len(result)}


# ── Document download + confirmation (knowledge indexing pipeline) ───────────


class ConfirmDocumentDownloadRequest(BaseModel):
    checksum: str
    worker_id: str


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
