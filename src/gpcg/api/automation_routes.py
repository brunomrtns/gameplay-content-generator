"""Automation and YouTube OAuth API routes.

Endpoints:
  GET    /api/automation              — get current user's automation
  PUT    /api/automation              — update automation config
  POST   /api/automation/run          — trigger a manual run
  GET    /api/youtube/connect          — get OAuth URL to connect channel
  GET    /api/youtube/status           — check YouTube connection status
  POST   /api/youtube/disconnect       — revoke YouTube access
  GET    /api/dashboard                — dashboard stats for current user
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from gpcg.domain.models import (
    Automation,
    GameplaySource,
    IngestionStatus,
    Job,
    JobStatus,
    User,
    Video,
    VideoStatus,
)
from gpcg.infrastructure.auth import get_current_user
from gpcg.infrastructure.database import get_db, session_scope
from gpcg.infrastructure.google_integration_adapter import GoogleIntegrationAdapter

router = APIRouter(tags=["automation"])


# ── Automation ───────────────────────────────────────────────────────────────


@router.get("/automation")
def get_automation(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the current user's automation config."""
    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    if not auto:
        # Auto-create
        with session_scope() as session:
            auto = Automation(user_id=user.id, name="Minha Automação", config={}, upload_config={})
            session.add(auto)
            session.flush()
            session.refresh(auto)
    return {
        "id": auto.id,
        "name": auto.name,
        "status": auto.status,
        "schedule": auto.schedule,
        "config": auto.config or {},
        "upload_config": auto.upload_config or {},
        "last_run_at": auto.last_run_at.isoformat() if auto.last_run_at else None,
        "next_run_at": auto.next_run_at.isoformat() if auto.next_run_at else None,
        "created_at": auto.created_at.isoformat() if auto.created_at else None,
    }


class UpdateAutomationRequest(BaseModel):
    name: Optional[str] = None
    config: Optional[dict] = None
    upload_config: Optional[dict] = None
    schedule: Optional[str] = None


