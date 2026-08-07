"""Catalog FastAPI application factory and background sync scheduler.

The catalog service runs as a separate process: `gpcg catalog`
(which calls uvicorn with this app factory).

On startup:
  1. Initialize the catalog DB (create tables if needed).
  2. Start a background thread that:
     a. If no sync has ever been done → run a full sync immediately.
     b. Then loop: sleep (sync_interval ± 10% jitter), wake up and run
        an incremental sync.
  3. Serve the FastAPI app on port 8788.

The scheduler is a simple background thread — no APScheduler dependency.
The thread is daemon=True so it dies with the process (Docker stop/restart).
"""

from __future__ import annotations

import logging
import random
import threading
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gpcg.catalog.database import init_catalog_db
from gpcg.catalog.routes import admin_router, router
from gpcg.catalog.sync_service import SyncService
from gpcg.config import get_settings
from gpcg.logging import configure_logging

log = logging.getLogger(__name__)

# ── Background sync state ─────────────────────────────────────────────────────

_sync_thread: Optional[threading.Thread] = None
_sync_stop_event = threading.Event()
_manual_sync_event = threading.Event()
_manual_sync_full = False


def trigger_background_sync(full: bool = False) -> None:
    """Trigger a manual sync. The background thread picks it up.

    If a sync is already running, the trigger is ignored (the current
    sync will complete and the next scheduled one will pick up changes).
    """
    global _manual_sync_full
    _manual_sync_full = full
    _manual_sync_event.set()


def _sync_worker() -> None:
    """Background sync thread main loop.

    1. On first run, if no sync has ever been done → full sync.
    2. Then loop: wait for either:
       a. Manual sync trigger (_manual_sync_event)
       b. Scheduled interval (with jitter)
    3. Run incremental sync (or full if manually triggered with full=true).
    """
    settings = get_settings()
    sync_service = SyncService()

    # ── Initial sync ────────────────────────────────────────────────────────
    try:
        status = sync_service.get_status()
        if status.get("last_full_sync") is None:
            log.info("No previous sync found, starting initial full sync...")
            sync_service.run_full_sync()
        else:
            log.info(
                "Catalog already synced (last: %s), skipping initial sync",
                status.get("last_full_sync"),
            )
    except Exception as e:
        log.error("Initial sync failed: %s", e)
        # Continue anyway — the service can serve stale data and retry later.

    # ── Scheduled sync loop ─────────────────────────────────────────────────
    while not _sync_stop_event.is_set():
        # Calculate sleep duration with ±10% jitter
        base_interval = settings.catalog_sync_interval_sec
        jitter = random.uniform(-0.1, 0.1) * base_interval
        sleep_duration = base_interval + jitter

        # Wait for either the timeout (scheduled sync) or manual trigger
        triggered = _manual_sync_event.wait(timeout=sleep_duration)

        if _sync_stop_event.is_set():
            break

        if triggered:
            _manual_sync_event.clear()
            full = _manual_sync_full
            log.info("Manual sync triggered (full=%s)", full)
        else:
            full = False
            log.info("Scheduled sync triggered (incremental)")

        try:
            if full:
                sync_service.run_full_sync()
            else:
                sync_service.run_incremental_sync()
        except Exception as e:
            log.error("Sync failed: %s", e)


def _start_sync_thread() -> None:
    """Start the background sync thread (daemon)."""
    global _sync_thread
    if _sync_thread is not None and _sync_thread.is_alive():
        return
    _sync_stop_event.clear()
    _sync_thread = threading.Thread(target=_sync_worker, daemon=True, name="catalog-sync")
    _sync_thread.start()
    log.info("Background sync thread started")


def _stop_sync_thread() -> None:
    """Stop the background sync thread (graceful shutdown)."""
    global _sync_thread
    _sync_stop_event.set()
    _manual_sync_event.set()  # Wake up the thread if it's waiting
    if _sync_thread is not None:
        _sync_thread.join(timeout=10)
        _sync_thread = None


# ── FastAPI app factory ───────────────────────────────────────────────────────


def create_catalog_app() -> FastAPI:
    """Create the catalog FastAPI application.

    Called by uvicorn: `uvicorn gpcg.catalog.app:create_catalog_app --factory`
    """
    configure_logging()
    settings = get_settings()

    # Initialize catalog DB before creating the app (tables must exist for routes)
    init_catalog_db()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Start background sync thread
        _start_sync_thread()
        yield
        # Stop sync thread on shutdown
        _stop_sync_thread()

    app = FastAPI(
        title="GPCG Game Catalog Service",
        description="IGDB-synced game catalog for the Gameplay Content Generator",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS — allow the GPCG API to call us (same origin in dev, Docker in prod)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Internal service — no auth, no origin restriction
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # Routes
    app.include_router(router)
    app.include_router(admin_router)

    # Root health (no /api prefix)
    @app.get("/health")
    def root_health():
        return {"status": "ok", "service": "gpcg-catalog"}

    return app
