"""App distribution routes — serves mobile APK version info and download.

Enables self-hosted app distribution: the mobile app checks /api/app/version
on startup and shows an update banner if a newer version is available.
The APK is stored on the VPS and served via /api/app/download.

Version info is read from the `app_releases` table (latest row by version_code).
The APK file is stored at data/app/gpcg-latest.apk.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import FileResponse
from sqlalchemy import desc

from gpcg.config import PROJECT_ROOT
from gpcg.core.models import AppRelease
from gpcg.infrastructure.database import session_scope

router = APIRouter(tags=["app"])

# Where the APK is stored
APP_RELEASE_DIR = PROJECT_ROOT / "data" / "app"
APK_PATH = APP_RELEASE_DIR / "gpcg-latest.apk"


def _get_latest_release() -> AppRelease | None:
    """Get the latest app release from the database."""
    with session_scope() as session:
        return (
            session.query(AppRelease)
            .order_by(desc(AppRelease.version_code))
            .first()
        )


@router.get("/app/version")
async def get_app_version():
    """Public endpoint — returns latest mobile app version info.

    No auth required so the app can check for updates before login.
    Reads from the app_releases table (latest row by version_code).
    """
    release = _get_latest_release()
    if release is None:
        return {
            "available": False,
            "version": None,
            "versionCode": None,
            "download_url": None,
            "released_at": None,
            "changelog": None,
            "size_bytes": None,
        }

    return {
        "available": True,
        "version": release.version,
        "versionCode": release.version_code,
        "download_url": "/api/app/download",
        "released_at": release.released_at.isoformat() if release.released_at else None,
        "changelog": release.changelog,
        "size_bytes": release.size_bytes,
    }


@router.get("/app/download")
async def download_app():
    """Public endpoint — downloads the latest APK.

    No auth required so users can download without logging in.
    Returns the APK file with proper headers for Android installation.
    """
    if not APK_PATH.exists():
        raise HTTPException(status_code=404, detail="Nenhum APK disponível")

    release = _get_latest_release()
    version = release.version if release else "unknown"

    return FileResponse(
        path=str(APK_PATH),
        media_type="application/vnd.android.package-archive",
        filename=f"gpcg-{version}.apk",
        headers={
            "Content-Disposition": f'attachment; filename="gpcg-{version}.apk"',
            "X-App-Version": str(version),
        },
    )


@router.post("/app/release")
async def register_release(
    version: str,
    version_code: int,
    changelog: str | None = None,
    size_bytes: int | None = None,
    deployed_by: str | None = None,
    x_worker_key: str | None = Header(default=None, alias="X-Worker-Key"),
):
    """Register a new app release in the database.

    Called by deploy.sh after uploading a new APK. Uses the worker API key
    for authentication (same as worker endpoints — not user auth).
    """
    from gpcg.config import get_settings

    settings = get_settings()
    expected_key = settings.gpcg_worker_api_key
    if not expected_key or x_worker_key != expected_key:
        raise HTTPException(status_code=403, detail="Unauthorized")

    with session_scope() as session:
        # Check if this version_code already exists (idempotent)
        existing = (
            session.query(AppRelease)
            .filter(AppRelease.version_code == version_code)
            .first()
        )
        if existing:
            # Update existing row (e.g. re-deploy of same version)
            existing.version = version
            existing.changelog = changelog
            existing.size_bytes = size_bytes
            existing.deployed_by = deployed_by
        else:
            release = AppRelease(
                version=version,
                version_code=version_code,
                changelog=changelog,
                size_bytes=size_bytes,
                deployed_by=deployed_by,
            )
            session.add(release)

    return {"status": "ok", "version": version, "version_code": version_code}
