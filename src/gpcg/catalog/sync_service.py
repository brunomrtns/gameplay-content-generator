"""Catalog sync service — full and incremental sync from IGDB.

Full sync:
  - Fetches all popular games from IGDB (filtered by rating/count/hypes/year).
  - Paginates with offset/limit until exhausted.
  - Upserts each game and its aliases into the catalog DB.
  - Runs on first startup (if catalog is empty) and can be triggered manually.

Incremental sync:
  - Fetches only games with updated_at > last_igdb_updated_at.
  - Same popularity filter applies (we don't sync unpopular games that
    happened to get a metadata update).
  - Runs on a scheduled interval (default: 24h with ±10% jitter).

Both syncs are idempotent — running them multiple times produces the same
result. Upserts use merge() on the IGDB ID (primary key).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select

from gpcg.catalog.database import session_scope
from gpcg.catalog.igdb_client import IGDBClient, IGDBGame
from gpcg.catalog.models import CatalogAlias, CatalogGame, SyncState

log = logging.getLogger(__name__)


class SyncService:
    """Orchestrates full and incremental syncs from IGDB to the catalog DB."""

    def __init__(self, client: Optional[IGDBClient] = None) -> None:
        self._client = client

    @property
    def client(self) -> IGDBClient:
        if self._client is None:
            self._client = IGDBClient()
        return self._client

    # ── Public API ──────────────────────────────────────────────────────────

    def run_full_sync(self) -> dict:
        """Run a full sync from IGDB.

        Fetches all popular games and upserts them into the catalog DB.
        Returns a summary dict with stats.
        """
        return self._run_sync(updated_after=None, is_full=True)

    def run_incremental_sync(self) -> dict:
        """Run an incremental sync from IGDB.

        Fetches only games updated since the last sync. Returns a summary
        dict with stats.
        """
        last_updated = self._get_last_igdb_updated_at()
        if last_updated is None:
            log.info("No previous sync found, running full sync instead")
            return self.run_full_sync()
        return self._run_sync(updated_after=last_updated, is_full=False)

    def get_status(self) -> dict:
        """Get current sync status."""
        with session_scope() as session:
            state = session.get(SyncState, 1)
            if state is None:
                return {
                    "initialized": False,
                    "sync_in_progress": False,
                    "total_games": 0,
                    "last_full_sync": None,
                    "last_incremental_sync": None,
                    "last_igdb_updated_at": None,
                    "last_error": None,
                }
            return {
                "initialized": True,
                "sync_in_progress": state.sync_in_progress,
                "total_games": state.total_games,
                "last_full_sync": state.last_full_sync,
                "last_incremental_sync": state.last_incremental_sync,
                "last_igdb_updated_at": state.last_igdb_updated_at,
                "last_error": state.last_sync_error,
                "last_error_at": state.last_error_at.isoformat() if state.last_error_at else None,
            }

    # ── Internal sync logic ─────────────────────────────────────────────────

    def _run_sync(self, *, updated_after: Optional[int], is_full: bool) -> dict:
        """Core sync loop. Acquires the sync lock, paginates through IGDB,
        upserts each batch, and updates sync_state.
        """
        if not self._acquire_sync_lock():
            log.warning("Sync already in progress, skipping")
            return {"skipped": True, "reason": "sync_in_progress"}

        sync_type = "full" if is_full else "incremental"
        log.info("Starting %s sync from IGDB...", sync_type)

        stats = {
            "type": sync_type,
            "fetched": 0,
            "created": 0,
            "updated": 0,
            "aliases_added": 0,
            "errors": 0,
            "duration_sec": 0.0,
        }
        start_time = time.time()

        try:
            # Get total count for progress logging
            total = self.client.fetch_game_count(updated_after=updated_after)
            log.info("IGDB reports %d games to sync", total)

            offset = 0
            batch_size = 500  # IGDB max page size
            max_igdb_updated_at = updated_after or 0

            while True:
                games = self.client.fetch_games(
                    offset=offset,
                    limit=batch_size,
                    updated_after=updated_after,
                    sort_by="updated_at asc" if is_full else "updated_at asc",
                )
                if not games:
                    break

                batch_stats = self._upsert_batch(games)
                stats["fetched"] += len(games)
                stats["created"] += batch_stats["created"]
                stats["updated"] += batch_stats["updated"]
                stats["aliases_added"] += batch_stats["aliases_added"]
                stats["errors"] += batch_stats["errors"]

                # Track the highest updated_at for next incremental sync
                for game in games:
                    if game.updated_at and game.updated_at > max_igdb_updated_at:
                        max_igdb_updated_at = game.updated_at

                log.info(
                    "Synced batch %d-%d (%d games, %d created, %d updated, %d aliases)",
                    offset,
                    offset + len(games) - 1,
                    len(games),
                    batch_stats["created"],
                    batch_stats["updated"],
                    batch_stats["aliases_added"],
                )

                offset += len(games)
                if len(games) < batch_size:
                    break  # Last page

            # Update sync state
            self._update_sync_state(
                is_full=is_full,
                max_igdb_updated_at=max_igdb_updated_at,
                error=None,
            )

            stats["duration_sec"] = round(time.time() - start_time, 2)
            log.info(
                "%s sync complete: %d fetched, %d created, %d updated, %d aliases, "
                "%d errors, %.1fs",
                sync_type.capitalize(),
                stats["fetched"],
                stats["created"],
                stats["updated"],
                stats["aliases_added"],
                stats["errors"],
                stats["duration_sec"],
            )
            return stats

        except Exception as e:
            log.exception("%s sync failed", sync_type.capitalize())
            self._update_sync_state(is_full=is_full, max_igdb_updated_at=None, error=str(e))
            stats["error"] = str(e)
            stats["duration_sec"] = round(time.time() - start_time, 2)
            return stats

        finally:
            self._release_sync_lock()

    def _upsert_batch(self, games: list[IGDBGame]) -> dict:
        """Upsert a batch of IGDB games into the catalog DB.

        For each game:
        1. Check if a CatalogGame with this IGDB ID exists.
        2. If yes: update all fields.
        3. If no: create a new CatalogGame.
        4. Sync aliases: add new ones, don't remove old ones (aliases are
           additive — IGDB might not return all aliases every time).
        """
        stats = {"created": 0, "updated": 0, "aliases_added": 0, "errors": 0}

        with session_scope() as session:
            for igdb_game in games:
                try:
                    existing = session.get(CatalogGame, igdb_game.id)

                    if existing is None:
                        # Create new
                        catalog_game = self._igdb_to_catalog_game(igdb_game)
                        session.add(catalog_game)
                        session.flush()
                        stats["created"] += 1
                        game_id = catalog_game.id
                    else:
                        # Update existing
                        self._update_catalog_game(existing, igdb_game)
                        stats["updated"] += 1
                        game_id = existing.id

                    # Sync aliases (additive)
                    if igdb_game.alternative_names:
                        added = self._sync_aliases(session, game_id, igdb_game.alternative_names)
                        stats["aliases_added"] += added

                except Exception as e:
                    log.error("Failed to upsert IGDB game %d (%s): %s", igdb_game.id, igdb_game.name, e)
                    stats["errors"] += 1

        return stats

    def _igdb_to_catalog_game(self, igdb: IGDBGame) -> CatalogGame:
        """Convert an IGDBGame dataclass to a CatalogGame ORM instance."""
        now_ts = int(datetime.now(timezone.utc).timestamp())
        return CatalogGame(
            id=igdb.id,
            name=igdb.name,
            slug=igdb.slug,
            summary=igdb.summary,
            first_release_date=igdb.first_release_date,
            total_rating=igdb.total_rating,
            total_rating_count=igdb.total_rating_count,
            cover_url=igdb.cover_url,
            screenshots=igdb.screenshot_urls,
            genres=igdb.genres,
            platforms=igdb.platforms,
            developer=igdb.developer,
            igdb_updated_at=igdb.updated_at,
            synced_at=now_ts,
        )

    def _update_catalog_game(self, existing: CatalogGame, igdb: IGDBGame) -> None:
        """Update an existing CatalogGame with new IGDB data."""
        existing.name = igdb.name
        existing.slug = igdb.slug
        existing.summary = igdb.summary
        existing.first_release_date = igdb.first_release_date
        existing.total_rating = igdb.total_rating
        existing.total_rating_count = igdb.total_rating_count
        existing.cover_url = igdb.cover_url
        existing.screenshots = igdb.screenshot_urls
        existing.genres = igdb.genres
        existing.platforms = igdb.platforms
        existing.developer = igdb.developer
        existing.igdb_updated_at = igdb.updated_at
        existing.synced_at = int(datetime.now(timezone.utc).timestamp())

    def _sync_aliases(
        self,
        session,
        game_id: int,
        aliases: list[str],
    ) -> int:
        """Add new aliases for a game. Returns count of aliases added.

        Aliases are additive — we never remove aliases that IGDB doesn't
        return anymore, because they might have been returned in a previous
        sync and are still valid alternative names.
        """
        added = 0
        for alias in aliases:
            alias = alias.strip()
            if not alias:
                continue
            # Check if this alias already exists for this game
            exists = session.execute(
                select(CatalogAlias.id).where(
                    CatalogAlias.game_id == game_id,
                    func.lower(CatalogAlias.alias) == alias.lower(),
                )
            ).first()
            if exists:
                continue
            session.add(CatalogAlias(
                game_id=game_id,
                alias=alias,
                alias_type="alternative",
                source="igdb",
            ))
            added += 1
        return added

    # ── Sync state management ───────────────────────────────────────────────

    def _acquire_sync_lock(self) -> bool:
        """Try to acquire the sync lock. Returns True if acquired."""
        with session_scope() as session:
            state = session.get(SyncState, 1)
            if state is None:
                state = SyncState(id=1)
                session.add(state)
                session.flush()
            if state.sync_in_progress:
                return False
            state.sync_in_progress = True
            return True

    def _release_sync_lock(self) -> None:
        """Release the sync lock."""
        with session_scope() as session:
            state = session.get(SyncState, 1)
            if state is not None:
                state.sync_in_progress = False

    def _update_sync_state(
        self,
        *,
        is_full: bool,
        max_igdb_updated_at: Optional[int],
        error: Optional[str],
    ) -> None:
        """Update sync state after a sync completes (success or failure)."""
        now_ts = int(datetime.now(timezone.utc).timestamp())
        with session_scope() as session:
            state = session.get(SyncState, 1)
            if state is None:
                state = SyncState(id=1)
                session.add(state)
                session.flush()

            if error is not None:
                state.last_sync_error = error
                state.last_error_at = datetime.now(timezone.utc)
            else:
                state.last_sync_error = None
                state.last_error_at = None

            if is_full:
                state.last_full_sync = now_ts
            else:
                state.last_incremental_sync = now_ts

            if max_igdb_updated_at is not None:
                state.last_igdb_updated_at = max_igdb_updated_at

            # Update total games count
            total = session.execute(
                select(func.count(CatalogGame.id))
            ).scalar()
            state.total_games = total or 0

    def _get_last_igdb_updated_at(self) -> Optional[int]:
        """Get the highest IGDB updated_at timestamp from the last sync."""
        with session_scope() as session:
            state = session.get(SyncState, 1)
            if state is None:
                return None
            return state.last_igdb_updated_at
