"""Kids domain endpoints — asset processing, mapping, thumbnails, idea system sync.

These endpoints cover the Kids domain worker interactions:
  - Kids asset process result (FFprobe + thumbnail metadata)
  - Kids asset mapping result (semantic events from VLM + ASR)
  - Kids asset thumbnail upload
  - Channel profile fetch (for kids_idea_discovery/score jobs)
  - Kids idea fetch by ID
  - Kids idea discovery sync (worker → VPS)
  - Kids idea scoring sync (worker → VPS)
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.core.models import Job
from gpcg.infrastructure.database import get_db

from gpcg.api.workers._common import (
    _utcnow,
    worker_auth,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["workers"])


# ── Kids asset process result (worker → VPS) ─────────────────────────────────


class KidsAssetProcessResult(BaseModel):
    """Result of processing a Kids video asset on the worker.

    The worker runs FFprobe to extract metadata and optionally generates
    a thumbnail. This payload syncs the extracted data back to the VPS.
    """
    asset_id: int
    width: int = 0
    height: int = 0
    duration: float = 0.0
    codec: str = ""
    has_audio: bool = False
    thumbnail_key: str = ""  # relative path within kids_assets/ on VPS
    error: str = ""


@router.post("/kids/assets/{asset_id}/process-result")
def submit_kids_asset_process_result(
    asset_id: int,
    req: KidsAssetProcessResult,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Receive the FFprobe/thumbnail result of a Kids asset processing job.

    The worker downloads the video, runs FFprobe for metadata, generates
    a thumbnail (uploaded separately), and calls this endpoint to sync
    the results. The VPS updates the StoryAsset record and marks it
    ``mapping`` (ready for semantic mapping) — or ``failed`` if error.

    The worker then runs the VLM+ASR mapping and calls
    POST /kids/assets/{asset_id}/mapping-result with the events.
    """
    from gpcg.domains.kids.models import StoryAsset, AssetProcessingStatus

    asset = db.query(StoryAsset).filter(StoryAsset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Story asset not found")

    if req.error:
        asset.processing_status = AssetProcessingStatus.failed.value
        asset.process_error = req.error
        log.warning(f"Kids asset #{asset_id} processing failed: {req.error}")
    else:
        asset.width = req.width
        asset.height = req.height
        asset.duration = req.duration
        asset.codec = req.codec
        asset.has_audio = req.has_audio
        asset.thumbnail_key = req.thumbnail_key
        # Mark as "mapping" — the worker will run VLM+ASR next
        asset.processing_status = AssetProcessingStatus.mapping.value
        asset.process_error = ""
        log.info(
            f"Kids asset #{asset_id} FFprobe done: {req.width}x{req.height} "
            f"{req.duration:.1f}s codec={req.codec} audio={req.has_audio} → mapping"
        )

    db.commit()
    return {"ok": True, "asset_id": asset_id, "processing_status": asset.processing_status}


# ── Kids asset mapping result (worker → VPS) ─────────────────────────────────


class KidsMediaEventPayload(BaseModel):
    asset_id: int
    start_time: float
    end_time: float
    event_type: str = "unknown"
    description: str = ""
    characters: list = []
    location: str = ""
    actions: list = []
    tags: list = []
    transcript: str = ""
    visual_confidence: float = 0.0
    interesting_score: float = 0.0
    analysis_version: str = "v1"
    metadata_json: dict = {}


class KidsMappingResult(BaseModel):
    asset_id: int
    events: list[KidsMediaEventPayload] = []
    analysis_version: str = "v1"


@router.post("/kids/assets/{asset_id}/mapping-result")
def submit_kids_asset_mapping_result(
    asset_id: int,
    req: KidsMappingResult,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Receive the semantic mapping events for a Kids video asset.

    The worker runs the KidsMediaAnalyzer (VLM + ASR, same pipeline as
    GameplayAnalyzer) and sends the structured events here. The VPS
    persists them as KidsMediaEvent records and marks the asset ``ready``.

    This is the Kids equivalent of POST /gameplays/{id}/mapping-result
    in the Games domain.
    """
    from gpcg.domains.kids.models import (
        StoryAsset,
        KidsMediaEvent,
        AssetProcessingStatus,
    )

    asset = db.query(StoryAsset).filter(StoryAsset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Story asset not found")

    # Clear existing events (in case of re-mapping)
    db.query(KidsMediaEvent).filter(KidsMediaEvent.asset_id == asset_id).delete()
    db.flush()

    # Insert new events
    for evt in req.events:
        db.add(KidsMediaEvent(
            asset_id=asset_id,
            start_time=evt.start_time,
            end_time=evt.end_time,
            event_type=evt.event_type,
            description=evt.description,
            characters=evt.characters,
            location=evt.location or None,
            actions=evt.actions,
            tags=evt.tags,
            transcript=evt.transcript,
            visual_confidence=evt.visual_confidence,
            interesting_score=evt.interesting_score,
            analysis_version=evt.analysis_version or req.analysis_version,
            metadata_json=evt.metadata_json,
        ))

    # Mark asset as ready + update analysis metadata
    asset.processing_status = AssetProcessingStatus.ready.value
    asset.process_error = ""
    asset.metadata_json = {
        **(asset.metadata_json or {}),
        "analysis": {
            "status": "ready",
            "version": req.analysis_version,
            "event_count": len(req.events),
            "analyzed_at": _utcnow().isoformat() if _utcnow else None,
        },
    }

    db.commit()
    log.info(
        f"Kids asset #{asset_id} mapping complete: {len(req.events)} events → ready"
    )
    return {
        "ok": True,
        "asset_id": asset_id,
        "event_count": len(req.events),
        "processing_status": asset.processing_status,
    }


# NOTE: GET /kids/assets/{asset_id}/events is defined in kids_routes.py with
# user auth (get_current_user). The worker does NOT need a separate events
# query endpoint — it receives events through GET /api/jobs/{id}/data which
# includes kids_media_events in the job payload.


# ── Kids asset thumbnail upload (worker → VPS) ───────────────────────────────


@router.post("/kids/assets/{asset_id}/thumbnail")
async def upload_kids_asset_thumbnail(
    asset_id: int,
    file: UploadFile = File(...),
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Receive a generated thumbnail from the worker and store it on the VPS.

    The worker generates a thumbnail from the video using FFmpeg and
    uploads it here. The thumbnail_key is set on the StoryAsset via
    the process-result endpoint (not here — this just stores the file).
    """
    from gpcg.domains.kids.models import StoryAsset
    from gpcg.config import get_settings

    asset = db.query(StoryAsset).filter(StoryAsset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Story asset not found")

    settings = get_settings()
    assets_dir = settings.data_dir / "kids_assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    thumb_name = f"thumb_{asset_id}_{file.filename or 'thumbnail.jpg'}"
    thumb_path = assets_dir / thumb_name
    content = await file.read()
    if content:
        thumb_path.write_bytes(content)
        log.info(f"Stored thumbnail for Kids asset #{asset_id}: {thumb_name}")
        return {"ok": True, "thumbnail_key": thumb_name}
    raise HTTPException(400, "Empty thumbnail file")


# ── Kids Idea System: sync endpoints ──────────────────────────────────────────


@router.get("/workers/channel-profile/{user_id}")
def worker_get_channel_profile(
    user_id: int,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Worker fetches a user's channel profile (worker-auth, not user-auth).

    Used by kids_idea_discovery and kids_idea_score jobs to get the
    editorial profile without needing user authentication.
    """
    from gpcg.core.models import ChannelProfile
    profile = db.query(ChannelProfile).filter(
        ChannelProfile.user_id == user_id
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Channel profile not found")
    return {
        "id": profile.id,
        "user_id": profile.user_id,
        "channel_description": profile.channel_description,
        "niche": profile.niche,
        "target_audience": profile.target_audience,
        "tone_of_voice": profile.tone_of_voice,
        "narrative_style": profile.narrative_style,
        "content_goals": profile.content_goals,
        "special_rules": profile.special_rules,
        "metadata_json": profile.metadata_json,
    }


@router.get("/workers/kids-ideas/{idea_id}")
def worker_get_kids_idea(
    idea_id: int,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Worker fetches a KidsIdea by ID (worker-auth, not user-auth).

    Used by kids_idea_score jobs to get the idea data without needing
    user authentication.
    """
    import gpcg.domains.kids.models  # noqa: F401 — ensure models are loaded
    from gpcg.domains.kids.models import KidsIdea
    idea = db.query(KidsIdea).filter(KidsIdea.id == idea_id).first()
    if not idea:
        raise HTTPException(status_code=404, detail="KidsIdea not found")
    return {
        "id": idea.id,
        "title": idea.title,
        "description": idea.description,
        "category": idea.category,
        "suggested_age_range": idea.suggested_age_range,
        "source": idea.source,
        "status": idea.status,
    }


class KidsIdeaSyncItem(BaseModel):
    """A single KidsIdea to sync from worker to VPS."""
    title: str
    description: str = ""
    category: str = ""
    suggested_age_range: str = "3-6"
    source: str = "ai_ideation"
    source_metadata: dict = {}
    content_hash: str = ""
    # Optional: pre-evaluated by the worker (safety + scoring already done)
    evaluated: bool = False
    safety_score: Optional[float] = None
    safety_flags: Optional[list] = None
    safety_reviewed: bool = False
    editorial_score: Optional[float] = None
    age_fit_score: Optional[float] = None
    educational_value: Optional[float] = None
    curiosity_score: Optional[float] = None
    visual_potential: Optional[float] = None
    final_score: Optional[float] = None
    score_breakdown: Optional[dict] = None


class KidsDiscoverySyncRequest(BaseModel):
    """Worker sends discovered KidsIdeas back to VPS."""
    ideas: list[KidsIdeaSyncItem] = []
    error: Optional[str] = None


@router.post("/jobs/{job_id}/sync-kids-ideas")
def sync_kids_ideas(
    job_id: int,
    req: KidsDiscoverySyncRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Worker sends discovered KidsIdeas back to VPS.

    The worker has run AI ideation + topic library + seasonal locally
    (with LLM) and sends the structured ideas to VPS for storage.
    Deduplication is handled by create_idea() on the VPS side.
    """
    import gpcg.domains.kids.models  # noqa: F401 — ensure models are loaded
    from gpcg.domains.kids.idea_service import create_idea
    from gpcg.domains.kids.models import KidsIdeaStatus

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if req.error:
        log.warning(f"Kids discovery job {job_id} failed: {req.error}")
        return {"ok": False, "error": req.error}

    created_count = 0
    skipped_count = 0
    created_titles: list[str] = []

    for item in req.ideas:
        idea = create_idea(
            db, job.user_id,
            title=item.title,
            description=item.description,
            category=item.category,
            suggested_age_range=item.suggested_age_range,
            source=item.source,
            source_metadata=item.source_metadata,
            skip_dedup=False,
        )
        if idea:
            created_count += 1
            created_titles.append(idea.title)
            # If the worker already evaluated (safety + scoring), apply scores
            if item.evaluated:
                if item.safety_score is not None:
                    idea.safety_score = item.safety_score
                if item.safety_flags is not None:
                    idea.safety_flags = item.safety_flags
                idea.safety_reviewed = item.safety_reviewed
                if item.editorial_score is not None:
                    idea.editorial_score = item.editorial_score
                if item.age_fit_score is not None:
                    idea.age_fit_score = item.age_fit_score
                if item.educational_value is not None:
                    idea.educational_value = item.educational_value
                if item.curiosity_score is not None:
                    idea.curiosity_score = item.curiosity_score
                if item.visual_potential is not None:
                    idea.visual_potential = item.visual_potential
                if item.final_score is not None:
                    idea.final_score = item.final_score
                if item.score_breakdown is not None:
                    idea.score_breakdown = item.score_breakdown
                # Transition: discovered → evaluated
                if idea.status == KidsIdeaStatus.discovered.value:
                    idea.status = KidsIdeaStatus.evaluated.value
        else:
            skipped_count += 1

    db.commit()
    log.info(
        f"Kids discovery synced to VPS for job {job_id}: "
        f"created={created_count}, skipped={skipped_count}"
    )
    return {
        "ok": True,
        "created_count": created_count,
        "skipped_count": skipped_count,
        "created_titles": created_titles,
    }


class KidsScoreSyncRequest(BaseModel):
    """Worker sends safety + scoring results back to VPS."""
    idea_id: int
    safety: dict = {}
    scoring: dict = {}
    status: str = "evaluated"
    rejection_reason: Optional[str] = None
    error: Optional[str] = None


@router.post("/jobs/{job_id}/sync-kids-score")
def sync_kids_score(
    job_id: int,
    req: KidsScoreSyncRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Worker sends safety + scoring results back to VPS.

    The worker has run KidsSafetyFilter and KidsScorer locally (with LLM)
    and sends the results to VPS for storage on the KidsIdea record.
    """
    import gpcg.domains.kids.models  # noqa: F401 — ensure models are loaded
    from gpcg.domains.kids.models import KidsIdea, KidsIdeaStatus

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if req.error:
        log.warning(f"Kids scoring job {job_id} failed: {req.error}")
        return {"ok": False, "error": req.error}

    idea = db.query(KidsIdea).filter(KidsIdea.id == req.idea_id).first()
    if not idea:
        raise HTTPException(status_code=404, detail="KidsIdea not found")

    # Update safety fields
    safety = req.safety
    idea.safety_score = safety.get("safety_score", 0.5)
    idea.safety_flags = safety.get("flags", [])
    idea.safety_reviewed = True

    # Update scoring fields
    scoring = req.scoring
    idea.editorial_score = scoring.get("editorial_score_0_100", 50.0)
    idea.age_fit_score = scoring.get("age_fit", 0.5)
    idea.educational_value = scoring.get("educational_value", 0.5)
    idea.curiosity_score = scoring.get("curiosity", 0.5)
    idea.visual_potential = scoring.get("visual_potential", 0.5)
    idea.final_score = scoring.get("final_score", 0.5)
    idea.score_breakdown = scoring.get("breakdown", {})

    # Update status
    if safety.get("safe") is False:
        idea.status = KidsIdeaStatus.rejected.value
        idea.rejection_reason = f"Safety: {safety.get('reason', 'unsafe')}"
    elif idea.status == KidsIdeaStatus.discovered.value:
        idea.status = KidsIdeaStatus.evaluated.value

    db.commit()
    log.info(f"Kids scoring synced to VPS for idea #{req.idea_id} (job {job_id})")
    return {"ok": True, "idea_id": req.idea_id, "status": idea.status}
