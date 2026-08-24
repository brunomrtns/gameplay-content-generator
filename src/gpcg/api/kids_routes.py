"""Kids domain API routes — topic management, asset upload, and generation.

These endpoints are Kids-specific. They only make sense when the channel's
domain is "kids". The frontend shows/hides them based on the current domain.
"""

from __future__ import annotations

import hashlib
import uuid as _uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.core.models import (
    ChannelProfile,
    ContentDomain,
    Job,
    JobStatus,
    JobType,
    JobPriority,
    User,
)
from gpcg.domains.kids.models import (
    KidsTopic,
    StoryAsset,
    AssetProcessingStatus,
    AssetMediaKind,
)
from gpcg.infrastructure.auth import get_current_user
from gpcg.infrastructure.database import get_db
from gpcg.logging import get_logger

log = get_logger(__name__)

router = APIRouter()


# Allowed MIME types for Kids media uploads
_IMAGE_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}
_VIDEO_MIMES = {"video/mp4", "video/webm", "video/quicktime", "video/x-matroska", "video/avi"}
_ALLOWED_MIMES = _IMAGE_MIMES | _VIDEO_MIMES
_MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GiB


# ── Models ───────────────────────────────────────────────────────────────────


class TopicCreate(BaseModel):
    title: str
    category: str = "general"
    age_range: str = "3-6"
    description: str = ""


class TopicOut(BaseModel):
    id: int
    title: str
    slug: str
    category: str
    age_range: str
    description: str
    asset_count: int = 0
    metadata_json: dict = {}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _require_kids_domain(user: User, db: Session) -> ChannelProfile:
    """Ensure the user's channel is in the Kids domain."""
    profile = db.query(ChannelProfile).filter(
        ChannelProfile.user_id == user.id
    ).first()
    if not profile or profile.domain != ContentDomain.kids.value:
        raise HTTPException(
            403,
            "This endpoint requires the Kids domain. Switch your channel to Kids first.",
        )
    return profile


def _slugify(title: str) -> str:
    """Simple slugify for topic titles."""
    import re
    slug = title.lower().strip()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug or f"topic-{hash(title) % 10000}"


# ── Topic routes ─────────────────────────────────────────────────────────────


@router.get("/kids/topics")
def list_topics(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all Kids topics for the current user."""
    _require_kids_domain(user, db)
    topics = db.query(KidsTopic).filter(
        KidsTopic.user_id == user.id
    ).order_by(KidsTopic.created_at.desc()).all()
    result = []
    for t in topics:
        asset_count = db.query(StoryAsset).filter(
            StoryAsset.topic_id == t.id
        ).count()
        result.append({
            "id": t.id,
            "title": t.title,
            "slug": t.slug,
            "category": t.category,
            "age_range": t.age_range,
            "description": t.description,
            "asset_count": asset_count,
            "metadata_json": t.metadata_json,
        })
    return {"topics": result}


@router.post("/kids/topics")
def create_topic(
    data: TopicCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new Kids topic."""
    _require_kids_domain(user, db)
    topic = KidsTopic(
        user_id=user.id,
        title=data.title,
        slug=_slugify(data.title),
        category=data.category,
        age_range=data.age_range,
        description=data.description,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    log.info(f"Created Kids topic #{topic.id}: {topic.title}")
    return {
        "id": topic.id,
        "title": topic.title,
        "slug": topic.slug,
        "category": topic.category,
        "age_range": topic.age_range,
        "description": topic.description,
    }


@router.delete("/kids/topics/{topic_id}")
def delete_topic(
    topic_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a Kids topic and all its assets."""
    _require_kids_domain(user, db)
    topic = db.query(KidsTopic).filter(
        KidsTopic.id == topic_id,
        KidsTopic.user_id == user.id,
    ).first()
    if not topic:
        raise HTTPException(404, "Topic not found")

    # Delete physical asset files (including thumbnails)
    settings = get_settings()
    assets = db.query(StoryAsset).filter(StoryAsset.topic_id == topic_id).all()
    for asset in assets:
        if asset.storage_key:
            p = settings.data_dir / "kids_assets" / asset.storage_key
            if p.exists():
                try:
                    p.unlink()
                except OSError as e:
                    log.warning(f"Failed to delete asset {p}: {e}")
        if asset.thumbnail_key:
            t = settings.data_dir / "kids_assets" / asset.thumbnail_key
            if t.exists():
                try:
                    t.unlink()
                except OSError as e:
                    log.warning(f"Failed to delete thumbnail {t}: {e}")

    db.delete(topic)  # cascade deletes assets
    db.commit()
    return {"deleted": True}


# ── Asset library routes (channel library, not per-topic) ────────────────────


@router.post("/kids/assets/upload")
async def upload_library_asset(
    file: UploadFile = File(...),
    tags: str = Form(""),       # comma-separated tags
    description: str = Form(""),
    topic_id: Optional[int] = Form(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload a media asset to the channel's Kids media library.

    Mídia da **biblioteca do canal**, não vinculada obrigatoriamente a um
    tópico. O ``topic_id`` é opcional — se fornecido, a mídia fica
    vinculada ao tópico (para priorização na seleção semântica). Se
    omitido, fica na biblioteca geral do canal.

    Images are probed synchronously (PIL dimensions) and marked ``ready``
    immediately. Videos are saved to disk and a ``kids_asset_process`` job
    is created — the worker will download the video, run FFprobe for
    metadata (duration, dimensions, codec, audio), generate a thumbnail,
    and sync the results back.

    Upload is streamed in 1 MiB chunks (NOT loaded into RAM) so large
    video files don't OOM the VPS. Hashing is incremental for dedup.
    """
    _require_kids_domain(user, db)

    # Validate optional topic_id
    if topic_id is not None:
        topic = db.query(KidsTopic).filter(
            KidsTopic.id == topic_id,
            KidsTopic.user_id == user.id,
        ).first()
        if not topic:
            raise HTTPException(404, "Topic not found")

    # Determine media kind from content type
    content_type = (file.content_type or "").lower()
    if content_type not in _ALLOWED_MIMES:
        raise HTTPException(
            422,
            f"Tipo de arquivo não suportado: {content_type or 'desconhecido'}. "
            f"Use imagens (PNG, JPEG, WebP, GIF) ou vídeos (MP4, WebM, MOV, MKV)."
        )
    media_kind = (
        AssetMediaKind.video.value if content_type in _VIDEO_MIMES
        else AssetMediaKind.image.value
    )

    settings = get_settings()
    assets_dir = settings.data_dir / "kids_assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    # Stream upload to disk in chunks (same pattern as /gameplays/upload).
    # Hash incrementally as we go so we can dedup without a second pass.
    filename = file.filename or f"upload.{media_kind}"
    hasher = hashlib.sha256()
    file_size = 0
    tmp_path = assets_dir / f".{filename}.uploading.part"
    try:
        with open(tmp_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)  # 1 MiB chunks
                if not chunk:
                    break
                hasher.update(chunk)
                out.write(chunk)
                file_size += len(chunk)
                if file_size > _MAX_FILE_SIZE:
                    raise HTTPException(413, "Arquivo muito grande (máx 2 GiB)")
    except HTTPException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise

    file_hash = hasher.hexdigest()

    # Dedup: same user + same hash = already uploaded
    existing = db.query(StoryAsset).filter(
        StoryAsset.user_id == user.id,
        StoryAsset.file_hash == file_hash,
    ).first()
    if existing:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(409, "Este arquivo já foi enviado")

    # Rename .part → final name (hash prefix avoids collisions)
    safe_name = f"{file_hash[:8]}_{filename}"
    file_path = assets_dir / safe_name
    tmp_path.rename(file_path)
    storage_key = safe_name

    # Parse tags
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    if media_kind == AssetMediaKind.image.value:
        # Images: probe dimensions synchronously with PIL (lightweight, no GPU)
        width, height = 0, 0
        try:
            from PIL import Image
            import io
            with open(file_path, "rb") as f:
                img = Image.open(io.BytesIO(f.read()))
                width, height = img.size
        except Exception:
            pass  # Not a valid image or PIL not available

        asset = StoryAsset(
            user_id=user.id,
            topic_id=topic_id,
            filename=filename,
            storage_key=storage_key,
            file_hash=file_hash,
            file_size=file_size,
            width=width,
            height=height,
            media_kind=media_kind,
            processing_status=AssetProcessingStatus.ready.value,
            tags=tag_list,
            description=description,
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        log.info(f"Uploaded Kids image asset #{asset.id} to library: {filename}")
        return {
            "id": asset.id,
            "filename": asset.filename,
            "storage_key": asset.storage_key,
            "media_kind": asset.media_kind,
            "width": asset.width,
            "height": asset.height,
            "processing_status": asset.processing_status,
            "tags": asset.tags,
            "description": asset.description,
            "topic_id": asset.topic_id,
        }

    # Videos: save record as "queued" and create a processing job.
    # The worker will download → FFprobe → thumbnail → sync metadata → ready.
    asset = StoryAsset(
        user_id=user.id,
        topic_id=topic_id,
        filename=filename,
        storage_key=storage_key,
        file_hash=file_hash,
        file_size=file_size,
        media_kind=media_kind,
        processing_status=AssetProcessingStatus.queued.value,
        tags=tag_list,
        description=description,
    )
    db.add(asset)
    db.flush()

    job = Job(
        job_uuid=str(_uuid.uuid4()),
        type=JobType.kids_asset_process.value,
        user_id=user.id,
        domain=ContentDomain.kids.value,
        status=JobStatus.queued.value,
        priority=JobPriority.normal.value,
        artifacts={
            "asset_id": asset.id,
            "topic_id": topic_id,
            "media_kind": media_kind,
            "filename": filename,
            "file_hash": file_hash,
        },
    )
    db.add(job)
    db.commit()
    db.refresh(asset)
    db.refresh(job)

    log.info(
        f"Uploaded Kids video asset #{asset.id} to library: {filename} "
        f"→ processing job #{job.id} queued"
    )
    return {
        "id": asset.id,
        "filename": asset.filename,
        "storage_key": asset.storage_key,
        "media_kind": asset.media_kind,
        "file_size": asset.file_size,
        "processing_status": asset.processing_status,
        "tags": asset.tags,
        "description": asset.description,
        "topic_id": asset.topic_id,
        "job_id": job.id,
    }


@router.get("/kids/assets")
def list_library_assets(
    media_kind: Optional[str] = None,
    status: Optional[str] = None,
    topic_id: Optional[int] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all media assets in the channel's Kids library.

    Supports optional filters: media_kind (image/video), status (ready/queued/
    processing/failed), topic_id.
    """
    _require_kids_domain(user, db)
    query = db.query(StoryAsset).filter(
        StoryAsset.user_id == user.id,
    )
    if media_kind:
        query = query.filter(StoryAsset.media_kind == media_kind)
    if status:
        query = query.filter(StoryAsset.processing_status == status)
    if topic_id is not None:
        query = query.filter(StoryAsset.topic_id == topic_id)
    assets = query.order_by(StoryAsset.created_at.desc()).all()
    return {
        "assets": [{
            "id": a.id,
            "filename": a.filename,
            "storage_key": a.storage_key,
            "media_kind": a.media_kind,
            "width": a.width,
            "height": a.height,
            "duration": a.duration,
            "codec": a.codec,
            "has_audio": a.has_audio,
            "thumbnail_key": a.thumbnail_key,
            "processing_status": a.processing_status,
            "process_error": a.process_error,
            "file_size": a.file_size,
            "tags": a.tags or [],
            "description": a.description or "",
            "is_public": a.is_public,
            "topic_id": a.topic_id,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        } for a in assets]
    }


class AssetPatch(BaseModel):
    """Patch update for a story asset (tags + description + topic link)."""
    tags: Optional[list[str]] = None
    description: Optional[str] = None
    topic_id: Optional[int] = None
    is_public: Optional[bool] = None


@router.patch("/kids/assets/{asset_id}")
def patch_asset(
    asset_id: int,
    data: AssetPatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update a story asset's tags, description, topic link, or visibility.

    Used by the frontend to tag media for semantic selection and to
    optionally link/unlink assets to topics.
    """
    _require_kids_domain(user, db)
    asset = db.query(StoryAsset).filter(
        StoryAsset.id == asset_id,
        StoryAsset.user_id == user.id,
    ).first()
    if not asset:
        raise HTTPException(404, "Asset not found")

    if data.tags is not None:
        asset.tags = data.tags
    if data.description is not None:
        asset.description = data.description
    if data.topic_id is not None:
        # Validate topic ownership
        if data.topic_id > 0:
            topic = db.query(KidsTopic).filter(
                KidsTopic.id == data.topic_id,
                KidsTopic.user_id == user.id,
            ).first()
            if not topic:
                raise HTTPException(404, "Topic not found")
            asset.topic_id = data.topic_id
        else:
            asset.topic_id = None  # unlink from topic
    if data.is_public is not None:
        asset.is_public = data.is_public

    db.commit()
    db.refresh(asset)
    return {
        "id": asset.id,
        "tags": asset.tags,
        "description": asset.description,
        "topic_id": asset.topic_id,
        "is_public": asset.is_public,
    }


# Legacy endpoint: upload to a specific topic (redirects to library upload)
@router.post("/kids/topics/{topic_id}/assets")
async def upload_topic_asset(
    topic_id: int,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload a media asset for a specific Kids topic (legacy compat).

    Delegates to the library upload with topic_id set. Kept for backward
    compatibility with older frontend code.
    """
    return await upload_library_asset(
        file=file, tags="", description="", topic_id=topic_id,
        user=user, db=db,
    )


@router.get("/kids/topics/{topic_id}/assets")
def list_topic_assets(
    topic_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all assets linked to a Kids topic."""
    _require_kids_domain(user, db)
    topic = db.query(KidsTopic).filter(
        KidsTopic.id == topic_id,
        KidsTopic.user_id == user.id,
    ).first()
    if not topic:
        raise HTTPException(404, "Topic not found")

    assets = db.query(StoryAsset).filter(
        StoryAsset.topic_id == topic_id
    ).order_by(StoryAsset.created_at.desc()).all()
    return {
        "assets": [{
            "id": a.id,
            "filename": a.filename,
            "storage_key": a.storage_key,
            "media_kind": a.media_kind,
            "width": a.width,
            "height": a.height,
            "duration": a.duration,
            "has_audio": a.has_audio,
            "thumbnail_key": a.thumbnail_key,
            "processing_status": a.processing_status,
            "process_error": a.process_error,
            "file_size": a.file_size,
            "tags": a.tags or [],
            "description": a.description or "",
            "created_at": a.created_at.isoformat() if a.created_at else None,
        } for a in assets]
    }


@router.delete("/kids/assets/{asset_id}")
def delete_asset(
    asset_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a single story asset and its thumbnail.

    Also cancels any pending ``kids_asset_process`` job for this asset
    so a worker doesn't try to process a deleted file.
    """
    _require_kids_domain(user, db)
    asset = db.query(StoryAsset).filter(
        StoryAsset.id == asset_id,
        StoryAsset.user_id == user.id,
    ).first()
    if not asset:
        raise HTTPException(404, "Asset not found")

    # Cancel pending processing jobs for this asset
    pending_jobs = db.query(Job).filter(
        Job.type == JobType.kids_asset_process.value,
        Job.status.in_([JobStatus.queued.value, JobStatus.running.value]),
    ).all()
    for j in pending_jobs:
        artifacts = j.artifacts or {}
        if artifacts.get("asset_id") == asset_id:
            j.status = JobStatus.cancelled.value
    db.flush()

    # Delete physical files (asset + thumbnail)
    settings = get_settings()
    if asset.storage_key:
        p = settings.data_dir / "kids_assets" / asset.storage_key
        if p.exists():
            try:
                p.unlink()
            except OSError as e:
                log.warning(f"Failed to delete asset file {p}: {e}")
    if asset.thumbnail_key:
        t = settings.data_dir / "kids_assets" / asset.thumbnail_key
        if t.exists():
            try:
                t.unlink()
            except OSError as e:
                log.warning(f"Failed to delete thumbnail {t}: {e}")

    db.delete(asset)
    db.commit()
    return {"deleted": True}


# ── Thumbnail serving (frontend) ─────────────────────────────────────────────


@router.get("/kids/assets/thumbnail/{thumbnail_key:path}")
def serve_thumbnail(
    thumbnail_key: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Serve a thumbnail image for a Kids video asset (frontend display)."""
    _require_kids_domain(user, db)
    settings = get_settings()
    thumb_path = settings.data_dir / "kids_assets" / thumbnail_key
    if not thumb_path.exists():
        raise HTTPException(404, "Thumbnail not found")

    from fastapi.responses import FileResponse
    return FileResponse(
        str(thumb_path),
        media_type="image/jpeg",
        filename=thumbnail_key,
    )
