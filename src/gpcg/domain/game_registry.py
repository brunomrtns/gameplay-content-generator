"""Game Registry service — canonical game CRUD with slug + alias dedup (V2).

This is the V2 replacement for game_repository.py. The key difference is
deduplication on creation: when a game name is provided, the registry first
checks for an existing Game by slug, then by alias in the game_aliases table.
Only if neither matches does it create a new Game.

See ARCHITECTURE_V2.md §4.3 (Deduplicação) and §6.7 (Reutilização).
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gpcg.domains.games.models import Game, GameAlias
from gpcg.domain.slug_utils import normalize_name, slugify
from gpcg.logging import get_logger

log = get_logger(__name__)


def find_by_slug(session: Session, slug: str) -> Optional[Game]:
    """Find a game by its canonical slug (exact match)."""
    return session.execute(
        select(Game).where(Game.slug == slug)
    ).scalar_one_or_none()


def find_by_name(session: Session, name: str) -> Optional[Game]:
    """Find a game by canonical name (case-insensitive) or slug.

    V2: checks slug first (O(log n) via index), then canonical_name,
    then game_aliases table (O(log n) via index on LOWER(alias)).
    """
    if not name:
        return None

    normalized = normalize_name(name)
    slug = slugify(name)

    # 1. Try slug (fastest, most canonical)
    game = find_by_slug(session, slug)
    if game:
        return game

    # 2. Try canonical_name (case-insensitive)
    game = session.execute(
        select(Game).where(func.lower(Game.canonical_name) == normalized)
    ).scalar_one_or_none()
    if game:
        return game

    # 3. Try game_aliases (case-insensitive, indexed)
    alias_row = session.execute(
        select(GameAlias).where(func.lower(GameAlias.alias) == normalized)
    ).scalar_one_or_none()
    if alias_row:
        return session.get(Game, alias_row.game_id)

    return None


def find_by_alias(session: Session, alias: str) -> Optional[Game]:
    """Find a game by alias (case-insensitive) via game_aliases table."""
    if not alias:
        return None
    normalized = normalize_name(alias)
    alias_row = session.execute(
        select(GameAlias).where(func.lower(GameAlias.alias) == normalized)
    ).scalar_one_or_none()
    if alias_row:
        return session.get(Game, alias_row.game_id)
    return None


def add_alias(
    session: Session,
    game_id: int,
    alias: str,
    *,
    alias_type: str = "alternative",
    source: str = "manual",
) -> Optional[GameAlias]:
    """Add an alias to a game if it doesn't already exist.

    Returns the GameAlias if created, None if it already existed.
    """
    alias = alias.strip()
    if not alias:
        return None

    # Check if alias already exists for this game
    existing = session.execute(
        select(GameAlias).where(
            GameAlias.game_id == game_id,
            func.lower(GameAlias.alias) == alias.lower(),
        )
    ).scalar_one_or_none()
    if existing:
        return None

    # Check if alias is already used by a DIFFERENT game (would cause dedup confusion)
    other = session.execute(
        select(GameAlias).where(
            func.lower(GameAlias.alias) == alias.lower(),
            GameAlias.game_id != game_id,
        )
    ).scalar_one_or_none()
    if other:
        log.warning(
            f"Alias '{alias}' already belongs to game #{other.game_id}, "
            f"not adding to game #{game_id}"
        )
        return None

    alias_row = GameAlias(
        game_id=game_id,
        alias=alias,
        alias_type=alias_type,
        source=source,
    )
    session.add(alias_row)
    session.flush()
    return alias_row


def get_or_create(
    session: Session,
    canonical_name: str,
    *,
    aliases: Optional[list[str]] = None,
    platforms: Optional[list[str]] = None,
    capture_sources: Optional[list[str]] = None,
    alias_source: str = "manual",
) -> Game:
    """Get an existing game by name/slug/alias, or create a new one.

    V2 dedup algorithm (ARCHITECTURE_V2.md §4.3):
    1. Normalize name, generate slug
    2. Search by slug → if found: reuse, add input name as alias if new
    3. Search by alias in game_aliases → if found: reuse
    4. Create new Game with generated slug

    The input `canonical_name` is added as an alias if it differs from the
    existing canonical name (so future uploads with the same name resolve
    deterministically in L1).
    """
    canonical_name = canonical_name.strip()
    if not canonical_name:
        raise ValueError("canonical_name cannot be empty")

    slug = slugify(canonical_name)

    # 1. Try to find existing by name/slug/alias
    existing = find_by_name(session, canonical_name)
    if existing:
        # Merge new aliases, platforms, capture_sources
        changed = False
        if aliases:
            for alias in aliases:
                created = add_alias(session, existing.id, alias, source=alias_source)
                if created:
                    changed = True
        if platforms:
            merged = list(set((existing.platforms or []) + platforms))
            if merged != (existing.platforms or []):
                existing.platforms = merged
                changed = True
        if capture_sources:
            merged = list(set((existing.capture_sources or []) + capture_sources))
            if merged != (existing.capture_sources or []):
                existing.capture_sources = merged
                changed = True
        # Also add the input canonical_name as alias if it differs from the existing canonical
        if normalize_name(canonical_name) != normalize_name(existing.canonical_name):
            add_alias(session, existing.id, canonical_name, source=alias_source)
            changed = True
        if changed:
            session.flush()
        return existing

    # 2. Create new Game
    game = Game(
        canonical_name=canonical_name,
        slug=slug,
        platforms=platforms or [],
        capture_sources=capture_sources or [],
    )
    session.add(game)
    session.flush()  # get the id

    # Add aliases to game_aliases table
    if aliases:
        for alias in aliases:
            add_alias(session, game.id, alias, source=alias_source)

    log.info(f"Created new Game '{canonical_name}' (slug={slug}, id={game.id})")

    # V2: auto-trigger enrichment if flag is on
    _maybe_trigger_enrichment(session, game)

    return game


def _maybe_trigger_enrichment(session: Session, game: Game) -> None:
    """V2: create a game_enrich job if GPCG_GAME_ENRICHMENT_ENABLED is on.

    Only triggers for newly created games (enriched_at IS NULL).
    Dedup: skips if a game_enrich job already exists for this game.
    """
    try:
        from gpcg.config import get_settings
        settings = get_settings()
        if not settings.gpcg_game_enrichment_enabled:
            return
        if game.enriched_at is not None:
            return  # already enriched

        import uuid
        from sqlalchemy import select as sa_select
        from gpcg.core.models import (
    Job,
    JobType,
    JobStatus,
    JobPriority,
)

        # Dedup: check for existing queued/running enrichment job
        existing = session.execute(
            sa_select(Job).where(
                Job.type == JobType.game_enrich.value,
                Job.game_id == game.id,
                Job.status.in_([JobStatus.queued.value, JobStatus.running.value]),
            )
        ).scalar_one_or_none()
        if existing:
            return  # already queued

        job = Job(
            job_uuid=str(uuid.uuid4()),
            type=JobType.game_enrich.value,
            game_id=game.id,
            status=JobStatus.queued.value,
            stage="enrichment",
            priority=JobPriority.normal.value,
            required_capabilities=["enrichment"],
        )
        session.add(job)
        session.flush()
        log.info(f"Auto-triggered game_enrich job #{job.id} for '{game.canonical_name}'")
    except Exception as e:
        log.warning(f"Failed to auto-trigger enrichment for '{game.canonical_name}': {e}")


def list_all(session: Session, *, include_enrichment: bool = False, limit: Optional[int] = None) -> list[Game]:
    """List all games ordered by canonical_name."""
    stmt = select(Game).order_by(Game.canonical_name)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.execute(stmt).scalars().all())


def search(
    session: Session,
    query: str,
    *,
    limit: int = 20,
) -> list[Game]:
    """Search games by canonical name or alias (case-insensitive substring).

    V2: uses game_aliases table for alias search (indexed).
    """
    if not query:
        return list_all(session, limit=limit)

    pattern = f"%{query.lower()}%"

    # Search by canonical_name (ILIKE equivalent)
    by_name = session.execute(
        select(Game).where(func.lower(Game.canonical_name).like(pattern))
    ).scalars().all()

    # Search by slug
    by_slug = session.execute(
        select(Game).where(func.lower(Game.slug).like(pattern))
    ).scalars().all()

    # Search by alias in game_aliases
    alias_game_ids = session.execute(
        select(GameAlias.game_id).where(func.lower(GameAlias.alias).like(pattern))
    ).scalars().all()
    by_alias = []
    if alias_game_ids:
        by_alias = session.execute(
            select(Game).where(Game.id.in_(alias_game_ids))
        ).scalars().all()

    # Deduplicate while preserving order
    seen = set()
    results = []
    for game in list(by_name) + list(by_slug) + list(by_alias):
        if game.id not in seen:
            seen.add(game.id)
            results.append(game)
    return results[:limit]


def get_aliases(session: Session, game_id: int) -> list[GameAlias]:
    """List all aliases for a game."""
    return list(session.execute(
        select(GameAlias).where(GameAlias.game_id == game_id).order_by(GameAlias.alias)
    ).scalars().all())


def remove_alias(session: Session, game_id: int, alias_id: int) -> bool:
    """Remove an alias from a game. Returns True if removed, False if not found."""
    alias_row = session.execute(
        select(GameAlias).where(GameAlias.id == alias_id, GameAlias.game_id == game_id)
    ).scalar_one_or_none()
    if not alias_row:
        return False
    session.delete(alias_row)
    session.flush()
    return True
