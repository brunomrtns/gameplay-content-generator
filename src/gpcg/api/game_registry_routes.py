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
from gpcg.domain.models import Game, GameAlias, Job, JobType, JobStatus, JobPriority, User
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


@router.get("/games/{slug}")
def get_game_by_slug(
    slug: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get detailed information about a game by its canonical slug."""
    game = find_by_slug(db, slug)
    if not game:
        raise HTTPException(status_code=404, detail=f"Game with slug '{slug}' not found")
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
        status=JobStatus.queued.value,
        stage="enrichment",
        priority=JobPriority.normal.value,
        required_capabilities=["enrichment"],
    )
    db.add(job)
    db.commit()
    db.refresh(job)

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
