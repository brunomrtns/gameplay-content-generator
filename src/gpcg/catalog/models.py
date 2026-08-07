"""Catalog SQLAlchemy models.

Three tables, all in the catalog.db (separate from gpcg.db):

  catalog_games   — one row per IGDB game (IGDB ID is the PK)
  catalog_aliases — alternative names for games (from IGDB alternative_names)
  sync_state      — singleton row tracking sync progress

The catalog DB is write-only during sync (background thread) and read-only
during normal operation (query API). SQLite WAL mode ensures readers
never block writers and vice versa.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from gpcg.catalog.database import CatalogBase


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_ts() -> int:
    """Unix timestamp (seconds) for IGDB compatibility."""
    return int(_utcnow().timestamp())


class CatalogGame(CatalogBase):
    """A game entry synced from IGDB.

    The primary key is the IGDB game ID (integer). This is stable across
    syncs — IGDB IDs don't change. If a game is removed from IGDB, it
    stays in our catalog (we don't delete on sync).
    """

    __tablename__ = "catalog_games"

    # ── Identity (from IGDB) ────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)

    # ── Descriptive ──────────────────────────────────────────────────────────
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Release ──────────────────────────────────────────────────────────────
    first_release_date: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── Ratings (IGDB) ───────────────────────────────────────────────────────
    # total_rating = critics + users combined (0-100). NULL if not enough ratings.
    total_rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_rating_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── Media URLs (IGDB CDN — we store URLs, not blobs) ────────────────────
    cover_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    screenshots: Mapped[list] = mapped_column(JSON, default=list)

    # ── Taxonomy (JSON arrays of IGDB names) ────────────────────────────────
    genres: Mapped[list] = mapped_column(JSON, default=list)
    platforms: Mapped[list] = mapped_column(JSON, default=list)

    # ── People ───────────────────────────────────────────────────────────────
    developer: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # ── IGDB metadata ────────────────────────────────────────────────────────
    igdb_updated_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── Sync tracking ────────────────────────────────────────────────────────
    synced_at: Mapped[int] = mapped_column(Integer, nullable=False, default=_utcnow_ts)
    created_at: Mapped[datetime] = mapped_column(
        default=_utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )

    # ── Relationships ────────────────────────────────────────────────────────
    alias_rows: Mapped[list["CatalogAlias"]] = relationship(
        back_populates="game",
        cascade="all, delete-orphan",
    )

    @property
    def release_year(self) -> Optional[int]:
        """Extract year from first_release_date (unix timestamp)."""
        if self.first_release_date is None:
            return None
        try:
            return datetime.fromtimestamp(self.first_release_date, tz=timezone.utc).year
        except (ValueError, OSError):
            return None


class CatalogAlias(CatalogBase):
    """Alternative name for a catalog game.

    Sourced from IGDB's alternative_names endpoint. Each alias is unique
    per game (enforced by unique constraint). The lowercase index enables
    fast case-insensitive lookups during search.
    """

    __tablename__ = "catalog_aliases"
    __table_args__ = (
        UniqueConstraint("game_id", "alias", name="uq_catalog_alias_per_game"),
        Index("ix_catalog_aliases_lower", "alias"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_games.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias: Mapped[str] = mapped_column(String(200), nullable=False)
    alias_type: Mapped[str] = mapped_column(String(30), default="alternative")
    source: Mapped[str] = mapped_column(String(50), default="igdb")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)

    game: Mapped["CatalogGame"] = relationship(back_populates="alias_rows")


class SyncState(CatalogBase):
    """Singleton row tracking sync progress.

    Always has exactly one row (id=1). Tracks when the last full and
    incremental syncs ran, the highest IGDB updated_at timestamp seen
    (for incremental sync pagination), and whether a sync is currently
    in progress (prevents concurrent syncs).
    """

    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    last_full_sync: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_incremental_sync: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_igdb_updated_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_games: Mapped[int] = mapped_column(Integer, default=0)
    sync_in_progress: Mapped[bool] = mapped_column(Boolean, default=False)
    last_sync_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )
