"""Kids Idea API routes — idea management, safety, scoring, and conversion.

These endpoints are Kids-specific and complement the existing kids_routes.py.
They only make sense when the channel's domain is "kids".

Endpoints:
- GET    /api/kids/ideas              — list ideas (with filters)
- GET    /api/kids/ideas/{id}         — get idea detail
- POST   /api/kids/ideas              — create manual idea
- POST   /api/kids/ideas/{id}/reject  — reject an idea
- POST   /api/kids/ideas/{id}/score   — trigger safety + scoring (manual)
- POST   /api/kids/ideas/{id}/convert — convert idea → KidsTopic
- GET    /api/kids/ideas/stats        — statistics
- GET    /api/kids/idea-queue         — get the kids idea queue
- POST   /api/kids/idea-queue/add     — add idea to queue
- POST   /api/kids/idea-queue/remove  — remove idea from queue
- POST   /api/kids/idea-queue/reorder — reorder queue
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from gpcg.core.models import ChannelProfile, ContentDomain, User, WorkerCapability
from gpcg.domains.kids.models import (
    KidsIdea,
    KidsIdeaSource,
    KidsIdeaStatus,
    KidsTopic,
)
from gpcg.domains.kids.idea_service import (
    compute_content_hash,
    convert_to_topic,
    create_idea,
    get_by_id,
    get_stats,
    is_duplicate_topic,
    is_terminal,
    list_ideas,
    reject_idea,
    update_status,
)
from gpcg.infrastructure.auth import get_current_user
from gpcg.infrastructure.database import get_db
from gpcg.logging import get_logger

log = get_logger(__name__)

router = APIRouter()


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


def _get_safety_strictness(profile: ChannelProfile) -> float:
    """Get safety strictness from channel profile metadata."""
    meta = profile.metadata_json or {}
    return float(meta.get("kids_safety_strictness", 0.7))


def _get_age_range(profile: ChannelProfile) -> str:
    """Get target age range from channel profile metadata."""
    meta = profile.metadata_json or {}
    return str(meta.get("age_range", meta.get("kids_age_range", "3-6")))


def _idea_to_out(idea: KidsIdea) -> dict:
    """Convert a KidsIdea to a dict for API response."""
    return {
        "id": idea.id,
        "title": idea.title,
        "description": idea.description,
        "category": idea.category,
        "suggested_age_range": idea.suggested_age_range,
        "source": idea.source,
        "source_metadata": idea.source_metadata,
        "editorial_score": idea.editorial_score,
        "safety_score": idea.safety_score,
        "age_fit_score": idea.age_fit_score,
        "educational_value": idea.educational_value,
        "curiosity_score": idea.curiosity_score,
        "visual_potential": idea.visual_potential,
        "final_score": idea.final_score,
        "score_breakdown": idea.score_breakdown,
        "safety_flags": idea.safety_flags,
        "safety_reviewed": idea.safety_reviewed,
        "status": idea.status,
        "rejection_reason": idea.rejection_reason,
        "content_hash": idea.content_hash,
        "topic_id": idea.topic_id,
        "created_at": idea.created_at.isoformat() if idea.created_at else None,
        "updated_at": idea.updated_at.isoformat() if idea.updated_at else None,
    }


# ── Request/Response models ──────────────────────────────────────────────────


class IdeaCreate(BaseModel):
    """Manual idea creation."""
    title: str
    description: str = ""
    category: str = "general"
    suggested_age_range: str = "3-6"


class IdeaReject(BaseModel):
    """Reject an idea."""
    reason: str = ""


class IdeaConvert(BaseModel):
    """Convert an idea to a KidsTopic."""
    editorial_intent: str = "curiosity"
    educational_goal: str = "general"
    description_override: Optional[str] = None


class QueueAddRequest(BaseModel):
    """Add an idea to the queue."""
    idea_id: int


class QueueRemoveRequest(BaseModel):
    """Remove an idea from the queue."""
    idea_id: int


class QueueReorderRequest(BaseModel):
    """Reorder the queue."""
    idea_ids: list[int]  # new order


# ── Idea CRUD ────────────────────────────────────────────────────────────────


@router.get("/kids/ideas")
def list_kids_ideas(
    status: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List KidsIdeas for the current user with optional filters."""
    _require_kids_domain(user, db)
    ideas = list_ideas(
        db, user.id,
        status=status,
        category=category,
        limit=min(limit, 200),
        offset=offset,
    )
    return {"ideas": [_idea_to_out(i) for i in ideas]}


