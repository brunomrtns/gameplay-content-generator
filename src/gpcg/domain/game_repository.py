"""Game registry repository — CRUD + alias lookup for games."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from gpcg.domain.models import Game


def find_by_name(session: Session, name: str) -> Optional[Game]:
    """Find a game by canonical name or alias (case-insensitive)."""
    name_l = name.lower().strip()
    games = session.execute(select(Game)).scalars().all()
    for g in games:
        if g.canonical_name.lower() == name_l:
            return g
        for alias in (g.aliases or []):
            if alias.lower() == name_l:
                return g
    return None


def get_or_create(
    session: Session,
    canonical_name: str,
    *,
    aliases: Optional[list[str]] = None,
    platforms: Optional[list[str]] = None,
    capture_sources: Optional[list[str]] = None,
) -> Game:
    """Get an existing game by name/alias, or create a new one."""
    existing = find_by_name(session, canonical_name)
    if existing:
        # Merge new aliases/platforms if provided
        changed = False
        if aliases:
            merged = list(set((existing.aliases or []) + aliases))
            if merged != (existing.aliases or []):
                existing.aliases = merged
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
        if changed:
            session.flush()
        return existing

    game = Game(
        canonical_name=canonical_name,
        aliases=aliases or [],
        platforms=platforms or [],
        capture_sources=capture_sources or [],
    )
    session.add(game)
    session.flush()
    return game


def list_all(session: Session) -> list[Game]:
    return list(session.execute(select(Game).order_by(Game.canonical_name)).scalars().all())