@router.put("/automation")
def update_automation(
    req: UpdateAutomationRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update the current user's automation config."""
    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    if not auto:
        raise HTTPException(status_code=404, detail="Automação não encontrada")

    with session_scope() as session:
        a = session.get(Automation, auto.id)
        if req.name is not None:
            a.name = req.name
        if req.config is not None:
            a.config = req.config
        if req.upload_config is not None:
            a.upload_config = req.upload_config
        if req.schedule is not None:
            a.schedule = req.schedule
        session.flush()
        session.refresh(a)
        return {
            "id": a.id,
            "name": a.name,
            "status": a.status,
            "schedule": a.schedule,
            "config": a.config or {},
            "upload_config": a.upload_config or {},
        }


@router.post("/automation/run")
def trigger_run(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Trigger a manual run of the automation.

    This creates a job using the automation's config. The worker picks it up.
    """
    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    if not auto:
        raise HTTPException(status_code=404, detail="Automação não encontrada")

    # Check YouTube connection
    if not user.google_user_id:
        raise HTTPException(status_code=400, detail="Conecte seu canal do YouTube primeiro")

    # Check if there are gameplays available
    sources = db.query(GameplaySource).filter(
        GameplaySource.user_id == user.id,
        GameplaySource.ingestion_status == IngestionStatus.ready.value,
    ).count()
    if sources == 0:
        raise HTTPException(status_code=400, detail="Envie gameplays primeiro")

    # Create job via generation service
    from gpcg.application.generation_service import GenerationService
    from gpcg.infrastructure.llm import get_llm

    service = GenerationService(llm=get_llm())
    config = auto.config or {}

    job_id = service.create_curiosity_job(
        user_id=user.id,
        background_game_id=config.get("background_game_id"),
        target_duration=config.get("target_duration", 60),
        scene_duration=config.get("scene_duration"),
        video_format=config.get("video_format"),
        subtitle_font=config.get("subtitle_font"),
        subtitle_font_size=config.get("subtitle_font_size"),
        subtitle_color=config.get("subtitle_color"),
        subtitle_outline_color=config.get("subtitle_outline_color"),
        subtitle_position=config.get("subtitle_position"),
        subtitle_case=config.get("subtitle_case"),
        subtitle_box_enabled=config.get("subtitle_box_enabled"),
        subtitle_box_color=config.get("subtitle_box_color"),
        subtitle_box_padding=config.get("subtitle_box_padding"),
        subtitle_stroke_color=config.get("subtitle_stroke_color"),
        subtitle_stroke_width=config.get("subtitle_stroke_width"),
        subtitle_rounded_box=config.get("subtitle_rounded_box"),
        transition_type=config.get("transition_type"),
        transition_duration=config.get("transition_duration"),
        voice=config.get("voice"),
        creative_style=config.get("creative_style"),
    )

    # Update automation last_run_at
    with session_scope() as session:
        a = session.get(Automation, auto.id)
        from datetime import datetime, timezone
        a.last_run_at = datetime.now(timezone.utc)
        a.status = "running"
        session.flush()

    return {"job_id": job_id, "status": "running"}


# ── YouTube OAuth ─────────────────────────────────────────────────────────────


@router.get("/youtube/connect")
def youtube_connect(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the OAuth URL for the user to connect their YouTube channel.

    The frontend should redirect the user's browser to this URL.
    """
    # Assign a google_user_id if not already set
    # We use the GPCG user ID as the google-integration user ID
    if not user.google_user_id:
        with session_scope() as session:
            u = session.get(User, user.id)
            u.google_user_id = user.id
            session.flush()

    adapter = GoogleIntegrationAdapter()
    url = adapter.get_oauth_url(user.id)
    if not url:
        raise HTTPException(status_code=502, detail="Não foi possível obter URL do Google")
    return {"url": url}


@router.get("/youtube/status")
def youtube_status(
    user: User = Depends(get_current_user),
):
    """Check if the user's YouTube channel is connected."""
    if not user.google_user_id:
        return {"connected": False, "channel_title": None}

    adapter = GoogleIntegrationAdapter()
    status = adapter.get_auth_status(user.google_user_id)
    return {
        "connected": status.get("connected", False),
        "channel_title": status.get("channelTitle"),
        "channel_id": status.get("channelId"),
        "email": status.get("email"),
        "picture": status.get("picture"),
    }


@router.post("/youtube/disconnect")
def youtube_disconnect(
    user: User = Depends(get_current_user),
):
    """Revoke YouTube access for the current user."""
    if not user.google_user_id:
        return {"success": True, "message": "Already disconnected"}

    adapter = GoogleIntegrationAdapter()
    adapter.revoke_auth(user.google_user_id)

    with session_scope() as session:
        u = session.get(User, user.id)
        u.google_user_id = None
        session.flush()

    return {"success": True}


# ── Dashboard ─────────────────────────────────────────────────────────────────


@router.get("/dashboard")
def dashboard(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get dashboard stats for the current user."""
    # YouTube connection
    yt_connected = False
    yt_channel = None
    if user.google_user_id:
        adapter = GoogleIntegrationAdapter()
        status = adapter.get_auth_status(user.google_user_id)
        yt_connected = status.get("connected", False)
        yt_channel = status.get("channelTitle")

    # Gameplays
    total_gameplays = db.query(GameplaySource).filter(
        GameplaySource.user_id == user.id
    ).count()
    processing_gameplays = db.query(GameplaySource).filter(
        GameplaySource.user_id == user.id,
        GameplaySource.ingestion_status.in_([
            IngestionStatus.discovered.value,
            IngestionStatus.probing.value,
        ]),
    ).count()
    ready_gameplays = db.query(GameplaySource).filter(
        GameplaySource.user_id == user.id,
        GameplaySource.ingestion_status == IngestionStatus.ready.value,
    ).count()

    # Jobs
    total_jobs = db.query(Job).filter(Job.user_id == user.id).count()
    running_jobs = db.query(Job).filter(
        Job.user_id == user.id,
        Job.status.in_([JobStatus.queued.value, JobStatus.running.value]),
    ).count()

    # Videos
    total_videos = db.query(Video).filter(Video.user_id == user.id).count()
    published_videos = db.query(Video).filter(
        Video.user_id == user.id,
        Video.status == VideoStatus.published.value,
    ).count()

    # Recent videos
    recent_videos = db.query(Video).filter(
        Video.user_id == user.id
    ).order_by(Video.created_at.desc()).limit(5).all()

    # Automation
    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    auto_status = auto.status if auto else "idle"

    return {
        "youtube_connected": yt_connected,
        "youtube_channel": yt_channel,
        "gameplays": {
            "total": total_gameplays,
            "processing": processing_gameplays,
            "ready": ready_gameplays,
        },
        "jobs": {
            "total": total_jobs,
            "running": running_jobs,
        },
        "videos": {
            "total": total_videos,
            "published": published_videos,
        },
        "automation_status": auto_status,
        "recent_videos": [
            {
                "id": v.id,
                "status": v.status,
                "created_at": v.created_at.isoformat() if v.created_at else None,
                "qa_score": v.qa_score,
            }
            for v in recent_videos
        ],
    }
