"""Game registry repository — legacy compatibility wrapper (V2).

This module is kept for backward compatibility with existing imports.
New code should use `gpcg.domain.game_registry` directly, which
implements the V2 canonical registry with slug + game_aliases dedup.

See ARCHITECTURE_V2.md §4 (Game Registry Canônico).
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from gpcg.domain.game_registry import (
    add_alias as _add_alias,
    find_by_name as _find_by_name,
    find_by_slug,
    get_aliases,
    get_or_create as _get_or_create,
    list_all as _list_all,
    remove_alias,
    search,
)
from gpcg.domain.models import Game


def find_by_name(session: Session, name: str) -> Optional[Game]:
    """Find a game by canonical name, slug, or alias (case-insensitive).

    V2: delegates to game_registry.find_by_name which checks slug,
    canonical_name, and game_aliases table (in that order).
    """
    return _find_by_name(session, name)


def get_or_create(
    session: Session,
    canonical_name: str,
    *,
    aliases: Optional[list[str]] = None,
    platforms: Optional[list[str]] = None,
    capture_sources: Optional[list[str]] = None,
) -> Game:
    """Get an existing game by name/slug/alias, or create a new one.

    V2: delegates to game_registry.get_or_create which implements
    the canonical dedup algorithm (slug → alias → create).
    """
    return _get_or_create(
        session,
        canonical_name,
        aliases=aliases,
        platforms=platforms,
        capture_sources=capture_sources,
        alias_source="legacy",
    )


def list_all(session: Session) -> list[Game]:
    """List all games ordered by canonical_name."""
    return _list_all(session)