@router.get("/kids/ideas/stats")
def get_kids_idea_stats(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get statistics about KidsIdeas for the current user."""
    _require_kids_domain(user, db)
    return get_stats(db, user.id)


@router.get("/kids/ideas/{idea_id}")
def get_kids_idea(
    idea_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get detailed information about a specific KidsIdea."""
    _require_kids_domain(user, db)
    idea = get_by_id(db, idea_id)
    if not idea or idea.user_id != user.id:
        raise HTTPException(404, "KidsIdea not found")
    return _idea_to_out(idea)


@router.post("/kids/ideas")
def create_manual_idea(
    req: IdeaCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a manual KidsIdea (user-curated).

    The idea is created with status=discovered. Safety review and scoring
    can be triggered separately via POST /kids/ideas/{id}/score.
    """
    profile = _require_kids_domain(user, db)

    if not req.title or not req.title.strip():
        raise HTTPException(422, "title must not be empty")
    if len(req.title) > 500:
        raise HTTPException(422, "title must be at most 500 characters")

    # Check for duplicate topic (don't create an idea for a topic that exists)
    if is_duplicate_topic(db, user.id, req.title):
        raise HTTPException(
            409,
            "A KidsTopic with a similar title already exists. "
            "Use the existing topic instead of creating a duplicate idea.",
        )

    idea = create_idea(
        db, user.id,
        title=req.title,
        description=req.description,
        category=req.category,
        suggested_age_range=req.suggested_age_range,
        source=KidsIdeaSource.manual.value,
    )
    if not idea:
        raise HTTPException(
            409,
            "A KidsIdea with the same content already exists (duplicate).",
        )
    db.commit()
    return _idea_to_out(idea)


class DiscoverRequest(BaseModel):
    """Trigger AI ideation discovery."""
    categories: Optional[list[str]] = None  # defaults to all
    ideas_per_category: int = 3
    include_seasonal: bool = True
    include_topic_library: bool = True


@router.post("/kids/ideas/discover")
def discover_kids_ideas(
    req: DiscoverRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Trigger Kids idea discovery (AI ideation + topic library + seasonal).

    Creates a kids_idea_discovery job for the remote worker to process.
    The worker runs AI ideation (needs LLM) + topic library + seasonal
    locally and syncs the created ideas back to the VPS.

    Returns the job info immediately — the frontend should poll the job
    status to know when discovery is complete.
    """
    profile = _require_kids_domain(user, db)

    import uuid
    from gpcg.core.models import Job, JobType, JobStatus, JobPriority

    job = Job(
        job_uuid=str(uuid.uuid4()),
        type=JobType.kids_idea_discovery.value,
        status=JobStatus.queued.value,
        stage="discovery",
        domain=ContentDomain.kids.value,
        user_id=user.id,
        priority=JobPriority.normal.value,
        required_capabilities=[WorkerCapability.content_intelligence.value],
        artifacts={
            "categories": req.categories,
            "ideas_per_category": min(req.ideas_per_category, 10),
            "include_seasonal": req.include_seasonal,
            "include_topic_library": req.include_topic_library,
        },
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    from gpcg.infrastructure.job_queue import enqueue_job
    enqueue_job(job)

    from gpcg.infrastructure.events import publish_job_created
    publish_job_created(user.id, job.id, job.type, job.priority)
    log.info(f"Created kids_idea_discovery job #{job.id} for user {user.id}")
    return {
        "job_id": job.id,
        "job_status": job.status,
        "message": "Discovery job queued — worker will process it when available.",
    }


@router.get("/kids/topic-library")
def get_topic_library(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the Kids topic library (categories and seeds)."""
    _require_kids_domain(user, db)
    from gpcg.domains.kids.topic_library import get_all_categories

    cats = get_all_categories()
    return {
        "categories": [
            {
                "name": cat.name,
                "display_name": cat.display_name,
                "description": cat.description,
                "seeds": [
                    {"title_hint": s.title_hint, "description": s.description}
                    for s in cat.seeds
                ],
            }
            for cat in cats
        ]
    }


@router.get("/kids/seasonal-calendar")
def get_seasonal_calendar(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the seasonal calendar (active entries)."""
    _require_kids_domain(user, db)
    from gpcg.domains.kids.seasonal_calendar import get_active_seasonal, get_all_entries

    active = get_active_seasonal()
    all_entries = get_all_entries()
    return {
        "active": [
            {
                "name": e.name,
                "date": e.date,
                "description": e.description,
                "category": e.category,
            }
            for e in active
        ],
        "all": [
            {
                "name": e.name,
                "date": e.date,
                "description": e.description,
                "category": e.category,
            }
            for e in all_entries
        ],
    }


@router.post("/kids/ideas/{idea_id}/reject")
def reject_kids_idea(
    idea_id: int,
    req: IdeaReject,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reject a KidsIdea."""
    _require_kids_domain(user, db)
    idea = get_by_id(db, idea_id)
    if not idea or idea.user_id != user.id:
        raise HTTPException(404, "KidsIdea not found")
    if not reject_idea(db, idea_id, req.reason):
        raise HTTPException(400, "Idea cannot be rejected (not found or already terminal)")
    db.commit()
    return {"rejected": True, "idea_id": idea_id}


@router.post("/kids/ideas/{idea_id}/score")
def score_kids_idea(
    idea_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Trigger safety review + scoring for a KidsIdea.

    Creates a kids_idea_score job for the remote worker to process.
    The worker runs the KidsSafetyFilter (LLM review) and KidsScorer
    locally and syncs the results back to the VPS.

    Returns the job info immediately — the frontend should poll the job
    status to know when scoring is complete.
    """
    profile = _require_kids_domain(user, db)
    idea = get_by_id(db, idea_id)
    if not idea or idea.user_id != user.id:
        raise HTTPException(404, "KidsIdea not found")

    if idea.status == KidsIdeaStatus.converted.value:
        raise HTTPException(400, "Cannot score a converted idea")

    import uuid
    from gpcg.core.models import Job, JobType, JobStatus, JobPriority

    job = Job(
        job_uuid=str(uuid.uuid4()),
        type=JobType.kids_idea_score.value,
        status=JobStatus.queued.value,
        stage="scoring",
        domain=ContentDomain.kids.value,
        user_id=user.id,
        priority=JobPriority.normal.value,
        required_capabilities=[WorkerCapability.content_intelligence.value],
        artifacts={
            "idea_id": idea_id,
        },
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    from gpcg.infrastructure.job_queue import enqueue_job
    enqueue_job(job)

    from gpcg.infrastructure.events import publish_job_created
    publish_job_created(user.id, job.id, job.type, job.priority)
    log.info(f"Created kids_idea_score job #{job.id} for idea #{idea_id}")
    return {
        "job_id": job.id,
        "job_status": job.status,
        "idea_id": idea_id,
        "message": "Scoring job queued — worker will process it when available.",
    }


@router.post("/kids/ideas/{idea_id}/convert")
def convert_kids_idea_to_topic(
    idea_id: int,
    req: IdeaConvert,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Convert a KidsIdea into a KidsTopic.

    Creates a KidsTopic with the idea's metadata and links them.
    The user then needs to upload story assets to the topic before
    generating a video.
    """
    _require_kids_domain(user, db)
    idea = get_by_id(db, idea_id)
    if not idea or idea.user_id != user.id:
        raise HTTPException(404, "KidsIdea not found")

    topic = convert_to_topic(
        db, idea_id,
        editorial_intent=req.editorial_intent,
        educational_goal=req.educational_goal,
        description_override=req.description_override,
    )
    if not topic:
        raise HTTPException(
            409,
            "Cannot convert: idea already converted or duplicate topic exists",
        )
    db.commit()
    return {
        "topic_id": topic.id,
        "idea_id": idea_id,
        "title": topic.title,
        "slug": topic.slug,
        "category": topic.category,
        "age_range": topic.age_range,
    }


@router.post("/kids/ideas/{idea_id}/produce")
def produce_kids_idea(
    idea_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One-step production: convert idea to topic + create generation job.

    This is the end-to-end flow:
        KidsIdea → KidsTopic → generate_short job

    Requires:
    - Idea must be in a non-terminal status (discovered/evaluated/queued)
    - If idea is already converted, uses the existing topic
    - Topic must have at least one ready StoryAsset

    Returns:
        {idea_id, topic_id, job_id}
    """
    _require_kids_domain(user, db)

    idea = get_by_id(db, idea_id)
    if not idea or idea.user_id != user.id:
        raise HTTPException(404, "Idea not found")
    # Allow production from any non-rejected, non-expired status.
    # 'converted' is allowed — the idea was already converted to a topic,
    # and we just need to create the generation job.
    if idea.status in (KidsIdeaStatus.rejected.value, KidsIdeaStatus.expired.value):
        raise HTTPException(409, f"Cannot produce: idea is in status '{idea.status}'")

    # Convert to topic if not already converted
    topic_id = idea.topic_id
    if not topic_id:
        topic = convert_to_topic(db, idea_id)
        if not topic:
            raise HTTPException(
                409,
                "Cannot convert: idea already converted or duplicate topic exists",
            )
        topic_id = topic.id
        db.flush()

    # Verify topic has ready assets
    from gpcg.domains.kids.models import KidsTopic, StoryAsset, AssetProcessingStatus
    topic = db.query(KidsTopic).filter(KidsTopic.id == topic_id).first()
    if not topic:
        raise HTTPException(404, "Topic not found after conversion")

    asset_count = db.query(StoryAsset).filter(
        StoryAsset.topic_id == topic_id,
        StoryAsset.processing_status == AssetProcessingStatus.ready.value,
    ).count()
    if asset_count == 0:
        raise HTTPException(
            422,
            "Topic has no ready assets. Upload images before generating a video.",
        )

    # Create the generation job
    import uuid as _uuid
    from gpcg.core.models import Job, JobStatus, JobType, JobPriority, ContentDomain

    artifacts = {
        "topic_id": topic_id,
        "topic_title": topic.title,
        "idea_id": idea_id,
        "source": "kids_idea_produce",
    }
    job = Job(
        job_uuid=str(_uuid.uuid4()),
        type=JobType.generate_short.value,
        user_id=user.id,
        domain=ContentDomain.kids.value,
        status=JobStatus.queued.value,
        priority=JobPriority.normal.value,
        required_capabilities=[WorkerCapability.generation.value],
        artifacts=artifacts,
    )
    db.add(job)
    db.commit()
    from gpcg.infrastructure.job_queue import enqueue_job
    enqueue_job(job)

    from gpcg.infrastructure.events import publish_job_created, publish_kids_idea_updated
    publish_job_created(user.id, job.id, job.type, job.priority)
    publish_kids_idea_updated(user.id, idea.id, idea.status)
    log.info(
        f"Kids produce: idea #{idea_id} → topic #{topic_id} → job #{job.id}"
    )
    return {
        "idea_id": idea_id,
        "topic_id": topic_id,
        "job_id": job.id,
    }


@router.get("/kids/ideas/{idea_id}/provenance")
def get_idea_provenance(
    idea_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the full provenance chain for a KidsIdea.

    Returns the idea, its topic (if converted), and all jobs/videos
    produced from that topic. This enables traceability from idea to
    final video.

    Returns:
        {idea, topic, jobs, videos}
    """
    _require_kids_domain(user, db)

    idea = get_by_id(db, idea_id)
    if not idea or idea.user_id != user.id:
        raise HTTPException(404, "Idea not found")

    result: dict = {"idea": _idea_to_out(idea)}

    # Topic
    if idea.topic_id:
        from gpcg.domains.kids.models import KidsTopic, StoryAsset, AssetProcessingStatus
        topic = db.query(KidsTopic).filter(KidsTopic.id == idea.topic_id).first()
        if topic:
            assets = db.query(StoryAsset).filter(
                StoryAsset.topic_id == topic.id
            ).all()
            result["topic"] = {
                "id": topic.id,
                "title": topic.title,
                "slug": topic.slug,
                "category": topic.category,
                "age_range": topic.age_range,
                "idea_id": topic.idea_id,
                "asset_count": len(assets),
                "ready_asset_count": sum(
                    1 for a in assets
                    if a.processing_status == AssetProcessingStatus.ready.value
                ),
            }

            # Jobs for this topic
            from gpcg.core.models import Job
            jobs = db.query(Job).filter(
                Job.user_id == user.id,
                Job.artifacts["topic_id"].as_integer() == topic.id,
            ).all() if hasattr(Job.artifacts, "as_integer") else []

            # Fallback: linear scan (SQLite JSON queries may not be supported)
            if not jobs:
                all_jobs = db.query(Job).filter(
                    Job.user_id == user.id,
                    Job.domain == ContentDomain.kids.value,
                ).all()
                jobs = [
                    j for j in all_jobs
                    if (j.artifacts or {}).get("topic_id") == topic.id
                ]

            result["jobs"] = [
                {
                    "id": j.id,
                    "type": j.type,
                    "status": j.status,
                    "stage": j.stage,
                    "created_at": j.created_at.isoformat() if j.created_at else None,
                    "idea_id": (j.artifacts or {}).get("idea_id"),
                }
                for j in jobs
            ]

            # Videos produced from these jobs
            from gpcg.core.models import Video
            job_ids = [j.id for j in jobs]
            if job_ids:
                videos = db.query(Video).filter(
                    Video.job_id.in_(job_ids)
                ).all()
                result["videos"] = [
                    {
                        "id": v.id,
                        "job_id": v.job_id,
                        "title": v.title,
                        "youtube_url": getattr(v, "youtube_url", None),
                        "youtube_video_id": getattr(v, "youtube_video_id", None),
                    }
                    for v in videos
                ]
            else:
                result["videos"] = []
        else:
            result["topic"] = None
            result["jobs"] = []
            result["videos"] = []
    else:
        result["topic"] = None
        result["jobs"] = []
        result["videos"] = []

    return result


# ── Idea Queue ───────────────────────────────────────────────────────────────


def _get_kids_queue(config: dict) -> list[int]:
    """Extract the kids_idea_queue from automation config."""
    queue = config.get("kids_idea_queue", [])
    if not isinstance(queue, list):
        return []
    result: list[int] = []
    for item in queue:
        if isinstance(item, int):
            result.append(item)
        elif isinstance(item, str) and item.isdigit():
            result.append(int(item))
    return result


def _set_kids_queue(db: Session, user_id: int, queue: list[int]) -> None:
    """Set the kids_idea_queue in the user's automation config."""
    from sqlalchemy.orm.attributes import flag_modified
    from gpcg.core.models import Automation

    auto = db.query(Automation).filter(Automation.user_id == user_id).first()
    if not auto:
        # Create automation if it doesn't exist
        auto = Automation(user_id=user_id, name="Automação", config={})
        db.add(auto)
        db.flush()
    config = dict(auto.config or {})
    config["kids_idea_queue"] = queue
    auto.config = config
    flag_modified(auto, "config")
    db.flush()


@router.get("/kids/idea-queue")
def get_kids_idea_queue(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the user's Kids idea queue (ordered list of idea IDs with details).

    Also cleans the queue (removes rejected/expired/converted ideas) and
    runs reconciliation if auto-fill is enabled.
    """
    _require_kids_domain(user, db)
    from gpcg.core.models import Automation
    from gpcg.domains.kids.idea_service import clean_kids_queue, reconcile_kids_queue

    # Clean invalid entries
    clean_kids_queue(db, user.id)
    # Auto-fill if enabled
    reconcile_kids_queue(db, user.id)
    db.commit()

    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    if not auto:
        return {"queue": [], "items": []}

    queue_ids = _get_kids_queue(auto.config or {})
    items = []
    for idea_id in queue_ids:
        idea = get_by_id(db, idea_id)
        if idea and idea.user_id == user.id:
            item = _idea_to_out(idea)
            items.append(item)

    return {"queue": queue_ids, "items": items}


@router.post("/kids/idea-queue/add")
def add_to_kids_idea_queue(
    req: QueueAddRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add a KidsIdea to the end of the user's idea queue."""
    _require_kids_domain(user, db)
    idea = get_by_id(db, req.idea_id)
    if not idea or idea.user_id != user.id:
        raise HTTPException(404, "KidsIdea not found")
    if idea.status == KidsIdeaStatus.rejected.value:
        raise HTTPException(400, "Cannot queue a rejected idea")
    if idea.status == KidsIdeaStatus.converted.value:
        raise HTTPException(400, "Cannot queue a converted idea")

    from gpcg.core.models import Automation
    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    if not auto:
        auto = Automation(user_id=user.id, name="Automação", config={})
        db.add(auto)
        db.flush()

    config = dict(auto.config or {})
    queue = _get_kids_queue(config)
    max_size = config.get("kids_max_queue_size", 10)

    if req.idea_id in queue:
        raise HTTPException(409, "Idea already in queue")
    if len(queue) >= max_size:
        raise HTTPException(400, f"Queue is full (max {max_size})")

    queue.append(req.idea_id)
    _set_kids_queue(db, user.id, queue)

    # Update idea status if it was evaluated
    if idea.status == KidsIdeaStatus.evaluated.value:
        update_status(db, req.idea_id, KidsIdeaStatus.queued.value)

    db.commit()
    return {"queue": queue, "message": "Added to queue"}


@router.post("/kids/idea-queue/remove")
def remove_from_kids_idea_queue(
    req: QueueRemoveRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a KidsIdea from the user's idea queue."""
    _require_kids_domain(user, db)
    from gpcg.core.models import Automation

    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    if not auto:
        return {"queue": [], "message": "Queue is empty"}

    queue = _get_kids_queue(auto.config or {})
    if req.idea_id not in queue:
        raise HTTPException(404, "Idea not in queue")

    queue.remove(req.idea_id)
    _set_kids_queue(db, user.id, queue)

    # Revert idea status from queued → evaluated
    idea = get_by_id(db, req.idea_id)
    if idea and idea.status == KidsIdeaStatus.queued.value:
        update_status(db, req.idea_id, KidsIdeaStatus.evaluated.value)

    db.commit()
    return {"queue": queue, "message": "Removed from queue"}


@router.post("/kids/idea-queue/reorder")
def reorder_kids_idea_queue(
    req: QueueReorderRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reorder the user's Kids idea queue."""
    _require_kids_domain(user, db)
    _set_kids_queue(db, user.id, req.idea_ids)
    db.commit()
    return {"queue": req.idea_ids, "message": "Queue reordered"}


@router.post("/kids/idea-queue/reconcile")
def reconcile_kids_idea_queue(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually trigger queue reconciliation (clean + auto-fill)."""
    _require_kids_domain(user, db)
    from gpcg.domains.kids.idea_service import clean_kids_queue, reconcile_kids_queue

    removed = clean_kids_queue(db, user.id)
    added = reconcile_kids_queue(db, user.id)
    db.commit()

    return {
        "removed": removed,
        "added": added,
        "message": f"Cleaned {removed} invalid entries, added {added} new ideas",
    }
