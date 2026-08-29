"""FastAPI application factory — serves the API and (in prod) the built frontend."""

from __future__ import annotations

import logging
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.requests import Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)

from gpcg.api import routes
from gpcg.api.auth_routes import router as auth_router
from gpcg.api.automation_routes import router as automation_router
from gpcg.api.workers import router as worker_router
from gpcg.api.upload_routes import router as upload_router
from gpcg.api.knowledge_routes import router as knowledge_router
from gpcg.api.game_registry_routes import router as game_registry_router
from gpcg.api.knowledge_item_routes import router as knowledge_item_router
from gpcg.api.kids_routes import router as kids_router
from gpcg.api.kids_idea_routes import router as kids_idea_router
from gpcg.api.events_routes import router as events_router
from gpcg.api.app_routes import router as app_router
from gpcg.config import PROJECT_ROOT, get_settings
from gpcg.infrastructure.database import init_db


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Start Redis Streams reconciler as a background thread
        import threading
        import time
        from gpcg.infrastructure.job_queue import reconcile_streams
        from gpcg.infrastructure.redis_adapter import get_redis

        def _reconciler_loop():
            while True:
                try:
                    redis = get_redis()
                    if redis.is_available():
                        reconcile_streams()
                except Exception as e:
                    log.warning(f"reconciler: error: {e}")
                time.sleep(settings.redis_reconciler_interval)

        thread = threading.Thread(target=_reconciler_loop, daemon=True, name="redis-reconciler")
        thread.start()
        log.info("Redis Streams reconciler started")
        yield

    app = FastAPI(
        title="Gameplay Content Generator",
        version="0.2.0",
        description="Multi-user gameplay to YouTube Shorts automation platform",
        lifespan=lifespan,
    )

    # Initialize database (creates tables, adds columns, seeds admin user)
    init_db()

    # CORS for dev (frontend on :5173) and production
    origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        f"http://{settings.gpcg_host}:{settings.gpcg_port}",
        "https://brunointegrations.com",
        "https://www.brunointegrations.com",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routes
    app.include_router(auth_router, prefix="/api")
    app.include_router(automation_router, prefix="/api")
    app.include_router(worker_router, prefix="/api")
    app.include_router(upload_router, prefix="/api")
    app.include_router(knowledge_router, prefix="/api")
    app.include_router(game_registry_router, prefix="/api")
    app.include_router(knowledge_item_router, prefix="/api")
    app.include_router(kids_router, prefix="/api")
    app.include_router(kids_idea_router, prefix="/api")
    app.include_router(events_router, prefix="/api")
    app.include_router(app_router, prefix="/api")
    app.include_router(routes.router, prefix="/api")

    # Health
    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "gpcg", "version": "0.2.0"}

    # Serve built frontend in production
    frontend_dist = PROJECT_ROOT / "frontend" / "dist"
    if frontend_dist.exists():
        index_html = frontend_dist / "index.html"

        # Serve static assets (js, css, images, etc.) with long cache
        app.mount(
            "/assets",
            StaticFiles(directory=str(frontend_dist / "assets")),
            name="frontend-assets",
        )

        # SPA fallback: any non-API path returns index.html
        # This allows React Router to handle client-side routing
        @app.get("/{full_path:path}")
        async def spa_fallback(request: Request, full_path: str):
            # Try to serve an actual file first (favicon, icons, etc.)
            candidate = frontend_dist / full_path
            if full_path and candidate.is_file():
                return FileResponse(str(candidate))

            # Otherwise return index.html for SPA routing
            if index_html.exists():
                return FileResponse(str(index_html))

            raise HTTPException(status_code=404, detail="Not found")
    else:
        @app.get("/")
        def root():
            return {
                "name": "Gameplay Content Generator API",
                "message": "Frontend not built. Run `cd frontend && npm run build` or use dev mode.",
                "api_docs": "/docs",
            }

    return app

