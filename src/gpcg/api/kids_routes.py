"""Kids domain API routes — topic management, asset upload, and generation.

These endpoints are Kids-specific. They only make sense when the channel's
domain is "kids". The frontend shows/hides them based on the current domain.
"""

from __future__ import annotations

import hashlib
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
from gpcg.domains.kids.models import KidsTopic, StoryAsset, AssetProcessingStatus
from gpcg.infrastructure.auth import get_current_user
from gpcg.infrastructure.database import get_db
from gpcg.logging import get_logger

log = get_logger(__name__)

router = APIRouter()


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

    # Delete physical asset files
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

    db.delete(topic)  # cascade deletes assets
    db.commit()
    return {"deleted": True}


# ── Asset upload routes ──────────────────────────────────────────────────────


@router.post("/kids/topics/{topic_id}/assets")
async def upload_asset(
    topic_id: int,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload an image asset for a Kids topic."""
    _require_kids_domain(user, db)
    topic = db.query(KidsTopic).filter(
        KidsTopic.id == topic_id,
        KidsTopic.user_id == user.id,
    ).first()
    if not topic:
        raise HTTPException(404, "Topic not found")

    # Read file
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file")

    # Compute hash for dedup
    file_hash = hashlib.sha256(content).hexdigest()

    # Save to kids_assets directory
    settings = get_settings()
    assets_dir = settings.data_dir / "kids_assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    # Use hash prefix to avoid collisions
    storage_key = f"{file_hash[:8]}_{file.filename}"
    file_path = assets_dir / storage_key
    file_path.write_bytes(content)

    # Probe image dimensions
    width, height = 0, 0
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(content))
        width, height = img.size
    except Exception:
        pass  # Not a valid image or PIL not available

    asset = StoryAsset(
        user_id=user.id,
        topic_id=topic_id,
        filename=file.filename,
        storage_key=storage_key,
        file_hash=file_hash,
        file_size=len(content),
        width=width,
        height=height,
        processing_status=AssetProcessingStatus.ready.value,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)

    log.info(f"Uploaded Kids asset #{asset.id} for topic #{topic_id}: {file.filename}")
    return {
        "id": asset.id,
        "filename": asset.filename,
        "storage_key": asset.storage_key,
        "width": asset.width,
        "height": asset.height,
        "processing_status": asset.processing_status,
    }


@router.get("/kids/topics/{topic_id}/assets")
def list_assets(
    topic_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all assets for a Kids topic."""
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
            "width": a.width,
            "height": a.height,
            "processing_status": a.processing_status,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        } for a in assets]
    }


@router.delete("/kids/assets/{asset_id}")
def delete_asset(
    asset_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a single story asset."""
    _require_kids_domain(user, db)
    asset = db.query(StoryAsset).filter(
        StoryAsset.id == asset_id,
        StoryAsset.user_id == user.id,
    ).first()
    if not asset:
        raise HTTPException(404, "Asset not found")

    # Delete physical file
    settings = get_settings()
    if asset.storage_key:
        p = settings.data_dir / "kids_assets" / asset.storage_key
        if p.exists():
            try:
                p.unlink()
            except OSError as e:
                log.warning(f"Failed to delete asset file {p}: {e}")

    db.delete(asset)
    db.commit()
    return {"deleted": True}


# ── Generation route ─────────────────────────────────────────────────────────


@router.post("/kids/generate")
def generate_kids_video(
    data: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a Kids video generation job for a topic."""
    _require_kids_domain(user, db)
    topic_id = data.get("topic_id")
    if not topic_id:
        raise HTTPException(422, "topic_id is required")

    topic = db.query(KidsTopic).filter(
        KidsTopic.id == topic_id,
        KidsTopic.user_id == user.id,
    ).first()
    if not topic:
        raise HTTPException(404, "Topic not found")

    # Check that the topic has at least one asset
    asset_count = db.query(StoryAsset).filter(
        StoryAsset.topic_id == topic_id,
        StoryAsset.processing_status == AssetProcessingStatus.ready.value,
    ).count()
    if asset_count == 0:
        raise HTTPException(
            422,
            "Topic has no ready assets. Upload images before generating a video."
        )

    # Create the generation job
    import uuid as _uuid
    job = Job(
        job_uuid=str(_uuid.uuid4()),
        type=JobType.generate_short.value,
        user_id=user.id,
        domain=ContentDomain.kids.value,
        status=JobStatus.queued.value,
        priority=JobPriority.normal.value,
        artifacts={
            "topic_id": topic_id,
            "topic_title": topic.title,
        },
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    log.info(f"Created Kids generation job #{job.id} for topic '{topic.title}'")
    return {"job_id": job.id, "topic_id": topic_id}
