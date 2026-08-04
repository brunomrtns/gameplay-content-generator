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
    """Check all running automations and return which ones need a job.

    Called by the remote worker on each poll cycle. For each automation
    with status='running', if there's no active job (queued/running) for
    that user, returns the automation info so the remote worker can make
    the editorial decision locally (where the LLM/Ollama is available)
    and create the job via the API.

    V2: The editorial decision (which game, which topic) is now made by
    the remote worker, NOT on the VPS, because the LLM (Ollama) runs on
    the local PC. The VPS only checks if a job should be created.
    """
    autos = db.query(Automation).filter(Automation.status == "running").all()

    pending = []
    for auto in autos:
        # Check if there's already an active job for this user
        active = db.query(Job).filter(
            Job.user_id == auto.user_id,
            Job.status.in_([JobStatus.queued.value, JobStatus.running.value]),
            Job.type.in_([JobType.generate_short.value, JobType.curiosity_short.value]),
        ).first()

        if active:
            continue

        # Check if user has YouTube connected
        user = db.get(User, auto.user_id)
        if not user or not user.google_user_id:
            continue

        # Check if there are gameplays available
        ready_count = db.query(GameplaySource).filter(
            GameplaySource.user_id == auto.user_id,
            GameplaySource.ingestion_status == IngestionStatus.ready.value,
        ).count()
        if ready_count == 0:
            continue

        pending.append({
            "user_id": auto.user_id,
            "automation_id": auto.id,
            "config": auto.config or {},
        })

    return {"pending": pending}


@router.get("/automation/editorial-data/{user_id}")
def get_editorial_data(
    user_id: int,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Return all data needed for the editorial decision.

    Called by the remote worker after /automation/check reports a pending
    automation. The remote worker uses this data to run the
    EditorialStrategyService locally (with LLM) and then creates the job
    via POST /api/automation/create-job.
    """
    from gpcg.domain.models import Fact, KnowledgeChunk, KnowledgeItem, KnowledgeItemStatus, ChannelProfile
    from sqlalchemy import select, func, desc
    from datetime import datetime, timezone, timedelta

    # 1. Build inventory: games with gameplay sources
    games_with_gameplay = db.execute(
        select(Game, func.count(GameplaySource.id), func.sum(GameplaySource.duration))
        .join(GameplaySource, GameplaySource.game_id == Game.id)
        .where(GameplaySource.user_id == user_id)
        .where(GameplaySource.ingestion_status == IngestionStatus.ready.value)
        .group_by(Game.id)
    ).all()

    games_with_facts = set(
        db.execute(
            select(Fact.game_id)
            .where(Fact.user_id == user_id)
            .where(Fact.game_id.isnot(None))
            .distinct()
        ).scalars().all()
    )
    games_with_chunks = set(
        db.execute(
            select(KnowledgeChunk.game_id)
            .where(KnowledgeChunk.user_id == user_id)
            .where(KnowledgeChunk.game_id.isnot(None))
            .distinct()
        ).scalars().all()
    )

    # V2: Also include games with KnowledgeItems
    from gpcg.domain.visibility import visible_to_user
    ki_vis_all = visible_to_user(KnowledgeItem.user_id, KnowledgeItem.is_public, user_id)
    games_with_kis = set(
        db.execute(
            select(KnowledgeItem.game_id)
            .where(KnowledgeItem.status == KnowledgeItemStatus.fresh.value)
            .where(KnowledgeItem.game_id.isnot(None))
            .where(ki_vis_all)
            .distinct()
        ).scalars().all()
    )

    all_game_ids = {g.id for g, _, _ in games_with_gameplay} | games_with_facts | games_with_chunks | games_with_kis
    inventory = []
    for game_id in all_game_ids:
        game = db.get(Game, game_id)
        if not game:
            continue
        inv = {
            "game_id": game.id,
            "game_name": game.canonical_name,
            "franchise": game.franchise,
            "developer": game.developer,
            "lore_summary": game.lore_summary or "",
        }
        for g, count, total_dur in games_with_gameplay:
            if g.id == game.id:
                inv["gameplay_sources_ready"] = count
                inv["total_gameplay_duration"] = total_dur or 0.0
                break
        else:
            inv["gameplay_sources_ready"] = 0
            inv["total_gameplay_duration"] = 0.0

        inv["gameplay_sources_total"] = db.execute(
            select(func.count(GameplaySource.id))
            .where(GameplaySource.user_id == user_id)
            .where(GameplaySource.game_id == game.id)
        ).scalar() or 0

        inv["facts_available"] = db.execute(
            select(func.count(Fact.id))
            .where(Fact.user_id == user_id)
            .where(Fact.game_id == game.id)
        ).scalar() or 0

        inv["facts_unused"] = db.execute(
            select(func.count(Fact.id))
            .where(Fact.user_id == user_id)
            .where(Fact.game_id == game.id)
            .where(Fact.used_count == 0)
        ).scalar() or 0

        inv["knowledge_chunks"] = db.execute(
            select(func.count(KnowledgeChunk.id))
            .where(KnowledgeChunk.user_id == user_id)
            .where(KnowledgeChunk.game_id == game.id)
        ).scalar() or 0

        # V2: KnowledgeItems count for this game
        from gpcg.domain.visibility import visible_to_user
        ki_vis = visible_to_user(KnowledgeItem.user_id, KnowledgeItem.is_public, user_id)
        inv["knowledge_items"] = db.execute(
            select(func.count(KnowledgeItem.id))
            .where(
                KnowledgeItem.status == KnowledgeItemStatus.fresh.value,
                KnowledgeItem.game_id == game.id,
                ki_vis,
            )
        ).scalar() or 0

        # V2: Include KnowledgeItem summaries for this game
        game_kis = db.execute(
            select(KnowledgeItem.id, KnowledgeItem.title, KnowledgeItem.editorial_score)
            .where(
                KnowledgeItem.status == KnowledgeItemStatus.fresh.value,
                KnowledgeItem.game_id == game.id,
                ki_vis,
            )
            .order_by(KnowledgeItem.editorial_score.desc())
            .limit(5)
        ).all()
        inv["knowledge_items_list"] = [
            {"id": ki.id, "title": ki.title, "editorial_score": ki.editorial_score}
            for ki in game_kis
        ]

        inv["videos_produced"] = db.execute(
            select(func.count(ContentPlan.id))
            .where(ContentPlan.user_id == user_id)
            .where(ContentPlan.game_id == game.id)
        ).scalar() or 0

        recent_plans = db.execute(
            select(ContentPlan.topic)
            .where(ContentPlan.user_id == user_id)
            .where(ContentPlan.game_id == game.id)
            .order_by(desc(ContentPlan.created_at))
            .limit(5)
        ).scalars().all()
        inv["recent_topics"] = [t for t in recent_plans if t]

        # Include fact summaries for the LLM
        facts = db.execute(
            select(Fact.id, Fact.claim, Fact.used_count, Fact.quality_score)
            .where(Fact.user_id == user_id)
            .where(Fact.game_id == game.id)
            .order_by(Fact.used_count.asc(), Fact.quality_score.desc())
            .limit(10)
        ).all()
        inv["facts"] = [
            {"id": f.id, "claim": f.claim, "used_count": f.used_count, "quality_score": f.quality_score}
            for f in facts
        ]

        inventory.append(inv)

    # 2. Recent editorial history
    recent_plans = db.execute(
        select(ContentPlan)
        .where(ContentPlan.user_id == user_id)
        .order_by(desc(ContentPlan.created_at))
        .limit(10)
    ).scalars().all()
    history = {
        "recent_topics": [p.topic for p in recent_plans if p.topic],
        "recent_game_ids": [p.game_id for p in recent_plans if p.game_id],
        "recent_fact_ids": [p.fact_id for p in recent_plans if p.fact_id],
        "total_videos": len(recent_plans),
    }

    # 3. Channel profile
    channel_context = ""
    try:
        profile = db.query(ChannelProfile).filter(
            ChannelProfile.user_id == user_id
        ).first()
        if profile:
            channel_context = profile.to_prompt_context()
    except Exception:
        pass

    # 4. V2: General KnowledgeItems (content ideas without game_id)
    # These can be used for curiosity_short videos with any gameplay as background.
    from gpcg.domain.visibility import visible_to_user
    ki_vis = visible_to_user(KnowledgeItem.user_id, KnowledgeItem.is_public, user_id)
    general_kis = db.execute(
        select(KnowledgeItem)
        .where(KnowledgeItem.status == KnowledgeItemStatus.fresh.value)
        .where(KnowledgeItem.editorial_score >= 20)
        .where(ki_vis)
        .order_by(KnowledgeItem.editorial_score.desc())
        .limit(20)
    ).scalars().all()
    general_ideas = [
        {
            "id": ki.id,
            "title": ki.title,
            "summary": (ki.content or "")[:300],
            "item_type": ki.item_type,
            "editorial_score": ki.editorial_score,
            "game_id": ki.game_id,
            "franchise": ki.franchise,
            "developer": ki.developer,
        }
        for ki in general_kis
    ]

    return {
        "inventory": inventory,
        "history": history,
        "channel_context": channel_context,
        "general_ideas": general_ideas,
    }


class CreateJobRequest(BaseModel):
    """Request to create a job from an editorial decision."""
    user_id: int
    game_id: Optional[int] = None
    fact_id: Optional[int] = None
    job_type: str = "generate_short"
    background_game_id: Optional[int] = None
    topic_hint: str = ""
    reason: str = ""


@router.post("/automation/create-job")
def create_job_from_decision(
    req: CreateJobRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Create a job from an editorial decision made by the remote worker.

    The remote worker runs the EditorialStrategyService locally (with LLM)
    and sends the decision here. The VPS creates the job with the automation
    config (subtitle/transition/voice settings).
    """
    auto = db.query(Automation).filter(Automation.user_id == req.user_id).first()
    if not auto:
        raise HTTPException(status_code=404, detail="Automation not found")
    if auto.status != "running":
        raise HTTPException(status_code=400, detail="Automation is not running")

    config = auto.config or {}

    from gpcg.application.generation_service import GenerationService
    from gpcg.config import get_settings

    service = GenerationService()
    settings = get_settings()

    # REFACTORY_V2: use settings defaults instead of hardcoded 0/"" so that
    # jobs created from automation decisions respect the same defaults as
    # the main POST /api/jobs/generate endpoint.
    # NOTE: use `or` instead of dict.get(key, default) because None values
    # in the config dict would bypass the default.
    subtitle_kwargs = dict(
        scene_duration=config.get("scene_duration") or settings.gpcg_scene_duration,
        video_format=config.get("video_format") or settings.gpcg_video_format,
        subtitle_font=config.get("subtitle_font") or settings.gpcg_subtitle_font,
        subtitle_font_size=config.get("subtitle_font_size") or settings.gpcg_subtitle_font_size,
        subtitle_color=config.get("subtitle_color") or settings.gpcg_subtitle_color,
        subtitle_outline_color=config.get("subtitle_outline_color") or settings.gpcg_subtitle_outline_color,
        subtitle_position=config.get("subtitle_position") or settings.gpcg_subtitle_position,
        subtitle_case=config.get("subtitle_case") or settings.gpcg_subtitle_case,
        subtitle_box_enabled=config.get("subtitle_box_enabled"),
        subtitle_box_color=config.get("subtitle_box_color") or "",
        subtitle_box_padding=config.get("subtitle_box_padding") or 0,
        subtitle_stroke_color=config.get("subtitle_stroke_color") or "",
        subtitle_stroke_width=config.get("subtitle_stroke_width") or 0,
        subtitle_rounded_box=config.get("subtitle_rounded_box"),
        transition_type=config.get("transition_type") or settings.gpcg_transition_type,
        transition_duration=config.get("transition_duration") or settings.gpcg_transition_duration,
        creative_style=config.get("creative_style") or settings.gpcg_creative_engine_style,
    )
    voice_name = config.get("voice", "")
    voice_path = ""
    if voice_name:
        # REFACTORY_V2: look in user's isolated directory first, then shared root
        user_voice = settings.voices_dir / f"user_{req.user_id}" / voice_name
        shared_voice = settings.voices_dir / voice_name
        if user_voice.exists():
            voice_path = str(user_voice)
        elif shared_voice.exists():
            voice_path = str(shared_voice)
    subtitle_kwargs["voice_path"] = voice_path

    if req.job_type == "generate_short" and req.game_id:
        game = db.get(Game, req.game_id)
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")
        job = service.create_job(
            req.game_id,
            user_id=req.user_id,
            **subtitle_kwargs,
        )
        # Store editorial decision in artifacts
        with session_scope() as s2:
            j = s2.get(Job, job.id)
            j.artifacts = {
                **j.artifacts,
                "editorial_decision": {
                    "job_type": req.job_type,
                    "game_id": req.game_id,
                    "fact_id": req.fact_id,
                    "topic_hint": req.topic_hint,
                    "reason": req.reason,
                },
                "fact_id": req.fact_id,
            }
            s2.flush()
    elif req.job_type == "curiosity_short" and req.background_game_id:
        job = service.create_curiosity_job(
            background_game_id=req.background_game_id,
            fact_id=req.fact_id,
            user_id=req.user_id,
            **subtitle_kwargs,
        )
        # Store editorial decision in artifacts
        with session_scope() as s2:
            j = s2.get(Job, job.id)
            j.artifacts = {
                **j.artifacts,
                "editorial_decision": {
                    "job_type": req.job_type,
                    "background_game_id": req.background_game_id,
                    "fact_id": req.fact_id,
                    "topic_hint": req.topic_hint,
                    "reason": req.reason,
                },
                "fact_id": req.fact_id,
            }
            s2.flush()
    else:
        raise HTTPException(status_code=400, detail="Invalid editorial decision")

    # Update last_run_at
    from datetime import datetime, timezone
    auto.last_run_at = datetime.now(timezone.utc)
    db.flush()

    log.info(f"Automation created job #{job.id} for user {req.user_id} (editorial decision from remote worker)")
    return {"job_id": job.id, "job_uuid": job.job_uuid}


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

        # ── Idea queue: user-curated queue takes priority ───────────────
        # If the user has queued KnowledgeItems, consume the first one (FIFO)
        # instead of running the autonomous editorial decision.
        auto = session.query(Automation).filter(Automation.user_id == user_id).first()
        config = auto.config or {} if auto else {}
        idea_queue: list[int] = config.get("idea_queue", [])

    if idea_queue:
        # Consume the first idea in the queue
        ki_id = idea_queue[0]
        from gpcg.application.generation_service import GenerationService
        from gpcg.domain.models import KnowledgeItem, KnowledgeItemStatus
        from gpcg.infrastructure.llm import get_llm

        with session_scope() as session:
            ki = session.get(KnowledgeItem, ki_id)
            if ki is None or ki.status != KnowledgeItemStatus.fresh.value:
                # KI no longer available — remove from queue and skip
                log.info(f"automation: queued KI #{ki_id} no longer fresh, removing from queue")
                auto = session.query(Automation).filter(Automation.user_id == user_id).first()
                cfg = auto.config or {}
                q = cfg.get("idea_queue", [])
                if ki_id in q:
                    q.remove(ki_id)
                    cfg["idea_queue"] = q
                    auto.config = cfg
                    session.commit()
                return None

            # Pick a background game (least recently used with gameplay ready)
            bg_game = session.query(Game).join(
                GameplaySource, GameplaySource.game_id == Game.id
            ).filter(
                GameplaySource.user_id == user_id,
                GameplaySource.ingestion_status == IngestionStatus.ready.value,
            ).order_by(Game.id).first()

            if not bg_game:
                log.warning("automation: idea queue has items but no gameplay available")
                return None

            # Build subtitle kwargs from automation config
            auto = session.query(Automation).filter(Automation.user_id == user_id).first()
            cfg = auto.config or {}
            subtitle_kwargs = dict(
                scene_duration=cfg.get("scene_duration") or 0,
                video_format=cfg.get("video_format") or "",
                subtitle_font=cfg.get("subtitle_font") or "",
                subtitle_font_size=cfg.get("subtitle_font_size") or 0,
                subtitle_color=cfg.get("subtitle_color") or "",
                subtitle_outline_color=cfg.get("subtitle_outline_color") or "",
                subtitle_position=cfg.get("subtitle_position") or "",
                subtitle_case=cfg.get("subtitle_case") or "",
                subtitle_box_enabled=cfg.get("subtitle_box_enabled"),
                subtitle_box_color=cfg.get("subtitle_box_color") or "",
                subtitle_box_padding=cfg.get("subtitle_box_padding") or 0,
                subtitle_stroke_color=cfg.get("subtitle_stroke_color") or "",
                subtitle_stroke_width=cfg.get("subtitle_stroke_width") or 0,
                subtitle_rounded_box=cfg.get("subtitle_rounded_box"),
                transition_type=cfg.get("transition_type") or "",
                transition_duration=cfg.get("transition_duration") or 0,
                creative_style=cfg.get("creative_style") or "",
            )
            voice_name = cfg.get("voice", "")
            voice_path = ""
            if voice_name:
                from gpcg.config import get_settings
                settings = get_settings()
                vp = settings.voices_dir / voice_name
                if vp.exists():
                    voice_path = str(vp)
            subtitle_kwargs["voice_path"] = voice_path

            service = GenerationService(llm=get_llm())

            # If KI has a game_id, create a game-specific short;
            # otherwise, create a curiosity_short with background gameplay.
            if ki.game_id:
                game = session.get(Game, ki.game_id)
                if game:
                    job = service.create_job(
                        game.id,
                        user_id=user_id,
                        **subtitle_kwargs,
                    )
                    # Store the KI reference for the content planner
                    with session_scope() as s2:
                        j = s2.get(Job, job.id)
                        j.artifacts = {
                            **j.artifacts,
                            "queued_knowledge_item_id": ki_id,
                            "idea_source": "user_queue",
                        }
                        s2.flush()
                    log.info(f"automation: created generate_short job #{job.id} from queued KI #{ki_id} (game={game.canonical_name})")
                else:
                    log.warning(f"automation: queued KI #{ki_id} references missing game {ki.game_id}")
                    # Remove from queue
                    q = cfg.get("idea_queue", [])
                    if ki_id in q:
                        q.remove(ki_id)
                        cfg["idea_queue"] = q
                        auto.config = cfg
                        session.commit()
                    return None
            else:
                # General idea → curiosity_short with background gameplay
                job = service.create_curiosity_job(
                    background_game_id=bg_game.id,
                    fact_id=None,
                    user_id=user_id,
                    **subtitle_kwargs,
                )
                with session_scope() as s2:
                    j = s2.get(Job, job.id)
                    j.artifacts = {
                        **j.artifacts,
                        "queued_knowledge_item_id": ki_id,
                        "idea_source": "user_queue",
                    }
                    s2.flush()
                log.info(f"automation: created curiosity_short job #{job.id} from queued KI #{ki_id} (bg={bg_game.canonical_name})")

            # Remove the consumed KI from the queue
            with session_scope() as session2:
                auto2 = session2.query(Automation).filter(Automation.user_id == user_id).first()
                cfg2 = auto2.config or {}
                q2 = cfg2.get("idea_queue", [])
                if ki_id in q2:
                    q2.remove(ki_id)
                    cfg2["idea_queue"] = q2
                    auto2.config = cfg2
                    session2.commit()
                log.info(f"automation: removed KI #{ki_id} from idea queue (remaining: {len(q2)})")

            # Update last_run_at
            from datetime import datetime, timezone
            with session_scope() as session3:
                auto3 = session3.query(Automation).filter(Automation.user_id == user_id).first()
                auto3.last_run_at = datetime.now(timezone.utc)
                session3.flush()

            return job.id

    # ── Editorial decision: decide what to produce ──────────────────────
    # No items in the idea queue — the system autonomously picks a game
    # + topic based on available gameplays, knowledge, and production history.
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
        # NOTE: use `or` instead of dict.get(key, default) because None values
        # in the config dict would bypass the default (get returns None, not
        # the default). `or` treats None/0/"" as falsy → falls back to default.
        subtitle_kwargs = dict(
            scene_duration=config.get("scene_duration") or 0,
            video_format=config.get("video_format") or "",
            subtitle_font=config.get("subtitle_font") or "",
            subtitle_font_size=config.get("subtitle_font_size") or 0,
            subtitle_color=config.get("subtitle_color") or "",
            subtitle_outline_color=config.get("subtitle_outline_color") or "",
            subtitle_position=config.get("subtitle_position") or "",
            subtitle_case=config.get("subtitle_case") or "",
            subtitle_box_enabled=config.get("subtitle_box_enabled"),
            subtitle_box_color=config.get("subtitle_box_color") or "",
            subtitle_box_padding=config.get("subtitle_box_padding") or 0,
            subtitle_stroke_color=config.get("subtitle_stroke_color") or "",
            subtitle_stroke_width=config.get("subtitle_stroke_width") or 0,
            subtitle_rounded_box=config.get("subtitle_rounded_box"),
            transition_type=config.get("transition_type") or "",
            transition_duration=config.get("transition_duration") or 0,
            creative_style=config.get("creative_style") or "",
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
