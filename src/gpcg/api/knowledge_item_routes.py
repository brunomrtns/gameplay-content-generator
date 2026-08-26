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
from gpcg.core.models import (
    ChannelProfile,
    ContentDomain,
    Job,
    JobPriority,
    JobStatus,
    JobType,
    User,
)
from gpcg.domains.games.models import Game
from gpcg.infrastructure.auth import get_current_user
from gpcg.infrastructure.database import get_db

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────


class KnowledgeItemOut(BaseModel):
    id: int
    game_id: Optional[int] = None
    game_name: Optional[str] = None
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


def _item_to_out(item, db=None) -> KnowledgeItemOut:
    game_name = None
    if item.game_id and db is not None:
        from gpcg.core.models import Game
        game = db.get(Game, item.game_id)
        if game:
            game_name = game.canonical_name
    return KnowledgeItemOut(
        id=item.id,
        game_id=item.game_id,
        game_name=game_name,
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
        "items": [_item_to_out(i, db) for i in items],
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
    return _item_to_out(item, db)


@router.post("/knowledge-items/{item_id}/reject")
def reject_knowledge_item(
    item_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Reject a KnowledgeItem (mark as rejected).

    V2: When the feedback loop is enabled, the rejection is propagated to
    similar KIs via embeddings (penalty) and recorded in editorial_signals.
    """
    if not reject_item(db, item_id):
        raise HTTPException(status_code=404, detail="KnowledgeItem not found")

    # V2: Propagate rejection feedback
    try:
        from gpcg.application.feedback_propagator import FeedbackPropagator
        FeedbackPropagator().propagate_rejection(db, user.id, item_id)
    except Exception as e:
        # Non-fatal — rejection still succeeds
        pass

    db.commit()
    return {"message": "KnowledgeItem rejected"}


@router.post("/knowledge-items/collect")
def trigger_content_collection(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Trigger manual content collection (creates a content_collect job).

    The job collects RSS for all games with gameplay available.
    Multiple jobs can be queued — they are processed in order by the worker.
    """
    import uuid
    from sqlalchemy import select, func

    # Soft cap: prevent spamming the queue with too many pending jobs.
    # Allow up to 3 queued content_collect jobs (the current one may be
    # running, so this leaves room for a couple more in the queue).
    queued_count = db.execute(
        select(func.count(Job.id)).where(
            Job.type == JobType.content_collect.value,
            Job.status == JobStatus.queued.value,
        )
    ).scalar() or 0
    if queued_count >= 3:
        raise HTTPException(
            status_code=409,
            detail="Já existem 3 jobs de coleta na fila. Aguarde processar para criar mais.",
        )

    # Set domain from channel profile
    _domain = ContentDomain.games.value
    _profile = db.query(ChannelProfile).filter(
        ChannelProfile.user_id == user.id
    ).first()
    if _profile:
        _domain = _profile.domain
    job = Job(
        job_uuid=str(uuid.uuid4()),
        type=JobType.content_collect.value,
        status=JobStatus.queued.value,
        stage="content_collection",
        domain=_domain,
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
    from gpcg.core.models import KnowledgeItem, KnowledgeItemSource, KnowledgeItemStatus

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

    # V2: Propagate manual-add boost feedback
    try:
        from gpcg.application.feedback_propagator import FeedbackPropagator
        FeedbackPropagator().propagate_manual_add(db, user.id, item.id)
        db.commit()
    except Exception:
        pass  # non-fatal

    return _item_to_out(item, db)


# ── Idea Queue (user-curated playlist of KnowledgeItems) ─────────────────────


class IdeaQueueUpdate(BaseModel):
    """Add or remove a KnowledgeItem from the user's idea queue."""
    knowledge_item_id: int


def _normalize_queue_entry(entry) -> dict:
    """Normalize a queue entry to dict format.

    Backward compat: plain int → {"ki_id": int, "gameplay_preference": None, "reuse_override": None}
    """
    if isinstance(entry, dict):
        return {
            "ki_id": entry.get("ki_id") or entry.get("id"),
            "gameplay_preference": entry.get("gameplay_preference"),
            "reuse_override": entry.get("reuse_override"),
        }
    if isinstance(entry, int):
        return {"ki_id": entry, "gameplay_preference": None, "reuse_override": None}
    if isinstance(entry, str):
        try:
            return {"ki_id": int(entry), "gameplay_preference": None, "reuse_override": None}
        except ValueError:
            return {"ki_id": None, "gameplay_preference": None, "reuse_override": None}
    return {"ki_id": None, "gameplay_preference": None, "reuse_override": None}


def _normalize_idea_queue(raw) -> list[dict]:
    """Normalize the idea_queue config value to list[dict]."""
    if not raw:
        return []
    return [_normalize_queue_entry(e) for e in raw]


def _queue_ki_ids(queue: list[dict]) -> list[int]:
    """Extract just the KI IDs from a normalized queue."""
    return [e["ki_id"] for e in queue if e.get("ki_id") is not None]


@router.get("/idea-queue")
def get_idea_queue(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get the user's idea queue (ordered list with metadata).

    The automation consumes this queue first (FIFO) before falling back
    to autonomous editorial selection. When a video is generated from a
    queued idea, it's removed from the queue.

    V3: Each queue entry is a dict with:
        - ki_id: KnowledgeItem ID
        - gameplay_preference: null (auto) or game_id (user chose specific game)
        - reuse_override: null (use global config), "allow_reuse", or "skip"

    V3: Also triggers the reconciliador — if auto_fill_queue is enabled and
    the queue is below max_queue_size, fresh KIs are added automatically.
    This runs on the VPS when the user opens the ideas page, so the queue
    is filled immediately without waiting for the worker to poll.
    """
    from gpcg.core.models import Automation, KnowledgeItem
    # V3: Reconcile before returning — fill queue if auto_fill_queue is on
    try:
        from gpcg.api.automation_routes import reconcile_user_queue
        added = reconcile_user_queue(db, user.id)
        if added > 0:
            db.commit()
    except Exception:
        pass  # non-fatal — just return the queue as-is

    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    if not auto:
        return {"queue": [], "items": []}
    queue = _normalize_idea_queue((auto.config or {}).get("idea_queue", []))
    # Fetch the actual items (preserve order)
    items = []
    for entry in queue:
        ki_id = entry.get("ki_id")
        if ki_id is None:
            continue
        ki = db.get(KnowledgeItem, ki_id)
        if ki:
            item_out = _item_to_out(ki, db).model_dump()
            item_out["gameplay_preference"] = entry.get("gameplay_preference")
            item_out["reuse_override"] = entry.get("reuse_override")
            items.append(item_out)
    return {"queue": queue, "items": items}


class IdeaQueueAddRequest(BaseModel):
    """Add a KnowledgeItem to the queue with optional gameplay preference."""
    knowledge_item_id: int
    gameplay_preference: Optional[int] = None  # null=auto, game_id=user chose
    reuse_override: Optional[str] = None  # null, "allow_reuse", "skip"


@router.post("/idea-queue/add")
def add_to_idea_queue(
    req: IdeaQueueAddRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Add a KnowledgeItem to the end of the user's idea queue.

    V3: Accepts gameplay_preference (null=auto, game_id=specific game) and
    reuse_override (null=use global config, "allow_reuse"=exceptional reuse,
    "skip"=don't generate without material).
    """
    from sqlalchemy.orm.attributes import flag_modified
    from gpcg.core.models import Automation, KnowledgeItem, KnowledgeItemStatus
    ki = db.get(KnowledgeItem, req.knowledge_item_id)
    if not ki:
        raise HTTPException(404, "KnowledgeItem not found")
    if ki.status != KnowledgeItemStatus.fresh.value:
        raise HTTPException(400, f"KnowledgeItem is not fresh (status={ki.status})")
    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    if not auto:
        raise HTTPException(404, "Automation not found")
    config = dict(auto.config or {})
    queue = _normalize_idea_queue(config.get("idea_queue", []))
    existing_ids = _queue_ki_ids(queue)
    if req.knowledge_item_id not in existing_ids:
        queue.append({
            "ki_id": req.knowledge_item_id,
            "gameplay_preference": req.gameplay_preference,
            "reuse_override": req.reuse_override,
        })
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
    from gpcg.core.models import Automation
    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    if not auto:
        raise HTTPException(404, "Automation not found")
    config = dict(auto.config or {})
    queue = _normalize_idea_queue(config.get("idea_queue", []))
    new_queue = [e for e in queue if e["ki_id"] != req.knowledge_item_id]
    if len(new_queue) != len(queue):
        config["idea_queue"] = new_queue
        auto.config = config
        flag_modified(auto, "config")
        db.commit()
    return {"queue": new_queue, "message": "Removed from queue"}


@router.post("/idea-queue/reorder")
def reorder_idea_queue(
    new_order: list[int],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Reorder the user's idea queue. Receives the full ordered list of IDs.

    V3: Preserves existing metadata (gameplay_preference, reuse_override)
    for items that already have it. New items get defaults.
    """
    from sqlalchemy.orm.attributes import flag_modified
    from gpcg.core.models import Automation
    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    if not auto:
        raise HTTPException(404, "Automation not found")
    config = dict(auto.config or {})
    old_queue = _normalize_idea_queue(config.get("idea_queue", []))
    old_by_id = {e["ki_id"]: e for e in old_queue if e.get("ki_id")}
    new_queue = []
    for ki_id in new_order:
        if ki_id in old_by_id:
            new_queue.append(old_by_id[ki_id])
        else:
            new_queue.append({"ki_id": ki_id, "gameplay_preference": None, "reuse_override": None})
    config["idea_queue"] = new_queue
    auto.config = config
    flag_modified(auto, "config")
    db.commit()
    return {"queue": new_queue, "message": "Queue reordered"}


class IdeaQueueUpdateRequest(BaseModel):
    """Update gameplay_preference and/or reuse_override for an existing queue item."""
    knowledge_item_id: int
    gameplay_preference: Optional[int] = None  # null=auto, game_id=user chose
    reuse_override: Optional[str] = None  # null, "allow_reuse", "skip"


@router.post("/idea-queue/update")
def update_idea_queue_item(
    req: IdeaQueueUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update gameplay_preference and/or reuse_override for an existing queue item."""
    from sqlalchemy.orm.attributes import flag_modified
    from gpcg.core.models import Automation

    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    if not auto:
        raise HTTPException(404, "Automation not found")
    config = dict(auto.config or {})
    queue = _normalize_idea_queue(config.get("idea_queue", []))

    # Update the matching queue entry
    updated = False
    for entry in queue:
        if entry.get("ki_id") == req.knowledge_item_id:
            entry["gameplay_preference"] = req.gameplay_preference
            entry["reuse_override"] = req.reuse_override
            updated = True
            break

    if not updated:
        raise HTTPException(404, "Item not found in queue")

    config["idea_queue"] = queue
    auto.config = config
    flag_modified(auto, "config")
    db.commit()
    return {"queue": queue, "message": "Queue item updated"}
