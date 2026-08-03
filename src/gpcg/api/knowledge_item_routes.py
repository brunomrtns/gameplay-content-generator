"""Knowledge Item API routes (V2).

Endpoints for the content intelligence idea bank: list, detail,
reject, manual collect trigger, and stats.

See ARCHITECTURE_V2.md §11.1.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from gpcg.application.content_collectors import cleanup_old_news, collect_rss
from gpcg.application.knowledge_item_service import (
    get_by_id,
    get_stats,
    list_items,
    reject_item,
)
from gpcg.domain.models import Game, Job, JobPriority, JobStatus, JobType, User
from gpcg.infrastructure.auth import get_current_user
from gpcg.infrastructure.database import get_db

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────


class KnowledgeItemOut(BaseModel):
    id: int
    game_id: Optional[int] = None
    title: str
    content: str
    item_type: str
    source_type: str
    source_url: Optional[str] = None
    source_name: Optional[str] = None
    published_at: Optional[str] = None
    collected_at: str
    editorial_score: float
    status: str
    franchise: Optional[str] = None
    developer: Optional[str] = None
    tags: list = []

    class Config:
        from_attributes = True


class StatsOut(BaseModel):
    total: int
    fresh: int
    by_type: dict
    by_status: dict
    by_source: dict


# ── Helpers ───────────────────────────────────────────────────────────────────


def _item_to_out(item) -> KnowledgeItemOut:
    return KnowledgeItemOut(
        id=item.id,
        game_id=item.game_id,
        title=item.title,
        content=item.content,
        item_type=item.item_type,
        source_type=item.source_type,
        source_url=item.source_url,
        source_name=item.source_name,
        published_at=item.published_at.isoformat() if item.published_at else None,
        collected_at=item.collected_at.isoformat() if item.collected_at else "",
        editorial_score=item.editorial_score,
        status=item.status,
        franchise=item.franchise,
        developer=item.developer,
        tags=item.tags or [],
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/knowledge-items")
def list_knowledge_items(
    game_id: Optional[int] = Query(None),
    item_type: Optional[str] = Query(None, description="Filter: news|curiosity|lore|fact"),
    status: Optional[str] = Query(None, description="Filter: fresh|used|rejected"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    min_score: float = Query(0.0, ge=0, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List KnowledgeItems with optional filters."""
    items = list_items(
        db,
        game_id=game_id,
        item_type=item_type,
        status=status,
        user_id=user.id,
        limit=limit,
        offset=offset,
        min_score=min_score,
    )
    return {
        "items": [_item_to_out(i) for i in items],
        "total": len(items),
    }


@router.get("/knowledge-items/stats")
def get_knowledge_item_stats(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get statistics about the KnowledgeItem bank."""
    return get_stats(db, user_id=user.id)


@router.get("/knowledge-items/{item_id}")
def get_knowledge_item_detail(
    item_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get detailed information about a KnowledgeItem."""
    item = get_by_id(db, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="KnowledgeItem not found")
    return _item_to_out(item)


@router.post("/knowledge-items/{item_id}/reject")
def reject_knowledge_item(
    item_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Reject a KnowledgeItem (mark as rejected)."""
    if not reject_item(db, item_id):
        raise HTTPException(status_code=404, detail="KnowledgeItem not found")
    db.commit()
    return {"message": "KnowledgeItem rejected"}


@router.post("/knowledge-items/collect")
def trigger_content_collection(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Trigger manual content collection (creates a content_collect job).

    The job collects RSS for all games with gameplay available.
    """
    import uuid
    from sqlalchemy import select

    # Dedup: check for existing queued/running content_collect job
    existing = db.execute(
        select(Job).where(
            Job.type == JobType.content_collect.value,
            Job.status.in_([JobStatus.queued.value, JobStatus.running.value]),
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Content collection job already queued or running",
        )

    job = Job(
        job_uuid=str(uuid.uuid4()),
        type=JobType.content_collect.value,
        status=JobStatus.queued.value,
        stage="content_collection",
        priority=JobPriority.normal.value,
        required_capabilities=["content_intelligence"],
        user_id=user.id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    return {
        "message": "Content collection job created",
        "job_id": job.id,
        "job_uuid": job.job_uuid,
    }
