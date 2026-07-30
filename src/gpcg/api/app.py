"""FastAPI application factory — serves the API and (in prod) the built frontend."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from gpcg.api import routes
from gpcg.api.auth_routes import router as auth_router
from gpcg.api.automation_routes import router as automation_router
from gpcg.config import PROJECT_ROOT, get_settings
from gpcg.infrastructure.database import init_db


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Gameplay Content Generator",
        version="0.2.0",
        description="Multi-user gameplay to YouTube Shorts automation platform",
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
    app.include_router(routes.router, prefix="/api")

    # Health
    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "gpcg", "version": "0.2.0"}

    # Serve built frontend in production
    frontend_dist = PROJECT_ROOT / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
    else:
        @app.get("/")
        def root():
            return {
                "name": "Gameplay Content Generator API",
                "message": "Frontend not built. Run `cd frontend && npm run build` or use dev mode.",
                "api_docs": "/docs",
            }

    return app

