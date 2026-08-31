"""Automation and YouTube OAuth API routes.

Endpoints:
  GET    /api/automation              — get current user's automation
  PUT    /api/automation              — update automation config
  POST   /api/automation/run          — trigger a manual run
  GET    /api/youtube/connect          — get OAuth URL to connect channel
  GET    /api/youtube/status           — check YouTube connection status
  POST   /api/youtube/disconnect       — revoke YouTube access
  GET    /api/dashboard                — dashboard stats for current user
  GET    /api/health/problems          — detect inventory problems for current user
  POST   /api/idea-queue/cleanup       — clean invalid KIs from idea queue
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from pathlib import Path

from gpcg.core.models import (
    Automation,
    ChannelProfile,
    ContentPlan,
    Job,
    JobPriority,
    JobStatus,
    JobType,
    KnowledgeItem,
    KnowledgeItemStatus,
    User,
    Video,
    VideoStatus,
)
from gpcg.domains.games.models import Game, GameplaySource, IngestionStatus
from gpcg.config import get_settings
from gpcg.infrastructure.auth import get_current_user
from gpcg.infrastructure.database import get_db, session_scope
from gpcg.infrastructure.google_integration_adapter import GoogleIntegrationAdapter
from gpcg.domain.visibility import gameplay_visible_to_user, user_allows_public_gameplays
from gpcg.logging import get_logger
from gpcg.api.worker_routes import worker_auth

log = get_logger(__name__)

router = APIRouter(tags=["automation"])


def _normalize_queue_entry(entry) -> dict:
    """Normalize a queue entry to dict format (backward compat: int → dict)."""
    if isinstance(entry, dict):
        return {
            "ki_id": entry.get("ki_id") or entry.get("id"),
            "gameplay_preference": entry.get("gameplay_preference"),
            "reuse_override": entry.get("reuse_override"),
            "gameplay_source_id": entry.get("gameplay_source_id"),
        }
    if isinstance(entry, int):
        return {"ki_id": entry, "gameplay_preference": None, "reuse_override": None, "gameplay_source_id": None}
    return {"ki_id": None, "gameplay_preference": None, "reuse_override": None, "gameplay_source_id": None}


def _normalize_idea_queue(raw) -> list[dict]:
    """Normalize the idea_queue config value to list[dict]."""
    if not raw:
        return []
    return [_normalize_queue_entry(e) for e in raw]


# V3: Fields included in the config snapshot stored in job.artifacts.
# Only generation-relevant fields — never secrets, tokens, idea_queue, etc.
_CONFIG_SNAPSHOT_FIELDS = (
    "scene_duration", "video_format",
    "subtitle_font", "subtitle_font_size", "subtitle_color",
    "subtitle_outline_color", "subtitle_position", "subtitle_case",
    "subtitle_box_enabled", "subtitle_box_color", "subtitle_box_padding",
    "subtitle_stroke_color", "subtitle_stroke_width", "subtitle_rounded_box",
    "transition_type", "transition_duration",
    "creative_style", "voice",
    "max_clip_uses", "fallback_policy",
    "presentation",  # Presentation Layer config (thumbnail + opening)
    "language",  # Multilingual: content language (frozen per job for deterministic retry)
)


def _build_config_snapshot(config: dict) -> dict:
    """Build a deterministic config snapshot for a job.

    Only includes fields needed for generation. Excludes secrets, tokens,
    idea_queue (mutable), and other non-generation fields. This ensures
    retry uses the same intent even if the user changes config later.

    Skips None values so that downstream .get(key, default) calls work
    correctly (otherwise None in the snapshot shadows the default).
    """
    return {k: v for k, v in ((k, config.get(k)) for k in _CONFIG_SNAPSHOT_FIELDS) if v is not None}


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


@router.get("/automation/current-job")
def get_current_job(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the currently running job for the user, including which idea (KI)
    is being processed.

    Returns null if no job is running. Used by the ideas page to show
    "currently processing" above the queue.
    """
    from gpcg.core.models import Job, JobStatus, KnowledgeItem
    from gpcg.domains.games.models import Game
    from sqlalchemy import select

    job = db.execute(
        select(Job).where(
            Job.user_id == user.id,
            Job.status == JobStatus.running.value,
        ).order_by(Job.created_at.desc()).limit(1)
    ).scalars().first()

    if not job:
        return {"job": None}

    artifacts = job.artifacts or {}
    ki_id = artifacts.get("queued_knowledge_item_id")
    ki_title = None
    ki_item_type = None
    if ki_id:
        ki = db.get(KnowledgeItem, ki_id)
        if ki:
            ki_title = ki.title
            ki_item_type = ki.item_type

    # If no KI title (job created via editorial decision, not queue),
    # fall back to editorial decision info or content plan topic.
    if not ki_title:
        editorial = artifacts.get("editorial_decision", {})
        if editorial:
            ki_title = editorial.get("topic_hint") or editorial.get("reason") or None
            # Try to get the game name
            game_id = editorial.get("game_id") or editorial.get("background_game_id")
            if game_id:
                game = db.get(Game, game_id)
                if game:
                    ki_title = ki_title or f"Vídeo sobre {game.canonical_name}"
                    if not ki_item_type:
                        ki_item_type = "game_related"

    # Map stage to user-friendly Portuguese label
    STAGE_LABELS = {
        "content_planning": "Planejando conteúdo",
        "story_finding": "Encontrando narrativa",
        "editorial_planning": "Planejamento editorial",
        "creative_engine": "Motor criativo",
        "script": "Escrevendo roteiro",
        "humanization": "Humanizando texto",
        "script_review": "Revisando roteiro",
        "tts": "Sintetizando voz",
        "gameplay_selection": "Selecionando gameplay",
        "music_selection": "Escolhendo música",
        "render_plan": "Preparando renderização",
        "render": "Renderizando vídeo",
        "output": "Enviando vídeo",
        "done": "Finalizando",
        "content_collection": "Coletando conteúdo",
        "mapping": "Mapeando gameplay",
    }

    return {
        "job": {
            "id": job.id,
            "type": job.type,
            "stage": job.stage,
            "stage_label": STAGE_LABELS.get(job.stage or "", job.stage or ""),
            "progress": job.progress,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "ki_id": ki_id,
            "ki_title": ki_title,
            "ki_item_type": ki_item_type,
            "idea_source": artifacts.get("idea_source"),
        }
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

    # Check if there are gameplays available.
    # When the user has opted into the public fallback, public gameplays
    # from other users count as available.
    cfg = auto.config or {}
    allows_public = user_allows_public_gameplays(cfg)

    ready_count = db.query(GameplaySource).filter(
        gameplay_visible_to_user(
            GameplaySource.user_id, GameplaySource.is_public, user.id,
            allows_public=allows_public,
        ),
        GameplaySource.ingestion_status == IngestionStatus.ready.value,
        GameplaySource.enabled == True,
    ).count()

    if ready_count == 0:
        if allows_public:
            raise HTTPException(
                status_code=400,
                detail="Nenhuma gameplay pública disponível no momento. Envie suas próprias gameplays ou aguarde novas gameplays públicas.",
            )
        else:
            raise HTTPException(
                status_code=400,
                detail="Envie gameplays primeiro ou ative o uso de gameplays públicas em Automação → Conteúdo → Fallback.",
            )

    with session_scope() as session:
        a = session.get(Automation, auto.id)
        a.status = "running"
        session.flush()

    from gpcg.infrastructure.events import publish_automation_status_changed
    publish_automation_status_changed(user.id, auto.id, "running")
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

    from gpcg.infrastructure.events import publish_automation_status_changed
    publish_automation_status_changed(user.id, auto.id, "paused")
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

    V4: Domain-aware dispatch. Games users use the existing path (gameplays,
    KnowledgeItem queue). Kids users use the new Kids path (StoryAssets,
    KidsIdea queue).
    """
    from gpcg.domains.automation_strategies import get_user_domain, KidsAutomationStrategy

    autos = db.query(Automation).filter(Automation.status == "running").all()

    pending = []
    for auto in autos:
        # Domain dispatch: Kids users use the Kids strategy
        domain = get_user_domain(db, auto.user_id)
        if domain == "kids":
            kids_pending = KidsAutomationStrategy.check(auto, db)
            if kids_pending:
                pending.append(kids_pending)
            continue

        # ── Games path (existing behavior, unchanged) ──────────────────────
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

        # Check if there are gameplays available (own + public fallback).
        cfg = auto.config or {}
        allows_public = user_allows_public_gameplays(cfg)

        ready_count = db.query(GameplaySource).filter(
            gameplay_visible_to_user(
                GameplaySource.user_id, GameplaySource.is_public, auto.user_id,
                allows_public=allows_public,
            ),
            GameplaySource.ingestion_status == IngestionStatus.ready.value,
            GameplaySource.enabled == True,
        ).count()
        if ready_count == 0:
            continue

        # V3: Reconciliador — auto-fill queue with fresh KIs up to max_queue_size
        # Runs on VPS, independent of worker. Also triggered after content
        # collection and when user opens the ideas page.
        # CRITICAL: must commit so that create_job_from_automation (which opens
        # its own session_scope) can see the reconciled queue. Without commit,
        # the queue is only visible in this request's transaction and
        # create_job_from_automation sees an empty queue, falling through to
        # the editorial decision path instead of consuming the queue.
        reconcile_user_queue(db, auto.user_id)
        db.commit()
        # Re-read config after reconcile may have updated it
        db.refresh(auto)
        cfg = auto.config or {}
        queue_mode = cfg.get("queue_mode", "automatic")
        idea_queue = cfg.get("idea_queue", [])

        pending.append({
            "user_id": auto.user_id,
            "automation_id": auto.id,
            "config": cfg,
            "idea_queue": idea_queue,
            # V3: queue_mode = "manual" (only consume queue, no auto-editorial)
            #               | "automatic" (fall back to editorial when queue empty)
            # Default: "automatic" (backward compat)
            "queue_mode": queue_mode,
        })

    return {"pending": pending}


def _reconcile_idea_queue(
    db: Session,
    user_id: int,
    config: dict,
    *,
    exclude_ids: set[int] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """V3: Auto-fill the idea queue with fresh KnowledgeItems.

    Selects fresh KIs visible to the user, sorted by editorial_score descending.
    Excludes KIs already in the queue (exclude_ids) and respects the limit
    (defaults to max_queue_size from config).

    Editorial Intelligence V2: when gpcg_composite_scoring_enabled is True,
    KIs are ranked by the 3-layer composite score (Editorial Quality ×
    Production Fit × Editorial Timing) instead of editorial_score alone.
    This makes the queue personalized per channel.

    Returns the new queue entries (list[dict] with ki_id, gameplay_preference,
    reuse_override). Returns empty list if no fresh KIs are available.
    """
    from gpcg.core.models import (
    KnowledgeItem,
    KnowledgeItemStatus,
    Job,
    JobStatus,
)
    from gpcg.domain.visibility import visible_to_user

    max_size = limit if limit is not None else (config.get("max_queue_size") or 10)
    vis = visible_to_user(KnowledgeItem.user_id, KnowledgeItem.is_public, user_id)
    query = db.query(KnowledgeItem).filter(
        KnowledgeItem.status == KnowledgeItemStatus.fresh.value,
        vis,
    )
    if exclude_ids:
        query = query.filter(~KnowledgeItem.id.in_(exclude_ids))

    # V3: Also exclude KIs that have an active job (running/queued) for this
    # user. Without this, the reconciler re-adds a KI to the queue right after
    # it was consumed (job created, KI removed from queue) but before the job
    # completes and marks the KI as "used".
    active_ki_ids = set()
    active_jobs = db.query(Job).filter(
        Job.user_id == user_id,
        Job.status.in_([JobStatus.queued.value, JobStatus.running.value]),
    ).all()
    for aj in active_jobs:
        ki_id = (aj.artifacts or {}).get("queued_knowledge_item_id")
        if ki_id:
            active_ki_ids.add(ki_id)
    if active_ki_ids:
        query = query.filter(~KnowledgeItem.id.in_(active_ki_ids))

    # V2: Exclude archived KIs (lifecycle_stage = archived)
    query = query.filter(KnowledgeItem.lifecycle_stage != "archived")

    # ── V2: Composite scoring path ────────────────────────────────────────
    settings = get_settings()
    if settings.gpcg_composite_scoring_enabled:
        return _reconcile_with_composite_score(db, query, user_id, max_size)

    # Legacy: sort by editorial_score descending
    kis = query.order_by(KnowledgeItem.editorial_score.desc()).limit(max_size).all()

    if not kis:
        return []

    return [
        {
            "ki_id": ki.id,
            "gameplay_preference": ki.game_id,
            "reuse_override": None,
        }
        for ki in kis
    ]


def _reconcile_with_composite_score(
    db: Session,
    query,
    user_id: int,
    max_size: int,
) -> list[dict]:
    """V2: Rank KIs by composite score (3 layers) for this channel.

    Fetches a larger pool of candidates, scores each with CompositeScorer,
    and returns the top-N by final_score.
    """
    from gpcg.application.composite_scorer import CompositeScorer
    from gpcg.application.editorial_profile_service import get_or_create_profile
    from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
    from gpcg.application.editorial_brief_builder import EditorialBriefBuilder
    from gpcg.application.embedding_service import get_knowledge_item_embedding
    from gpcg.core.models import ChannelProfile, KnowledgeItemEmbedding

    # Load the user's automation config to determine public gameplay access
    _auto = db.query(Automation).filter(Automation.user_id == user_id).first()
    _accept_public = user_allows_public_gameplays(_auto.config if _auto else None)

    # Fetch a larger pool to score (we need more than max_size to rank properly)
    pool_size = max(max_size * 3, 20)
    candidates = query.limit(pool_size).all()

    if not candidates:
        return []

    # Build ki_id -> game_id lookup for setting gameplay_preference
    ki_game_map = {ki.id: ki.game_id for ki in candidates}

    # Build the brief for this channel (needed for scoring context)
    try:
        profile = get_or_create_profile(db, user_id)
        intent = EditorialIntentBuilder().build(db, user_id, profile, accept_public=_accept_public)
        brief = EditorialBriefBuilder().build(db, user_id, profile, intent)
    except Exception as e:
        log.warning(f"Composite scoring: failed to build brief for user {user_id}: {e}, falling back to editorial_score")
        # Fallback to legacy
        candidates.sort(key=lambda ki: ki.editorial_score, reverse=True)
        return [
            {"ki_id": ki.id, "gameplay_preference": ki.game_id, "reuse_override": None}
            for ki in candidates[:max_size]
        ]

    # Get channel embedding (if available)
    channel_embedding = None
    try:
        from gpcg.core.models import ChannelProfileEmbedding
        emb_row = db.query(ChannelProfileEmbedding).filter_by(user_id=user_id).first()
        if emb_row:
            from gpcg.application.embedding_service import deserialize_embedding
            channel_embedding = deserialize_embedding(emb_row.embedding)
    except Exception:
        pass  # channel embeddings not yet implemented — neutral affinity

    scorer = CompositeScorer()
    scored: list[tuple[float, int]] = []

    for ki in candidates:
        ki_embedding = None
        try:
            ki_embedding = get_knowledge_item_embedding(db, ki.id)
        except Exception:
            pass

        try:
            cs = scorer.score(ki, brief, db, user_id, channel_embedding, ki_embedding, accept_public=_accept_public)
            scored.append((cs.final_score, ki.id))
        except Exception as e:
            log.warning(f"Composite scoring failed for KI {ki.id}: {e}, using editorial_score")
            scored.append((ki.editorial_score / 100.0, ki.id))

    # Sort by composite score descending
    scored.sort(key=lambda x: x[0], reverse=True)

    # V2 Phase 3: Exploration factor — reserve a fraction of slots for random
    # KIs (outside the channel niche) to avoid filter bubbles.
    settings = get_settings()
    exploration_factor = settings.gpcg_editorial_exploration_factor
    exploration_slots = int(max_size * exploration_factor) if exploration_factor > 0 else 0
    top_slots = max_size - exploration_slots

    result_ids = [ki_id for _, ki_id in scored[:top_slots]]

    # Fill exploration slots with random KIs from the remaining pool
    if exploration_slots > 0 and len(scored) > top_slots:
        import random
        remaining = scored[top_slots:]
        # Sample min(exploration_slots, len(remaining)) random KIs
        sample_size = min(exploration_slots, len(remaining))
        sampled = random.sample(remaining, sample_size)
        result_ids.extend(ki_id for _, ki_id in sampled)

    return [
        {"ki_id": ki_id, "gameplay_preference": ki_game_map.get(ki_id), "reuse_override": None}
        for ki_id in result_ids
    ]


def reconcile_user_queue(db: Session, user_id: int) -> int:
    """V3: Reconcile a user's idea queue — auto-fill up to max_queue_size.

    This is the public entry point that can be called from anywhere:
    - check_automation (worker poll)
    - sync_knowledge_items (after new KIs arrive from collection)
    - GET /idea-queue (when user opens the ideas page)
    - POST /automation/reconcile-queue (manual trigger)

    Only runs when:
    - queue_mode == "automatic"
    - auto_fill_queue == True
    - queue length < max_queue_size

    Returns the number of new entries added (0 if nothing changed).
    """
    from gpcg.core.models import Automation
    from sqlalchemy.orm.attributes import flag_modified

    auto = db.query(Automation).filter(Automation.user_id == user_id).first()
    if not auto:
        return 0

    cfg = dict(auto.config or {})
    queue_mode = cfg.get("queue_mode", "automatic")
    if queue_mode != "automatic" or not cfg.get("auto_fill_queue", False):
        return 0

    idea_queue = _normalize_idea_queue(cfg.get("idea_queue", []))
    max_size = cfg.get("max_queue_size") or 10
    if len(idea_queue) >= max_size:
        return 0

    new_entries = _reconcile_idea_queue(
        db, user_id, cfg,
        exclude_ids={e.get("ki_id") for e in idea_queue},
        limit=max_size - len(idea_queue),
    )
    if not new_entries:
        return 0

    idea_queue = list(idea_queue) + new_entries
    cfg["idea_queue"] = idea_queue
    auto.config = cfg
    flag_modified(auto, "config")
    db.flush()
    log.info(
        f"Reconciliador: auto-filled queue for user {user_id} "
        f"with {len(new_entries)} KIs (now {len(idea_queue)}/{max_size})"
    )
    return len(new_entries)


def reconcile_all_users(db: Session) -> int:
    """V3: Reconcile queues for ALL users with auto_fill_queue enabled.

    Called after content collection syncs new KIs to the VPS.
    Returns the total number of entries added across all users.
    """
    from gpcg.core.models import Automation
    autos = db.query(Automation).all()
    total = 0
    for auto in autos:
        cfg = auto.config or {}
        if cfg.get("auto_fill_queue", False) and cfg.get("queue_mode", "automatic") == "automatic":
            total += reconcile_user_queue(db, auto.user_id)
    if total > 0:
        db.commit()
    return total


@router.post("/automation/consume-queue")
def consume_idea_queue(
    req: ConsumeQueueRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Create a job from the first KnowledgeItem in the user's idea queue.

    Called by the remote worker when /automation/check returns a non-empty
    idea_queue. This bypasses the editorial decision (LLM) and uses the
    user-curated idea directly.
    """
    job_id = create_job_from_automation(req.user_id)
    if job_id is None:
        raise HTTPException(status_code=409, detail="No job created (queue empty or active job exists)")
    return {"job_id": job_id, "source": "idea_queue"}


@router.post("/automation/trigger-content-collection")
def trigger_content_collection_worker(
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Worker-auth endpoint to trigger content collection automatically.

    Called by the remote worker's auto-collection scheduler every N hours.
    Creates a content_collect job for the first user with an active automation.
    Deduplicates: returns 409 if a content_collect job is already queued/running.
    """
    import uuid
    from sqlalchemy import select as _sel

    # Dedup: check for existing queued/running content_collect job
    existing = db.execute(
        _sel(Job).where(
            Job.type == JobType.content_collect.value,
            Job.status.in_([JobStatus.queued.value, JobStatus.running.value]),
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Content collection already queued or running")

    # Find the first user with a running automation
    auto = db.query(Automation).filter(Automation.status == "running").first()
    if not auto:
        raise HTTPException(status_code=404, detail="No running automation found")

    # Set domain from channel profile
    from gpcg.core.models import ChannelProfile, ContentDomain
    _domain = ContentDomain.games.value
    _profile = db.query(ChannelProfile).filter(
        ChannelProfile.user_id == auto.user_id
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
        user_id=auto.user_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    from gpcg.infrastructure.job_queue import enqueue_job
    enqueue_job(job)

    log.info(f"Auto content collection: created job #{job.id} for user {auto.user_id}")
    return {"job_id": job.id, "message": "Content collection job created"}


class EnqueueRequest(BaseModel):
    user_id: int
    knowledge_item_ids: list[int]


@router.post("/automation/enqueue-ideas")
def enqueue_ideas_worker(
    req: EnqueueRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Worker-auth endpoint to add KnowledgeItems to a user's idea queue.

    Useful when SSH is unavailable but the worker API key is available.
    Only enqueues KIs that are fresh and visible to the user.
    """
    from sqlalchemy.orm.attributes import flag_modified
    from gpcg.core.models import Automation, KnowledgeItem, KnowledgeItemStatus
    auto = db.query(Automation).filter(Automation.user_id == req.user_id).first()
    if not auto:
        raise HTTPException(404, "Automation not found")
    cfg = dict(auto.config or {})
    q = list(cfg.get("idea_queue", []))
    added = []
    for kid in req.knowledge_item_ids:
        ki = db.get(KnowledgeItem, kid)
        if ki and ki.status == KnowledgeItemStatus.fresh.value and kid not in q:
            q.append(kid)
            added.append(kid)
    cfg["idea_queue"] = q
    auto.config = cfg
    flag_modified(auto, "config")
    db.commit()
    return {"queue": q, "added": added}


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
    from gpcg.core.models import (
    Fact,
    KnowledgeChunk,
    KnowledgeItem,
    KnowledgeItemStatus,
    ChannelProfile,
)
    from gpcg.domains.games.models import GameplayAsset
    from sqlalchemy import select, func, desc
    from datetime import datetime, timezone, timedelta

    # 1. Build inventory: games with gameplay sources.
    # Include public gameplays from other users when the user has opted
    # into the public fallback.
    auto = db.query(Automation).filter(Automation.user_id == user_id).first()
    auto_cfg = (auto.config or {}) if auto else {}
    allows_public = user_allows_public_gameplays(auto_cfg)
    gameplay_vis = gameplay_visible_to_user(
        GameplaySource.user_id, GameplaySource.is_public, user_id,
        allows_public=allows_public,
    )
    games_with_gameplay = db.execute(
        select(Game, func.count(GameplaySource.id), func.sum(GameplaySource.duration))
        .join(GameplaySource, GameplaySource.game_id == Game.id)
        .where(gameplay_vis)
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

        # Count usable clips (GameplayAsset) — only sources that are ready
        # AND owned by user OR public. A source with status=ready but 0
        # clips is NOT usable (render pipeline would fail).
        inv["gameplay_clips_available"] = db.execute(
            select(func.count(GameplayAsset.id))
            .join(GameplaySource, GameplayAsset.source_id == GameplaySource.id)
            .where(GameplaySource.game_id == game.id)
            .where(GameplaySource.ingestion_status == IngestionStatus.ready.value)
            .where(
                (GameplaySource.user_id == user_id) |
                (GameplaySource.is_public == True)
            )
        ).scalar() or 0

        inv["gameplay_sources_total"] = db.execute(
            select(func.count(GameplaySource.id))
            .where(gameplay_vis)
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


@router.get("/automation/editorial-brief/{user_id}")
def get_editorial_brief(
    user_id: int,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Return the Editorial Brief (search queries) for content collection.

    Called by the remote worker's content_collect job to get expanded
    search queries (curiosity/lore/news) based on the channel's profile
    and gameplay inventory. This replaces the basic "{game} game" query
    with editorial queries like "Bully hidden secrets", "Bully easter egg",
    "Bully story lore", etc.

    Falls back to basic game names if the Brief cannot be built.
    """
    from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
    from gpcg.application.editorial_brief_builder import EditorialBriefBuilder
    from gpcg.core.models import ChannelProfile

    try:
        profile = db.query(ChannelProfile).filter(
            ChannelProfile.user_id == user_id
        ).first()
        if not profile:
            # No profile — return empty (worker falls back to basic queries)
            return {"search_queries": [], "fallback": True}

        _auto = db.query(Automation).filter(Automation.user_id == user_id).first()
        _accept_public = user_allows_public_gameplays(_auto.config if _auto else None)
        intent = EditorialIntentBuilder().build(db, user_id, profile, accept_public=_accept_public)
        brief = EditorialBriefBuilder().build(db, user_id, profile, intent)

        return {
            "search_queries": [
                {
                    "text": sq.text,
                    "game_id": sq.game_id,
                    "template_name": sq.template_name,
                    "item_type": sq.item_type,
                }
                for sq in brief.search_queries
            ],
            "active_templates": brief.active_templates,
            "collection_targets": brief.collection_targets,
            "fallback": False,
        }
    except Exception as e:
        log.warning(f"Failed to build editorial brief for user {user_id}: {e}")
        return {"search_queries": [], "fallback": True, "error": str(e)}


class CreateJobRequest(BaseModel):
    """Request to create a job from an editorial decision."""
    user_id: int
    game_id: Optional[int] = None
    fact_id: Optional[int] = None
    job_type: str = "generate_short"
    background_game_id: Optional[int] = None
    topic_hint: str = ""
    reason: str = ""


class ConsumeQueueRequest(BaseModel):
    """Request to consume the first item from the user's idea queue."""
    user_id: int


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
    # Auto-select voice based on channel profile language when none is set
    if not voice_name:
        try:
            _profile = db.query(ChannelProfile).filter(
                ChannelProfile.user_id == req.user_id
            ).first()
            if _profile and getattr(_profile, "target_language", None):
                from gpcg.api.routes import _auto_select_voice_for_language
                auto_voice = _auto_select_voice_for_language(_profile.target_language, settings)
                if auto_voice:
                    voice_name = auto_voice
        except Exception:
            pass
    voice_path = ""
    if voice_name:
        # Get target_language for voice↔language validation
        target_language = ""
        try:
            _prof = db.query(ChannelProfile).filter(
                ChannelProfile.user_id == req.user_id
            ).first()
            if _prof and getattr(_prof, "target_language", None):
                target_language = _prof.target_language
        except Exception:
            pass
        from gpcg.api.routes import _resolve_voice_path
        voice_path = _resolve_voice_path(voice_name, req.user_id, settings, target_language)
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

    V4: Domain-aware dispatch. Games users use the existing path (gameplays,
    KnowledgeItem queue). Kids users use the new Kids path (StoryAssets,
    KidsIdea queue → KidsTopic → generate_short).

    O sistema decide autonomamente qual jogo e tema abordar, analisando:
    - quais jogos têm gameplays prontas
    - quais jogos têm conhecimento (facts, chunks)
    - quais temas já foram produzidos (para evitar repetição)

    Retorna o job_id ou None se não puder criar (sem gameplays, sem YouTube, etc).
    """
    # Domain dispatch: Kids users use the Kids strategy
    from gpcg.domains.automation_strategies import get_user_domain, KidsAutomationStrategy
    with session_scope() as session:
        domain = get_user_domain(session, user_id)
        if domain == "kids":
            return KidsAutomationStrategy.create_job(user_id)

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

        # Check if there are gameplays available (own + public fallback)
        cfg = auto.config or {}
        allows_public = user_allows_public_gameplays(cfg)
        ready_count = session.query(GameplaySource).filter(
            gameplay_visible_to_user(
                GameplaySource.user_id, GameplaySource.is_public, user_id,
                allows_public=allows_public,
            ),
            GameplaySource.ingestion_status == IngestionStatus.ready.value,
            GameplaySource.enabled == True,
        ).count()
        if ready_count == 0:
            return None

        # Check if there's already a running/queued GENERATION job.
        # NOTE: content_collect jobs run on the VPS (not the worker) and
        # should NOT block generation — they're a different pipeline.
        active_gen_jobs = session.query(Job).filter(
            Job.user_id == user_id,
            Job.status.in_([JobStatus.queued.value, JobStatus.running.value]),
            Job.type != "content_collect",
        ).count()
        if active_gen_jobs > 0:
            return None

        # ── Idea queue: user-curated queue takes priority ───────────────
        # If the user has queued KnowledgeItems, consume the first one (FIFO)
        # instead of running the autonomous editorial decision.
        auto = session.query(Automation).filter(Automation.user_id == user_id).first()
        config = auto.config or {} if auto else {}
        # V3: idea_queue is now list[dict] with metadata. Normalize for backward compat.
        raw_queue = config.get("idea_queue", [])
        idea_queue = _normalize_idea_queue(raw_queue)

    if idea_queue:
        # Consume the first idea in the queue
        queue_entry = idea_queue[0]
        ki_id = queue_entry.get("ki_id") if isinstance(queue_entry, dict) else queue_entry
        gameplay_preference = queue_entry.get("gameplay_preference") if isinstance(queue_entry, dict) else None
        reuse_override = queue_entry.get("reuse_override") if isinstance(queue_entry, dict) else None
        gameplay_source_id = queue_entry.get("gameplay_source_id") if isinstance(queue_entry, dict) else None
        from gpcg.application.generation_service import GenerationService
        from gpcg.core.models import KnowledgeItem, KnowledgeItemStatus
        from gpcg.infrastructure.llm import get_llm

        with session_scope() as session:
            ki = session.get(KnowledgeItem, ki_id)
            # Check eligibility: KI must be fresh AND not already used by this consumer
            from gpcg.application.knowledge_item_service import is_used_by_consumer
            already_used = is_used_by_consumer(session, ki_id, user_id) if ki else True
            if ki is None or ki.status != KnowledgeItemStatus.fresh.value or already_used:
                # KI no longer available — remove from queue and skip
                reason = "not found" if ki is None else ("already used by consumer" if already_used else f"status={ki.status}")
                log.info(f"automation: queued KI #{ki_id} no longer eligible ({reason}), removing from queue")
                from sqlalchemy.orm.attributes import flag_modified
                auto = session.query(Automation).filter(Automation.user_id == user_id).first()
                cfg = dict(auto.config or {})
                q = _normalize_idea_queue(cfg.get("idea_queue", []))
                q = [e for e in q if e.get("ki_id") != ki_id]
                cfg["idea_queue"] = q
                auto.config = cfg
                flag_modified(auto, "config")
                session.commit()
                return None

            # V3: If user chose a specific game (gameplay_preference), use it
            # as the background game instead of the automatic selection.
            # Validate that the game has ready gameplay for this user.
            bg_game = None
            if gameplay_preference:
                bg_game = session.query(Game).join(
                    GameplaySource, GameplaySource.game_id == Game.id
                ).filter(
                    Game.id == gameplay_preference,
                    GameplaySource.ingestion_status == IngestionStatus.ready.value,
            GameplaySource.enabled == True,
                ).first()
                if bg_game:
                    # Check ownership/visibility: user's own or public
                    has_access = session.query(GameplaySource).filter(
                        GameplaySource.game_id == gameplay_preference,
                        GameplaySource.ingestion_status == IngestionStatus.ready.value,
            GameplaySource.enabled == True,
                        ((GameplaySource.user_id == user_id) |
                         (GameplaySource.is_public == True)),
                    ).first()
                    if not has_access:
                        log.warning(f"automation: gameplay_preference game #{gameplay_preference} not accessible by user #{user_id}")
                        bg_game = None
                else:
                    log.warning(f"automation: gameplay_preference game #{gameplay_preference} has no ready gameplay")

            # V4: Validate gameplay_source_id if specified
            if gameplay_source_id:
                src = session.query(GameplaySource).filter(
                    GameplaySource.id == gameplay_source_id,
                    GameplaySource.ingestion_status == IngestionStatus.ready.value,
                    GameplaySource.enabled == True,
                    ((GameplaySource.user_id == user_id) |
                     (GameplaySource.is_public == True)),
                ).first()
                if not src:
                    log.warning(f"automation: gameplay_source_id #{gameplay_source_id} not accessible/enabled — ignoring")
                    gameplay_source_id = None
                elif gameplay_preference and src.game_id != gameplay_preference:
                    log.warning(f"automation: gameplay_source_id #{gameplay_source_id} belongs to game #{src.game_id}, "
                                f"not #{gameplay_preference} — ignoring")
                    gameplay_source_id = None

            # Fallback: automatic background game selection
            # CRITICAL: only pick games that have usable clips (GameplayAsset),
            # not just sources with status=ready. A source with 0 clips will
            # cause the render pipeline to fail with "no gameplay assets available".
            if not bg_game:
                from gpcg.domains.games.models import GameplayAsset
                bg_game_q = session.query(Game).join(
                    GameplaySource, GameplaySource.game_id == Game.id
                ).join(
                    GameplayAsset, GameplayAsset.source_id == GameplaySource.id
                ).filter(
                    GameplaySource.ingestion_status == IngestionStatus.ready.value,
            GameplaySource.enabled == True,
                )
                if allows_public:
                    bg_game_q = bg_game_q.filter(
                        _or2(
                            GameplaySource.user_id == user_id,
                            GameplaySource.is_public == True,
                        )
                    )
                else:
                    bg_game_q = bg_game_q.filter(GameplaySource.user_id == user_id)
                bg_game = bg_game_q.order_by(Game.id).first()

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
            # Auto-select voice based on channel profile language when none is set
            if not voice_name:
                from gpcg.config import get_settings
                settings = get_settings()
                try:
                    _prof = db.query(ChannelProfile).filter(
                        ChannelProfile.user_id == auto.user_id
                    ).first()
                    if _prof and getattr(_prof, "target_language", None):
                        from gpcg.api.routes import _auto_select_voice_for_language
                        auto_voice = _auto_select_voice_for_language(_prof.target_language, settings)
                        if auto_voice:
                            voice_name = auto_voice
                except Exception:
                    pass
            voice_path = ""
            if voice_name:
                from gpcg.config import get_settings
                settings = get_settings()
                # Get target_language for voice↔language validation
                target_language = ""
                try:
                    _prof = db.query(ChannelProfile).filter(
                        ChannelProfile.user_id == auto.user_id
                    ).first()
                    if _prof and getattr(_prof, "target_language", None):
                        target_language = _prof.target_language
                except Exception:
                    pass
                from gpcg.api.routes import _resolve_voice_path
                voice_path = _resolve_voice_path(voice_name, auto.user_id, settings, target_language)
            subtitle_kwargs["voice_path"] = voice_path

            service = GenerationService(llm=get_llm())

            # V3: Build a config snapshot for deterministic retry.
            config_snapshot = _build_config_snapshot(cfg)

            # Multilingual: build GenerationContext from ChannelProfile and
            # freeze it in job artifacts for deterministic retry/resume.
            from gpcg.i18n.language_context import GenerationContext
            _profile = session.query(ChannelProfile).filter(
                ChannelProfile.user_id == auto.user_id
            ).first()
            _gen_ctx = GenerationContext.from_channel_profile(_profile)
            # Inject language into config_snapshot so downstream services can read it
            if "language" not in config_snapshot:
                config_snapshot = {**config_snapshot, "language": _gen_ctx.language}

            # V3: Store gameplay preference + reuse override in job artifacts
            # so the generation pipeline can use them during gameplay selection.
            extra_artifacts = {
                "queued_knowledge_item_id": ki_id,
                "idea_source": "user_queue",
                "gameplay_preference": gameplay_preference,  # null=auto, game_id=user chose
                "reuse_override": reuse_override,  # null, "allow_reuse", "skip"
                "gameplay_source_id": gameplay_source_id,  # null=all sources, source_id=specific
                "gameplay_selection_mode": "manual" if gameplay_preference else "auto",
                "config_snapshot": config_snapshot,
                "generation_context": _gen_ctx.to_dict(),
            }

            # If KI has a game_id, create a game-specific short;
            # otherwise, create a curiosity_short with background gameplay.
            # FALLBACK: if the KI's game_id doesn't resolve to a local Game
            # (e.g. it's an IGDB catalog ID) or has no usable clips, but the
            # user set a gameplay_preference that IS valid, fall through to
            # the curiosity_short path using the preference as background.
            # This prevents KIs about games without gameplay from being
            # silently dropped from the queue.
            _job_created = False
            if ki.game_id:
                game = session.get(Game, ki.game_id)
                if game:
                    # CRITICAL: Verify that the user has usable clips for this game
                    # BEFORE creating the job. Without this check, the system would
                    # spend GPU time on script/TTS only to fail at render with
                    # "no gameplay assets available".
                    from gpcg.domains.games.models import GameplayAsset
                    user_clips = session.query(GameplayAsset).join(
                        GameplaySource, GameplayAsset.source_id == GameplaySource.id
                    ).filter(
                        GameplaySource.game_id == ki.game_id,
                        GameplaySource.ingestion_status == IngestionStatus.ready.value,
            GameplaySource.enabled == True,
                        ((GameplaySource.user_id == user_id) |
                         (GameplaySource.is_public == True)),
                    ).count()
                    if user_clips > 0:
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
                                **extra_artifacts,
                            }
                            s2.flush()
                        log.info(f"automation: created generate_short job #{job.id} from queued KI #{ki_id} (game={game.canonical_name})")
                        _job_created = True
                    else:
                        log.warning(
                            f"automation: queued KI #{ki_id} references game '{game.canonical_name}' "
                            f"but user #{user_id} has no usable clips — "
                            f"falling back to curiosity_short with gameplay_preference"
                        )
                        # Fall through to curiosity_short path below
                else:
                    log.warning(
                        f"automation: queued KI #{ki_id} references game_id {ki.game_id} "
                        f"not in local games table — falling back to curiosity_short"
                    )
                    # Fall through to curiosity_short path below

            if not _job_created:
                # General idea → curiosity_short with background gameplay
                # Also reached when ki.game_id didn't resolve or had no clips,
                # but gameplay_preference is valid (user wants to talk about
                # a game without gameplay using another game's gameplay as background).
                # V3: If gameplay_preference was set and validated (bg_game found
                # for that specific game), use it as background_game_id.
                # Otherwise fall back to auto-selected bg_game.
                if gameplay_preference and bg_game:
                    chosen_bg_game_id = bg_game.id
                    log.info(f"automation: using user-selected gameplay game #{chosen_bg_game_id} for KI #{ki_id}")
                elif bg_game:
                    chosen_bg_game_id = bg_game.id
                else:
                    log.warning("automation: no background game available for curiosity_short")
                    return None
                job = service.create_curiosity_job(
                    background_game_id=chosen_bg_game_id,
                    fact_id=None,
                    user_id=user_id,
                    **subtitle_kwargs,
                )
                with session_scope() as s2:
                    j = s2.get(Job, job.id)
                    j.artifacts = {
                        **j.artifacts,
                        **extra_artifacts,
                    }
                    s2.flush()
                log.info(f"automation: created curiosity_short job #{job.id} from queued KI #{ki_id} (bg={bg_game.canonical_name})")

            # Remove the consumed KI from the queue
            with session_scope() as session2:
                from sqlalchemy.orm.attributes import flag_modified
                auto2 = session2.query(Automation).filter(Automation.user_id == user_id).first()
                cfg2 = dict(auto2.config or {})
                q2 = _normalize_idea_queue(cfg2.get("idea_queue", []))
                q2 = [e for e in q2 if e.get("ki_id") != ki_id]
                cfg2["idea_queue"] = q2
                auto2.config = cfg2
                flag_modified(auto2, "config")
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
        # Determine whether the user accepts public gameplays (fallback_policy
        # == "allow_public" or accept_public_gameplays == True) so the
        # editorial inventory includes public gameplay sources.
        _auto = session.query(Automation).filter(Automation.user_id == user_id).first()
        _accept_public = user_allows_public_gameplays(_auto.config if _auto else None)
        decision = editorial.decide_next_video(
            session, user_id, accept_public=_accept_public
        )

    if not decision.success:
        log.info(f"automation: editorial decision failed: {decision.error}")
        return None

    # Create the job using the editorial decision
    from gpcg.application.generation_service import GenerationService
    from gpcg.i18n.language_context import GenerationContext

    with session_scope() as session:
        auto = session.query(Automation).filter(Automation.user_id == user_id).first()
        config = auto.config or {} if auto else {}

        # Multilingual: build GenerationContext from ChannelProfile
        _profile = session.query(ChannelProfile).filter(
            ChannelProfile.user_id == user_id
        ).first()
        _gen_ctx = GenerationContext.from_channel_profile(_profile)

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
        # Auto-select voice based on channel profile language when none is set
        if not voice_name:
            from gpcg.config import get_settings
            settings = get_settings()
            try:
                _prof = session.query(ChannelProfile).filter(
                    ChannelProfile.user_id == user_id
                ).first()
                if _prof and getattr(_prof, "target_language", None):
                    from gpcg.api.routes import _auto_select_voice_for_language
                    auto_voice = _auto_select_voice_for_language(_prof.target_language, settings)
                    if auto_voice:
                        voice_name = auto_voice
            except Exception:
                pass
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
                _snap = _build_config_snapshot(config)
                _snap = {**_snap, "language": _gen_ctx.language}
                j.artifacts = {
                    **j.artifacts,
                    "editorial_decision": decision.to_dict(),
                    "fact_id": decision.fact_id,
                    "config_snapshot": _snap,
                    "generation_context": _gen_ctx.to_dict(),
                }
                s2.flush()
        elif decision.job_type == "curiosity_short" and decision.background_game_id:
            job = service.create_curiosity_job(
                background_game_id=decision.background_game_id,
                fact_id=decision.fact_id,
                user_id=user_id,
                **subtitle_kwargs,
            )
            # Store editorial decision + config snapshot
            with session_scope() as s2:
                j = s2.get(Job, job.id)
                _snap = _build_config_snapshot(config)
                _snap = {**_snap, "language": _gen_ctx.language}
                j.artifacts = {
                    **j.artifacts,
                    "editorial_decision": decision.to_dict(),
                    "fact_id": decision.fact_id,
                    "config_snapshot": _snap,
                    "generation_context": _gen_ctx.to_dict(),
                }
                s2.flush()
        else:
            log.warning(f"automation: invalid editorial decision: {decision.to_dict()}")
            return None

        # Update last_run_at
        from datetime import datetime, timezone
        auto.last_run_at = datetime.now(timezone.utc)
        session.flush()

        return job.id


# ── Presentation Layer — image upload + serving ──────────────────────────────


@router.post("/presentation/upload-image")
async def upload_presentation_image(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """Upload an image for the Presentation Layer (thumbnail/opening).

    Saves to data/presentation/{user_id}/ and returns the storage_key
    that can be used in the presentation config.
    """
    from fastapi import UploadFile as _UF
    from gpcg.config import get_settings
    settings = get_settings()
    user_dir = settings.presentation_dir / f"user_{user.id}"
    user_dir.mkdir(parents=True, exist_ok=True)

    # Generate a unique filename
    import time as _time
    ext = Path(file.filename or "image.jpg").suffix or ".jpg"
    storage_key = f"user_{user.id}/pres_{int(_time.time())}_{file.filename or 'image'}{ext}"
    dest = settings.presentation_dir / storage_key
    dest.parent.mkdir(parents=True, exist_ok=True)

    with open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)

    file_size = dest.stat().st_size
    log.info(f"Presentation image uploaded by user {user.id}: {storage_key} ({file_size} bytes)")
    return {"storage_key": storage_key, "filename": file.filename, "file_size": file_size}


@router.get("/presentation/image/{storage_key:path}")
def serve_presentation_image(
    storage_key: str,
    user: User = Depends(get_current_user),
):
    """Serve a presentation image by storage_key."""
    from fastapi.responses import FileResponse
    from gpcg.config import get_settings
    settings = get_settings()
    # Prevent path traversal
    if ".." in storage_key or storage_key.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid storage key")
    path = settings.presentation_dir / storage_key
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(str(path))


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

    # Channel domain (needed to decide which stats to query)
    from gpcg.core.models import ChannelProfile, ContentDomain
    profile = db.query(ChannelProfile).filter(
        ChannelProfile.user_id == user.id
    ).first()
    channel_domain = profile.domain if profile else ContentDomain.games.value

    # Gameplays (only query for Games domain — avoids unnecessary queries for Kids)
    if channel_domain == ContentDomain.games.value:
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
            GameplaySource.enabled == True,
        ).count()
    else:
        total_gameplays = 0
        processing_gameplays = 0
        ready_gameplays = 0

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
        social_description = None
        social_tags = None
        if v.job_id:
            job = db.get(Job, v.job_id)
            if job and isinstance(job.artifacts, dict):
                social_title = job.artifacts.get("social_title")
                social_description = job.artifacts.get("social_description")
                social_tags = job.artifacts.get("social_tags")
        cp = db.get(ContentPlan, v.content_plan_id) if v.content_plan_id else None
        recent_list.append({
            "id": v.id,
            "status": v.status,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "qa_score": v.qa_score,
            "qa_passed": v.qa_report.get("passed", False) if v.qa_report else False,
            "duration": v.duration,
            "width": v.width,
            "height": v.height,
            "thumbnail_path": v.thumbnail_path,
            "topic": cp.topic if cp else None,
            "social_title": social_title,
            "social_description": social_description,
            "social_tags": social_tags,
            "youtube_url": v.youtube_url,
            "youtube_video_id": v.youtube_video_id,
        })

    # Automation
    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    auto_status = auto.status if auto else "idle"

    # Domain-aware stats: Games gets gameplay stats, Kids gets topic/asset stats
    result = {
        "youtube_connected": yt_connected,
        "youtube_channel": yt_channel,
        "channel_domain": channel_domain,
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

    if channel_domain == "games":
        result["gameplays"] = {
            "total": total_gameplays,
            "processing": processing_gameplays,
            "ready": ready_gameplays,
        }
    elif channel_domain == "kids":
        # Kids domain stats: topics + story assets
        from gpcg.domains.kids.models import KidsTopic, StoryAsset, AssetProcessingStatus
        total_topics = db.query(KidsTopic).filter(
            KidsTopic.user_id == user.id
        ).count()
        total_assets = db.query(StoryAsset).filter(
            StoryAsset.user_id == user.id
        ).count()
        ready_assets = db.query(StoryAsset).filter(
            StoryAsset.user_id == user.id,
            StoryAsset.processing_status == AssetProcessingStatus.ready.value,
        ).count()
        result["kids"] = {
            "total_topics": total_topics,
            "total_assets": total_assets,
            "ready_assets": ready_assets,
        }
    else:
        result["gameplays"] = {
            "total": total_gameplays,
            "processing": processing_gameplays,
            "ready": ready_gameplays,
        }

    return result


@router.get("/health/problems")
def detect_health_problems(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Detect inventory problems for the current user.

    Returns a structured report of issues that could cause failures:
    - sources_without_clips: ready sources with 0 GameplayAssets
    - sources_without_events: ready sources with 0 GameplayEvents (not mapped)
    - stuck_jobs: jobs running for > 1h without update
    - rejected_kis_in_queue: rejected KIs still in the idea queue
    - kis_without_gameplay: fresh KIs with game_id but no clips for user
    """
    from gpcg.application.problem_detector import detect_problems
    return detect_problems(db, user.id)


@router.post("/idea-queue/cleanup")
def cleanup_idea_queue(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove invalid KIs from the user's idea queue.

    Removes:
    - KIs that no longer exist (deleted)
    - KIs with status=rejected
    - KIs with game_id where user has no clips (would fail at render)
    """
    from gpcg.domains.games.models import GameplayAsset
    from sqlalchemy.orm.attributes import flag_modified

    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    if not auto:
        return {"removed": 0, "remaining": 0}

    cfg = dict(auto.config or {})
    raw_queue = cfg.get("idea_queue", [])
    queue = _normalize_idea_queue(raw_queue)

    removed = []
    cleaned = []
    for entry in queue:
        ki_id = entry.get("ki_id") if isinstance(entry, dict) else entry
        ki = db.get(KnowledgeItem, ki_id) if ki_id else None

        if ki is None:
            removed.append({"ki_id": ki_id, "reason": "not found"})
            continue
        if ki.status == KnowledgeItemStatus.rejected.value:
            removed.append({"ki_id": ki_id, "title": ki.title[:60] if ki.title else "", "reason": "rejected"})
            continue
        if ki.game_id is not None:
            user_clips = db.execute(
                select(func.count(GameplayAsset.id))
                .join(GameplaySource, GameplayAsset.source_id == GameplaySource.id)
                .where(
                    GameplaySource.game_id == ki.game_id,
                    GameplaySource.ingestion_status == IngestionStatus.ready.value,
            GameplaySource.enabled == True,
                    GameplaySource.ingestion_status != IngestionStatus.deleted.value,
                    ((GameplaySource.user_id == user.id) |
                     (GameplaySource.is_public == True)),
                )
            ).scalar()
            if user_clips == 0:
                removed.append({"ki_id": ki_id, "title": ki.title[:60] if ki.title else "", "reason": "no clips for game"})
                continue

        cleaned.append(entry)

    cfg["idea_queue"] = cleaned
    auto.config = cfg
    flag_modified(auto, "config")
    db.commit()

    log.info(f"idea queue cleanup: removed {len(removed)} invalid KIs for user #{user.id}")
    return {
        "removed": len(removed),
        "remaining": len(cleaned),
        "details": removed,
    }
