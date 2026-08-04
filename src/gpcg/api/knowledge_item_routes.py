"""Knowledge Item API routes (V2).

Endpoints for the content intelligence idea bank: list, detail,
reject, manual collect trigger, stats, and idea queue management.

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
    rejection_reason: Optional[str] = None  # REFACTORY_V2
    franchise: Optional[str] = None
    developer: Optional[str] = None
    tags: list = []

    class Config:
        from_attributes = True


class ManualIdeaCreate(BaseModel):
    """Request body for creating a manual KnowledgeItem (user-curated idea)."""
    title: str
    content: str
    game_id: Optional[int] = None


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
        rejection_reason=getattr(item, "rejection_reason", None),  # REFACTORY_V2
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
    """List KnowledgeItems with optional filters.

    When status=fresh (or no status filter), public KIs already used by this
    consumer are excluded via KnowledgeItemUsage records.
    """
    items = list_items(
        db,
        game_id=game_id,
        item_type=item_type,
        status=status,
        user_id=user.id,
        limit=limit,
        offset=offset,
        min_score=min_score,
        exclude_used_by_consumer=user.id,
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
    return get_stats(db, user_id=user.id, consumer_user_id=user.id)


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


@router.post("/knowledge-items")
def create_manual_knowledge_item(
    req: ManualIdeaCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a manual KnowledgeItem (user-curated idea).

    The item is private to the owner (is_public=False), sourced as "manual",
    and classified as a "curiosity" with a neutral editorial score (50.0).
    """
    from gpcg.domain.models import (
        KnowledgeItem,
        KnowledgeItemSource,
        KnowledgeItemStatus,
    )

    # Validate required fields
    if not req.title or not req.title.strip():
        raise HTTPException(status_code=422, detail="title must not be empty")
    if not req.content or not req.content.strip():
        raise HTTPException(status_code=422, detail="content must not be empty")
    if len(req.title) > 500:
        raise HTTPException(status_code=422, detail="title must be at most 500 characters")

    # If game_id provided, verify the game exists
    if req.game_id is not None:
        game = db.get(Game, req.game_id)
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")

    # Resolve source_type (use enum value if available, otherwise the string)
    try:
        source_type = KnowledgeItemSource.manual.value
    except AttributeError:
        source_type = "manual"

    item = KnowledgeItem(
        user_id=user.id,
        is_public=False,
        source_type=source_type,
        item_type="curiosity",
        status=KnowledgeItemStatus.fresh.value,
        editorial_score=50.0,
        title=req.title.strip(),
        content=req.content.strip(),
        game_id=req.game_id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    return _item_to_out(item)


# ── Idea Queue (user-curated playlist of KnowledgeItems) ─────────────────────


class IdeaQueueUpdate(BaseModel):
    """Add or remove a KnowledgeItem from the user's idea queue."""
    knowledge_item_id: int


@router.get("/idea-queue")
def get_idea_queue(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get the user's idea queue (ordered list of KnowledgeItem IDs).

    The automation consumes this queue first (FIFO) before falling back
    to autonomous editorial selection. When a video is generated from a
    queued idea, it's removed from the queue.
    """
    from gpcg.domain.models import Automation, KnowledgeItem
    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    if not auto:
        return {"queue": [], "items": []}
    queue_ids: list[int] = (auto.config or {}).get("idea_queue", [])
    # Fetch the actual items (preserve order)
    items = []
    for ki_id in queue_ids:
        ki = db.get(KnowledgeItem, ki_id)
        if ki:
            items.append(_item_to_out(ki))
    return {"queue": queue_ids, "items": items}


@router.post("/idea-queue/add")
def add_to_idea_queue(
    req: IdeaQueueUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Add a KnowledgeItem to the end of the user's idea queue."""
    from sqlalchemy.orm.attributes import flag_modified
    from gpcg.domain.models import Automation, KnowledgeItem, KnowledgeItemStatus
    ki = db.get(KnowledgeItem, req.knowledge_item_id)
    if not ki:
        raise HTTPException(404, "KnowledgeItem not found")
    if ki.status != KnowledgeItemStatus.fresh.value:
        raise HTTPException(400, f"KnowledgeItem is not fresh (status={ki.status})")
    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    if not auto:
        raise HTTPException(404, "Automation not found")
    config = dict(auto.config or {})
    queue: list = list(config.get("idea_queue", []))
    if req.knowledge_item_id not in queue:
        queue.append(req.knowledge_item_id)
        config["idea_queue"] = queue
        auto.config = config
        flag_modified(auto, "config")
        db.commit()
    return {"queue": queue, "message": "Added to queue"}


@router.post("/idea-queue/remove")
def remove_from_idea_queue(
    req: IdeaQueueUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Remove a KnowledgeItem from the user's idea queue."""
    from sqlalchemy.orm.attributes import flag_modified
    from gpcg.domain.models import Automation
    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    if not auto:
        raise HTTPException(404, "Automation not found")
    config = dict(auto.config or {})
    queue: list = list(config.get("idea_queue", []))
    if req.knowledge_item_id in queue:
        queue.remove(req.knowledge_item_id)
        config["idea_queue"] = queue
        auto.config = config
        flag_modified(auto, "config")
        db.commit()
    return {"queue": queue, "message": "Removed from queue"}


@router.post("/idea-queue/reorder")
def reorder_idea_queue(
    new_order: list[int],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Reorder the user's idea queue. Receives the full ordered list of IDs."""
    from sqlalchemy.orm.attributes import flag_modified
    from gpcg.domain.models import Automation
    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    if not auto:
        raise HTTPException(404, "Automation not found")
    config = dict(auto.config or {})
    config["idea_queue"] = new_order
    auto.config = config
    flag_modified(auto, "config")
    db.commit()
    return {"queue": new_order, "message": "Queue reordered"}
