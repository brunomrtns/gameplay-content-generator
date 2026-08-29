"""Mapping endpoints — gameplay analysis results, game resolution, mapping job creation, events query.

These endpoints receive the structured output of the worker's GameplayAnalyzer
(VLM + ASR + merge + scoring) and persist it on the VPS. The worker sends
ONLY event metadata — never frames, crops, caches, or embeddings.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from gpcg.core.models import (
    Job,
    JobPriority,
    JobStage,
    JobStatus,
    JobType,
    User,
    WorkerCapability,
)
from gpcg.domains.games.models import (
    GameplayAsset,
    GameplayEvent,
    GameplayProcessingStatus,
    GameplaySource,
)
from gpcg.infrastructure.auth import get_current_user
from gpcg.infrastructure.database import get_db

from gpcg.api.workers._common import (
    MappingResultRequest,
    _generate_upload_token,
    _utcnow,
    worker_auth,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["workers"])


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
    from gpcg.infrastructure.events import publish_gameplay_status_changed
    publish_gameplay_status_changed(
        source.user_id, source.id, source.processing_status, source.filename,
    )
    log.info(
        f"Mapping result for #{source_id}: {len(req.events)} events persisted "
        f"(version={req.analysis_version})"
    )
    return {"ok": True, "events_persisted": len(req.events)}


# ── Game resolution (worker → VPS) ───────────────────────────────────────────


class GameResolutionRequest(BaseModel):
    """Worker sends VLM-identified game info to update the source."""
    game_name: str  # canonical name or candidate
    method: str = "vlm"
    confidence: float = 0.0
    notes: str = ""
    capture_source: Optional[str] = None


@router.post("/gameplays/{source_id}/resolve-game")
def submit_game_resolution(
    source_id: int,
    req: GameResolutionRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Worker sends VLM-identified game for a source.

    Called after the worker runs L3 (VLM) resolution locally. If the game
    doesn't exist in the registry, it's created. The source is updated with
    the resolved game_id, method, confidence, and notes.
    """
    from gpcg.domain.game_registry import get_or_create
    from gpcg.domain.slug_utils import slugify

    source = db.query(GameplaySource).filter(GameplaySource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Gameplay source not found")

    if not req.game_name or req.confidence < 0.5:
        log.info(f"Worker sent low-confidence resolution for #{source_id}: {req.game_name} (conf={req.confidence})")
        return {"ok": True, "updated": False, "reason": "low confidence"}

    # Get or create game in registry
    game = get_or_create(
        db,
        req.game_name,
        capture_sources=[req.capture_source] if req.capture_source else None,
    )
    db.flush()

    source.game_id = game.id
    source.resolution_method = req.method
    source.resolution_confidence = req.confidence
    source.resolution_notes = req.notes
    if req.capture_source:
        source.capture_source = req.capture_source
    if source.ingestion_status == "needs_review":
        source.ingestion_status = "ready"

    db.commit()
    log.info(
        f"Worker resolved game for #{source_id}: '{game.canonical_name}' "
        f"(method={req.method}, conf={req.confidence:.2f})"
    )
    return {"ok": True, "updated": True, "game_id": game.id, "game_name": game.canonical_name}


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
    # Set domain from channel profile
    from gpcg.core.models import ChannelProfile, ContentDomain
    _domain = ContentDomain.games.value
    if source.user_id:
        _profile = db.query(ChannelProfile).filter(
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
    db.add(job)
    db.flush()

    db.commit()
    from gpcg.infrastructure.events import (
        publish_gameplay_status_changed,
        publish_job_created,
    )
    publish_gameplay_status_changed(
        source.user_id, source.id, source.processing_status, source.filename,
    )
    publish_job_created(user.id, job.id, job.type, job.priority)
    from gpcg.infrastructure.job_queue import enqueue_job
    enqueue_job(job)
    log.info(f"Created mapping job #{job.id} for gameplay #{source_id}")
    return {
        "job_id": job.id,
        "job_uuid": job.job_uuid,
        "processing_status": source.processing_status,
    }


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
