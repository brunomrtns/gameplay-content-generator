"""Game Registry API routes (V2).

Endpoints for the canonical game registry: list, search, detail,
enrichment trigger, and alias management.

See ARCHITECTURE_V2.md §11.1.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from gpcg.domain.game_registry import (
    add_alias,
    find_by_slug,
    get_aliases,
    list_all,
    remove_alias,
    search,
)
from gpcg.core.models import (
    Job,
    JobType,
    JobStatus,
    JobPriority,
    User,
)
from gpcg.domains.games.models import Game, GameAlias
from gpcg.infrastructure.auth import get_current_user
from gpcg.infrastructure.database import get_db

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────


class AliasOut(BaseModel):
    id: int
    game_id: int
    alias: str
    alias_type: str
    source: str

    class Config:
        from_attributes = True


class GameOut(BaseModel):
    id: int
    canonical_name: str
    slug: str
    description: Optional[str] = None
    developer: Optional[str] = None
    publisher: Optional[str] = None
    franchise: Optional[str] = None
    genres: list = []
    themes: list = []
    lore_summary: Optional[str] = None
    release_date: Optional[str] = None
    camera_type: str = "unknown"
    platforms: list = []
    capture_sources: list = []
    enriched_at: Optional[str] = None
    enrichment_error: Optional[str] = None
    enrichment_state: str = "pending"
    aliases: list[AliasOut] = []

    class Config:
        from_attributes = True


class AliasCreate(BaseModel):
    alias: str
    alias_type: str = "alternative"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _game_to_out(session: Session, game: Game) -> GameOut:
    """Serialize a Game to GameOut, including aliases from game_aliases table."""
    aliases = get_aliases(session, game.id)
    return GameOut(
        id=game.id,
        canonical_name=game.canonical_name,
        slug=game.slug or "",
        description=game.description,
        developer=game.developer,
        publisher=game.publisher,
        franchise=game.franchise,
        genres=game.genres or [],
        themes=game.themes or [],
        lore_summary=game.lore_summary,
        release_date=game.release_date.isoformat() if game.release_date else None,
        camera_type=game.camera_type,
        platforms=game.platforms or [],
        capture_sources=game.capture_sources or [],
        enriched_at=game.enriched_at.isoformat() if game.enriched_at else None,
        enrichment_error=game.enrichment_error,
        enrichment_state=game.enrichment_state,
        aliases=[AliasOut.model_validate(a) for a in aliases],
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/games/registry")
def list_games_registry(
    q: Optional[str] = Query(None, description="Search query (name or alias)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    enrichment_state: Optional[str] = Query(None, description="Filter: pending|enriched|error"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List games in the canonical registry with optional search and filters."""
    if q:
        games = search(db, q, limit=limit + offset)
        if offset:
            games = games[offset:]
        games = games[:limit]
    else:
        games = list_all(db)
        if enrichment_state:
            games = [g for g in games if g.enrichment_state == enrichment_state]
        games = games[offset : offset + limit]

    return {
        "games": [_game_to_out(db, g) for g in games],
        "total": len(games),
    }


