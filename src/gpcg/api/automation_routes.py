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
    ContentPlan,
    Game,
    GameplaySource,
    IngestionStatus,
    Job,
    JobStatus,
    JobType,
    User,
    Video,
    VideoStatus,
)
from gpcg.infrastructure.auth import get_current_user
from gpcg.infrastructure.database import get_db, session_scope
from gpcg.infrastructure.google_integration_adapter import GoogleIntegrationAdapter
from gpcg.logging import get_logger
from gpcg.api.worker_routes import worker_auth

log = get_logger(__name__)

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
        # Auto-create using the same session (SQLite doesn't support concurrent writes)
        auto = Automation(user_id=user.id, name="Minha Automação", config={}, upload_config={})
        db.add(auto)
        db.commit()
        db.refresh(auto)
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


@router.post("/automation/start")
def start_automation(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Inicia a automação. A automação passa a produzir vídeos continuamente.

    Marca o status como 'running'. O worker verifica se há job em andamento;
    se não houver, cria um novo automaticamente. Quando o job termina,
    o worker cria o próximo, e assim por diante até o usuário pausar
    ou acabarem os gameplays.
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

    with session_scope() as session:
        a = session.get(Automation, auto.id)
        a.status = "running"
        session.flush()

    return {"status": "running"}


@router.post("/automation/pause")
def pause_automation(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Pausa a automação. O vídeo sendo gerado no momento continua até o fim,
    mas nenhum novo vídeo será iniciado até que o usuário retome.
    """
    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    if not auto:
        raise HTTPException(status_code=404, detail="Automação não encontrada")

    with session_scope() as session:
        a = session.get(Automation, auto.id)
        a.status = "paused"
        session.flush()

    return {"status": "paused"}


@router.post("/automation/check")
def check_automation(
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Check all running automations and create jobs if needed.

    Called by the remote worker on each poll cycle. For each automation
    with status='running', if there's no active job (queued/running) for
    that user, creates a new job from the automation config (which properly
    passes subtitle/transition/voice settings).
    """
    autos = db.query(Automation).filter(Automation.status == "running").all()

    created_job_id = None
    for auto in autos:
        # Check if there's already an active job for this user
        active = db.query(Job).filter(
            Job.user_id == auto.user_id,
            Job.status.in_([JobStatus.queued.value, JobStatus.running.value]),
            Job.type.in_([JobType.generate_short.value, JobType.curiosity_short.value]),
        ).first()

        if active:
            continue

        # No active job — create one from the automation config
        try:
            job_id = create_job_from_automation(auto.user_id)
            if job_id:
                created_job_id = job_id
                log.info(f"Automation check: created job #{job_id} for user {auto.user_id}")
        except Exception as e:
            log.warning(f"Automation check failed for user {auto.user_id}: {e}")

    return {"job_id": created_job_id}


def create_job_from_automation(user_id: int) -> int | None:
    """Cria um job a partir da configuração da automação do usuário.

    Chamada pelo worker quando:
    - automation.status == 'running'
    - não há job em andamento (queued/running) para o usuário

    O sistema decide autonomamente qual jogo e tema abordar, analisando:
    - quais jogos têm gameplays prontas
    - quais jogos têm conhecimento (facts, chunks)
    - quais temas já foram produzidos (para evitar repetição)

    Retorna o job_id ou None se não puder criar (sem gameplays, sem YouTube, etc).
    """
    with session_scope() as session:
        user = session.get(User, user_id)
        if not user or not user.is_active:
            return None

        auto = session.query(Automation).filter(Automation.user_id == user_id).first()
        if not auto or auto.status != "running":
            return None

        # Check YouTube connection
        if not user.google_user_id:
            return None

        # Check if there are gameplays available
        ready_count = session.query(GameplaySource).filter(
            GameplaySource.user_id == user_id,
            GameplaySource.ingestion_status == IngestionStatus.ready.value,
        ).count()
        if ready_count == 0:
            return None

        # Check if there's already a running/queued job
        active_jobs = session.query(Job).filter(
            Job.user_id == user_id,
            Job.status.in_([JobStatus.queued.value, JobStatus.running.value]),
        ).count()
        if active_jobs > 0:
            return None

    # ── Editorial decision: decide what to produce ──────────────────────
    # The system autonomously picks a game + topic based on available
    # gameplays, knowledge, and production history. The user does NOT
    # specify a topic — the AI acts as the channel's editor.
    from gpcg.application.editorial_strategy import EditorialStrategyService
    from gpcg.infrastructure.llm import get_llm

    editorial = EditorialStrategyService(llm=get_llm())
    with session_scope() as session:
        decision = editorial.decide_next_video(session, user_id)

    if not decision.success:
        log.info(f"automation: editorial decision failed: {decision.error}")
        return None

    # Create the job using the editorial decision
    from gpcg.application.generation_service import GenerationService

    with session_scope() as session:
        auto = session.query(Automation).filter(Automation.user_id == user_id).first()
        config = auto.config or {} if auto else {}

        service = GenerationService(llm=get_llm())

        # Common subtitle/transition/voice config from automation
        subtitle_kwargs = dict(
            scene_duration=config.get("scene_duration", 0),
            video_format=config.get("video_format", ""),
            subtitle_font=config.get("subtitle_font", ""),
            subtitle_font_size=config.get("subtitle_font_size", 0),
            subtitle_color=config.get("subtitle_color", ""),
            subtitle_outline_color=config.get("subtitle_outline_color", ""),
            subtitle_position=config.get("subtitle_position", ""),
            subtitle_case=config.get("subtitle_case", ""),
            subtitle_box_enabled=config.get("subtitle_box_enabled"),
            subtitle_box_color=config.get("subtitle_box_color", ""),
            subtitle_box_padding=config.get("subtitle_box_padding", 0),
            subtitle_stroke_color=config.get("subtitle_stroke_color", ""),
            subtitle_stroke_width=config.get("subtitle_stroke_width", 0),
            subtitle_rounded_box=config.get("subtitle_rounded_box"),
            transition_type=config.get("transition_type", ""),
            transition_duration=config.get("transition_duration", 0),
            creative_style=config.get("creative_style", ""),
        )
        # Voice: resolve filename to path
        voice_name = config.get("voice", "")
        voice_path = ""
        if voice_name:
            from gpcg.config import get_settings
            settings = get_settings()
            vp = settings.voices_dir / voice_name
            if vp.exists():
                voice_path = str(vp)
        subtitle_kwargs["voice_path"] = voice_path

        if decision.job_type == "generate_short" and decision.game_id:
            # Game-specific short: the editorial AI picked a game
            game = session.get(Game, decision.game_id)
            if not game:
                log.warning(f"automation: game {decision.game_id} not found")
                return None
            job = service.create_job(
                game.id,
                user_id=user_id,
                **subtitle_kwargs,
            )
            # Store editorial decision in artifacts for the content planner
            with session_scope() as s2:
                j = s2.get(Job, job.id)
                j.artifacts = {
                    **j.artifacts,
                    "editorial_decision": decision.to_dict(),
                    "fact_id": decision.fact_id,
                }
                s2.flush()
        elif decision.job_type == "curiosity_short" and decision.background_game_id:
            job = service.create_curiosity_job(
                background_game_id=decision.background_game_id,
                fact_id=decision.fact_id,
                user_id=user_id,
                **subtitle_kwargs,
            )
        else:
            log.warning(f"automation: invalid editorial decision: {decision.to_dict()}")
            return None

        # Update last_run_at
        from datetime import datetime, timezone
        auto.last_run_at = datetime.now(timezone.utc)
        session.flush()

        return job.id


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

    # Build recent video list with social_title from job artifacts
    recent_list = []
    for v in recent_videos:
        social_title = None
        if v.job_id:
            job = db.get(Job, v.job_id)
            if job and isinstance(job.artifacts, dict):
                social_title = job.artifacts.get("social_title")
        cp = db.get(ContentPlan, v.content_plan_id) if v.content_plan_id else None
        recent_list.append({
            "id": v.id,
            "status": v.status,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "qa_score": v.qa_score,
            "qa_passed": v.qa_report.get("passed", False) if v.qa_report else False,
            "duration": v.duration,
            "thumbnail_path": v.thumbnail_path,
            "topic": cp.topic if cp else None,
            "social_title": social_title,
            "youtube_url": v.youtube_url,
            "youtube_video_id": v.youtube_video_id,
        })

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
        "recent_videos": recent_list,
    }
