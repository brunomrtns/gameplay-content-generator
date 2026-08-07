"""Catalog API routes — query, admin, and health endpoints.

All routes are prefixed with /api (for query) or /admin (for admin).
The GPCG API proxies /api/catalog/* to this service's /api/*.

Authentication: the catalog service has NO authentication of its own.
It's an internal service on the Docker network, not exposed publicly.
The GPCG API proxy handles auth (BI Identity SSO) before forwarding.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from gpcg.catalog.database import get_db
from gpcg.catalog.query_service import GameDetail, GameSummary, QueryService
from gpcg.catalog.sync_service import SyncService

log = logging.getLogger(__name__)

# ── Routers ───────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api", tags=["catalog"])
admin_router = APIRouter(prefix="/admin", tags=["catalog-admin"])


# ── Query endpoints ───────────────────────────────────────────────────────────


@router.get("/search")
def search(
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(20, ge=1, le=100),
):
    """Search games by name and aliases."""
    service = QueryService()
    results = service.search(q, limit=limit)
    return {"results": [r.to_dict() for r in results], "count": len(results)}


@router.get("/games/{game_id}")
def get_game(game_id: int):
    """Get full details for a game by IGDB ID."""
    service = QueryService()
    game = service.get_game(game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return game.to_dict()


@router.get("/games/slug/{slug}")
def get_by_slug(slug: str):
    """Get full details for a game by slug."""
    service = QueryService()
    game = service.get_by_slug(slug)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return game.to_dict()


@router.get("/games/popular")
def popular(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Get the most popular games by total_rating_count."""
    service = QueryService()
    results = service.popular(limit=limit, offset=offset)
    return {"results": [r.to_dict() for r in results], "count": len(results)}


@router.get("/games/recent")
def recent(limit: int = Query(50, ge=1, le=200)):
    """Get recently released games (newest first)."""
    service = QueryService()
    results = service.recent(limit=limit)
    return {"results": [r.to_dict() for r in results], "count": len(results)}


@router.get("/autocomplete")
def autocomplete(
    q: str = Query(..., min_length=2, description="Partial game name"),
    limit: int = Query(10, ge=1, le=50),
):
    """Fast autocomplete for UI search boxes."""
    service = QueryService()
    results = service.autocomplete(q, limit=limit)
    return {"results": [r.to_dict() for r in results], "count": len(results)}


# ── Admin endpoints ───────────────────────────────────────────────────────────


@admin_router.post("/sync")
def trigger_sync(full: bool = False):
    """Trigger a sync manually.

    By default runs an incremental sync. Pass full=true for a full sync.
    Returns immediately — the sync runs in the background. Check status
    with GET /admin/sync/status.
    """
    from gpcg.catalog.app import trigger_background_sync

    sync_type = "full" if full else "incremental"
    trigger_background_sync(full=full)
    return {"status": "triggered", "type": sync_type}


@admin_router.get("/sync/status")
def sync_status():
    """Get current sync status."""
    service = SyncService()
    return service.get_status()


@admin_router.get("/stats")
def stats():
    """Get catalog statistics."""
    query_service = QueryService()
    sync_service = SyncService()
    return {
        **query_service.stats(),
        "sync": sync_service.get_status(),
    }


# ── Health ────────────────────────────────────────────────────────────────────


@router.get("/health")
def health():
    """Liveness probe. Returns ok if the service is up and DB is accessible."""
    try:
        # Quick DB check
        from sqlalchemy import text
        from gpcg.catalog.database import get_engine

        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "service": "gpcg-catalog"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database error: {e}")