@router.get("/games/{slug_or_id}")
def get_game_by_slug(
    slug_or_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get detailed information about a game by slug or numeric ID."""
    # If it's a numeric ID, look up by ID
    if slug_or_id.isdigit():
        game = db.get(Game, int(slug_or_id))
        if not game:
            raise HTTPException(status_code=404, detail=f"Game #{slug_or_id} not found")
        return _game_to_out(db, game)
    # Otherwise treat as slug
    game = find_by_slug(db, slug_or_id)
    if not game:
        raise HTTPException(status_code=404, detail=f"Game with slug '{slug_or_id}' not found")
    return _game_to_out(db, game)


@router.get("/games/search")
def search_games(
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Search games by name, slug, or alias (case-insensitive substring)."""
    games = search(db, q, limit=limit)
    return {
        "games": [_game_to_out(db, g) for g in games],
        "query": q,
    }


@router.post("/games/{game_id}/enrich")
def trigger_enrichment(
    game_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Trigger manual enrichment for a game (creates a game_enrich job).

    Dedup: if a game_enrich job is already queued or running for this game,
    returns 409 Conflict instead of creating a duplicate.
    """
    game = db.get(Game, game_id)
    if not game:
        raise HTTPException(status_code=404, detail=f"Game #{game_id} not found")

    # Dedup: check for existing queued/running enrichment job
    import uuid
    from sqlalchemy import select

    existing = db.execute(
        select(Job).where(
            Job.type == JobType.game_enrich.value,
            Job.game_id == game_id,
            Job.status.in_([JobStatus.queued.value, JobStatus.running.value]),
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Enrichment job already {existing.status} for game '{game.canonical_name}'",
        )

    # Create enrichment job
    job = Job(
        job_uuid=str(uuid.uuid4()),
        type=JobType.game_enrich.value,
        game_id=game_id,
        user_id=game.user_id,
        status=JobStatus.queued.value,
        stage="enrichment",
        priority=JobPriority.normal.value,
        required_capabilities=["enrichment"],
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    from gpcg.infrastructure.job_queue import enqueue_job
    enqueue_job(job)

    from gpcg.infrastructure.events import publish_game_enriched
    publish_game_enriched(game.id, game.canonical_name)
    return {
        "message": f"Enrichment job created for game '{game.canonical_name}'",
        "job_id": job.id,
        "job_uuid": job.job_uuid,
    }


@router.post("/games/{game_id}/aliases")
def add_game_alias(
    game_id: int,
    body: AliasCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Add an alias to a game."""
    game = db.get(Game, game_id)
    if not game:
        raise HTTPException(status_code=404, detail=f"Game #{game_id} not found")

    alias_row = add_alias(db, game_id, body.alias, alias_type=body.alias_type, source="manual")
    if not alias_row:
        raise HTTPException(
            status_code=409,
            detail=f"Alias '{body.alias}' already exists for this game or belongs to another game",
        )
    db.commit()
    db.refresh(alias_row)
    return AliasOut.model_validate(alias_row)


@router.delete("/games/{game_id}/aliases/{alias_id}")
def delete_game_alias(
    game_id: int,
    alias_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Remove an alias from a game."""
    removed = remove_alias(db, game_id, alias_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Alias not found for this game")
    db.commit()
    return {"message": "Alias removed"}


# ── Gameplay Availability (V3) ───────────────────────────────────────────────


@router.get("/gameplay-availability")
def get_gameplay_availability(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List games with available gameplay for the current user, with availability status.

    V3: Returns per-game availability information so the UI can show badges
    like "bastante material", "pouco material", "sem material novo".

    For each game:
    - game_id, game_name
    - ownership: "own" or "public" (whether user has own gameplay or only public)
    - availability: "abundant" | "partial" | "low" | "none" | "reuse_only"
    - total_sources, available_seconds, used_seconds
    - eligible_events, total_events

    Availability is calculated from the consumer's perspective (per-user
    usage history), not globally.
    """
    from sqlalchemy import select, func as sql_func
    from gpcg.core.models import Automation
    from gpcg.domains.games.models import (
    GameplaySource,
    GameplayAsset,
    GameplayEvent,
    IngestionStatus,
)
    from gpcg.application.clip_usage_service import (
        get_used_ranges, estimate_availability,
    )

    # Read max_clip_uses from automation config
    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    max_uses = 1
    accept_public = False
    if auto and isinstance(auto.config, dict):
        max_uses = auto.config.get("max_clip_uses", 1)
        fallback_policy = auto.config.get("fallback_policy")
        if fallback_policy == "allow_public":
            accept_public = True
        elif fallback_policy == "stop":
            accept_public = False
        else:
            accept_public = auto.config.get("accept_public_gameplays", False)

    # Get all games that have ready gameplay sources accessible to this user
    # User's own + public (if allowed)
    sources_query = (
        select(GameplaySource, Game)
        .join(Game, GameplaySource.game_id == Game.id)
        .where(GameplaySource.ingestion_status == IngestionStatus.ready.value)
        .where(GameplaySource.enabled == True)
    )
    # User's own sources
    own_sources = sources_query.where(GameplaySource.user_id == user.id)
    own_results = db.execute(own_sources).all()

    # Public sources (only if user allows)
    public_results = []
    if accept_public:
        public_sources = sources_query.where(
            GameplaySource.is_public == True,
            GameplaySource.user_id != user.id,
        )
        public_results = db.execute(public_sources).all()

    # Group by game_id
    games_map: dict[int, dict] = {}
    for src, game in own_results:
        if game.id not in games_map:
            games_map[game.id] = {
                "game_id": game.id,
                "game_name": game.canonical_name,
                "ownership": "own",
                "sources": [],
            }
        games_map[game.id]["sources"].append(src)

    for src, game in public_results:
        if game.id not in games_map:
            games_map[game.id] = {
                "game_id": game.id,
                "game_name": game.canonical_name,
                "ownership": "public",
                "sources": [],
            }
        elif games_map[game.id]["ownership"] == "own":
            # User has own gameplay for this game — mark as own (precedence)
            pass
        games_map[game.id]["sources"].append(src)

    # Calculate availability per game
    result = []
    for game_id, info in games_map.items():
        total_avail_sec = 0.0
        total_used_sec = 0.0
        total_sources = len(info["sources"])
        total_eligible_events = 0
        total_events = 0
        worst_status = "abundant"

        for src in info["sources"]:
            # Get assets for this source (to find duration)
            asset = db.execute(
                select(GameplayAsset).where(GameplayAsset.source_id == src.id)
            ).scalars().first()
            if not asset:
                continue
            source_duration = asset.duration

            # Get used ranges for this consumer
            used = get_used_ranges(db, src.id, consumer_user_id=user.id)

            # Get events for this source
            events = db.execute(
                select(GameplayEvent.start_time, GameplayEvent.end_time)
                .where(GameplayEvent.source_id == src.id)
                .order_by(GameplayEvent.start_time)
            ).all()
            event_tuples = [(r[0], r[1]) for r in events]

            avail = estimate_availability(
                source_duration, used, event_tuples, max_uses=max_uses,
            )
            total_avail_sec += avail["available_seconds"]
            total_used_sec += avail["used_seconds"]
            total_eligible_events += avail["eligible_events"]
            total_events += avail["total_events"]

            # Worst status across sources determines game status
            status_order = {"abundant": 0, "partial": 1, "low": 2, "reuse_only": 3, "none": 4}
            if status_order.get(avail["status"], 4) > status_order.get(worst_status, 0):
                worst_status = avail["status"]

        result.append({
            "game_id": game_id,
            "game_name": info["game_name"],
            "ownership": info["ownership"],
            "availability": worst_status,
            "total_sources": total_sources,
            "available_seconds": round(total_avail_sec, 1),
            "used_seconds": round(total_used_sec, 1),
            "eligible_events": total_eligible_events,
            "total_events": total_events,
        })

    # Sort: own games first, then by availability (abundant first)
    status_order = {"abundant": 0, "partial": 1, "low": 2, "reuse_only": 3, "none": 4}
    result.sort(key=lambda g: (0 if g["ownership"] == "own" else 1, status_order.get(g["availability"], 4), g["game_name"]))

    return {"games": result, "max_uses": max_uses}


@router.get("/gameplay-availability/{game_id}/sources")
def get_gameplay_sources_for_game(
    game_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List individual gameplay sources for a specific game, with free-time info.

    Used by the idea-queue modal to let the user pick a specific source.
    Only returns sources with ≥2 minutes (120s) of FREE gameplay time,
    considering the user's clip usage history and max_uses config.

    Returns per-source:
        - source_id, filename, duration
        - free_seconds: estimated free gameplay time (considering used ranges)
        - total_events, eligible_events
        - availability: "abundant" | "partial" | "low" | "none" | "reuse_only"
    """
    from sqlalchemy import select, func as sql_func
    from gpcg.core.models import Automation
    from gpcg.domains.games.models import (
        GameplaySource,
        GameplayAsset,
        GameplayEvent,
        IngestionStatus,
    )
    from gpcg.application.clip_usage_service import (
        get_used_ranges, estimate_availability,
    )

    # Read max_clip_uses from automation config
    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    max_uses = 1
    accept_public = False
    if auto and isinstance(auto.config, dict):
        max_uses = auto.config.get("max_clip_uses", 1)
        fallback_policy = auto.config.get("fallback_policy")
        if fallback_policy == "allow_public":
            accept_public = True
        else:
            accept_public = auto.config.get("accept_public_gameplays", False)

    # Get enabled, ready sources for this game
    sources_q = (
        select(GameplaySource)
        .where(GameplaySource.game_id == game_id)
        .where(GameplaySource.ingestion_status == IngestionStatus.ready.value)
        .where(GameplaySource.enabled == True)
        .where((GameplaySource.user_id == user.id) |
               (GameplaySource.is_public == True if accept_public else False))
    )
    sources = db.execute(sources_q).scalars().all()

    result = []
    for src in sources:
        # Get the primary asset (duration)
        asset = db.execute(
            select(GameplayAsset).where(GameplayAsset.source_id == src.id)
        ).scalars().first()
        if not asset:
            continue
        source_duration = asset.duration

        # Get used ranges for this consumer
        used = get_used_ranges(db, src.id, consumer_user_id=user.id)

        # Get events for this source
        events = db.execute(
            select(GameplayEvent.start_time, GameplayEvent.end_time)
            .where(GameplayEvent.source_id == src.id)
            .order_by(GameplayEvent.start_time)
        ).all()
        event_tuples = [(r[0], r[1]) for r in events]

        avail = estimate_availability(
            source_duration, used, event_tuples, max_uses=max_uses,
        )

        result.append({
            "source_id": src.id,
            "filename": src.filename,
            "duration": round(source_duration, 1),
            "free_seconds": avail["available_seconds"],
            "used_seconds": avail["used_seconds"],
            "total_events": avail["total_events"],
            "eligible_events": avail["eligible_events"],
            "availability": avail["status"],
        })

    # Filter: only sources with ≥120s (2 min) of free gameplay
    result = [s for s in result if s["free_seconds"] >= 120.0]

    # Sort: most free time first
    result.sort(key=lambda s: s["free_seconds"], reverse=True)

    return {"game_id": game_id, "sources": result, "min_free_seconds": 120}
