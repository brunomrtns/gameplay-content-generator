"""Catalog query service — search, autocomplete, and retrieval.

All queries are read-only and use the catalog DB directly (no HTTP).
The GPCG API proxies requests to the catalog service's HTTP endpoints,
which call these functions.

Search strategy:
  1. Exact slug match (highest priority)
  2. Case-insensitive name prefix match
  3. Case-insensitive alias match
  4. Substring match on name (lowest priority, most expensive)

Results are ranked by: match quality → total_rating_count → name.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func, or_, select

from gpcg.catalog.database import session_scope
from gpcg.catalog.models import CatalogAlias, CatalogGame

log = logging.getLogger(__name__)


@dataclass
class GameSummary:
    """Lightweight game info for search results and lists."""

    id: int
    name: str
    slug: str
    cover_url: Optional[str]
    total_rating: Optional[float]
    total_rating_count: Optional[int]
    release_year: Optional[int]
    genres: list[str]

    @classmethod
    def from_orm(cls, game: CatalogGame) -> "GameSummary":
        return cls(
            id=game.id,
            name=game.name,
            slug=game.slug,
            cover_url=game.cover_url,
            total_rating=game.total_rating,
            total_rating_count=game.total_rating_count,
            release_year=game.release_year,
            genres=game.genres or [],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "cover_url": self.cover_url,
            "total_rating": self.total_rating,
            "total_rating_count": self.total_rating_count,
            "release_year": self.release_year,
            "genres": self.genres,
        }


@dataclass
class GameDetail:
    """Full game info for the /api/games/<id> endpoint."""

    id: int
    name: str
    slug: str
    summary: Optional[str]
    first_release_date: Optional[int]
    release_year: Optional[int]
    total_rating: Optional[float]
    total_rating_count: Optional[int]
    cover_url: Optional[str]
    screenshots: list[str]
    genres: list[str]
    platforms: list[str]
    developer: Optional[str]
    aliases: list[str]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "summary": self.summary,
            "first_release_date": self.first_release_date,
            "release_year": self.release_year,
            "total_rating": self.total_rating,
            "total_rating_count": self.total_rating_count,
            "cover_url": self.cover_url,
            "screenshots": self.screenshots,
            "genres": self.genres,
            "platforms": self.platforms,
            "developer": self.developer,
            "aliases": self.aliases,
        }


class QueryService:
    """Read-only query operations on the catalog DB."""

    def search(self, query: str, limit: int = 20) -> list[GameSummary]:
        """Search games by name and alias.

        Ranking:
          1. Exact slug match
          2. Name starts with query (case-insensitive)
          3. Alias exact match (case-insensitive)
          4. Name contains query (case-insensitive)

        Within each tier, results are sorted by total_rating_count desc.
        """
        query = query.strip()
        if not query:
            return []

        q_lower = query.lower()
        results: list[tuple[int, int, CatalogGame]] = []  # (tier, rating_count, game)

        with session_scope() as session:
            # Fetch all candidate games that match any criterion.
            # We use a single query with OR conditions, then rank in Python.
            # For a catalog of ~5-10k games, this is fast enough.
            stmt = select(CatalogGame).where(
                or_(
                    func.lower(CatalogGame.name) == q_lower,
                    func.lower(CatalogGame.slug) == q_lower,
                    func.lower(CatalogGame.name).startswith(q_lower),
                    func.lower(CatalogGame.name).contains(q_lower),
                    CatalogGame.id.in_(
                        select(CatalogAlias.game_id).where(
                            func.lower(CatalogAlias.alias) == q_lower
                        )
                    ),
                )
            )
            games = session.execute(stmt).scalars().all()

            for game in games:
                name_lower = game.name.lower()
                slug_lower = game.slug.lower()

                if slug_lower == q_lower or name_lower == q_lower:
                    tier = 0
                elif name_lower.startswith(q_lower):
                    tier = 1
                elif self._has_exact_alias(session, game.id, q_lower):
                    tier = 2
                else:
                    tier = 3

                rating_count = game.total_rating_count or 0
                results.append((tier, rating_count, game))

        # Sort by tier (0=best), then by rating_count desc, then by name
        results.sort(key=lambda x: (x[0], -x[1], x[2].name))

        return [GameSummary.from_orm(g) for _, _, g in results[:limit]]

    def _has_exact_alias(self, session, game_id: int, alias_lower: str) -> bool:
        """Check if a game has an exact alias match (case-insensitive)."""
        result = session.execute(
            select(CatalogAlias.id).where(
                CatalogAlias.game_id == game_id,
                func.lower(CatalogAlias.alias) == alias_lower,
            )
        ).first()
        return result is not None

    def get_game(self, game_id: int) -> Optional[GameDetail]:
        """Get full details for a game by IGDB ID."""
        with session_scope() as session:
            game = session.get(CatalogGame, game_id)
            if game is None:
                return None

            aliases = session.execute(
                select(CatalogAlias.alias).where(
                    CatalogAlias.game_id == game_id
                ).order_by(CatalogAlias.alias)
            ).scalars().all()

            return GameDetail(
                id=game.id,
                name=game.name,
                slug=game.slug,
                summary=game.summary,
                first_release_date=game.first_release_date,
                release_year=game.release_year,
                total_rating=game.total_rating,
                total_rating_count=game.total_rating_count,
                cover_url=game.cover_url,
                screenshots=game.screenshots or [],
                genres=game.genres or [],
                platforms=game.platforms or [],
                developer=game.developer,
                aliases=list(aliases),
            )

    def get_by_slug(self, slug: str) -> Optional[GameDetail]:
        """Get full details for a game by slug."""
        with session_scope() as session:
            game = session.execute(
                select(CatalogGame).where(CatalogGame.slug == slug)
            ).scalar_one_or_none()
            if game is None:
                return None
            return self.get_game(game.id)

    def autocomplete(self, partial: str, limit: int = 10) -> list[GameSummary]:
        """Fast autocomplete for UI search boxes.

        Uses a simple prefix match on the game name (case-insensitive).
        Sorted by total_rating_count desc so popular games appear first.
        """
        partial = partial.strip()
        if not partial or len(partial) < 2:
            return []

        q_lower = partial.lower()
        with session_scope() as session:
            games = session.execute(
                select(CatalogGame)
                .where(func.lower(CatalogGame.name).startswith(q_lower))
                .order_by(
                    func.coalesce(CatalogGame.total_rating_count, 0).desc(),
                    CatalogGame.name,
                )
                .limit(limit)
            ).scalars().all()

        return [GameSummary.from_orm(g) for g in games]

    def popular(self, limit: int = 50, offset: int = 0) -> list[GameSummary]:
        """Get the most popular games by total_rating_count."""
        with session_scope() as session:
            games = session.execute(
                select(CatalogGame)
                .order_by(
                    func.coalesce(CatalogGame.total_rating_count, 0).desc(),
                    func.coalesce(CatalogGame.total_rating, 0).desc(),
                    CatalogGame.name,
                )
                .offset(offset)
                .limit(limit)
            ).scalars().all()

        return [GameSummary.from_orm(g) for g in games]

    def recent(self, limit: int = 50) -> list[GameSummary]:
        """Get recently released games (newest first)."""
        with session_scope() as session:
            games = session.execute(
                select(CatalogGame)
                .where(CatalogGame.first_release_date.isnot(None))
                .order_by(CatalogGame.first_release_date.desc())
                .limit(limit)
            ).scalars().all()

        return [GameSummary.from_orm(g) for g in games]

    def stats(self) -> dict:
        """Get catalog statistics."""
        from sqlalchemy import func as sql_func

        with session_scope() as session:
            total_games = session.execute(
                select(sql_func.count(CatalogGame.id))
            ).scalar() or 0

            total_aliases = session.execute(
                select(sql_func.count(CatalogAlias.id))
            ).scalar() or 0

            games_with_cover = session.execute(
                select(sql_func.count(CatalogGame.id)).where(
                    CatalogGame.cover_url.isnot(None)
                )
            ).scalar() or 0

            return {
                "total_games": total_games,
                "total_aliases": total_aliases,
                "games_with_cover": games_with_cover,
            }
