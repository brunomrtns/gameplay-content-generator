"""App distribution routes — serves mobile APK version info and download.

Enables self-hosted app distribution: the mobile app checks /api/app/version
on startup and shows an update banner if a newer version is available.
The APK is stored on the VPS and served via /api/app/download.

The APK and version metadata are uploaded by deploy.sh after a successful
deploy. The metadata file (data/app/release.json) contains:
  {version, versionCode, download_url, released_at, changelog, size_bytes}
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from gpcg.config import PROJECT_ROOT

router = APIRouter(tags=["app"])

# Where the APK and metadata are stored
APP_RELEASE_DIR = PROJECT_ROOT / "data" / "app"
APK_PATH = APP_RELEASE_DIR / "gpcg-latest.apk"
METADATA_PATH = APP_RELEASE_DIR / "release.json"


def _read_metadata() -> dict | None:
    """Read release metadata from disk, or None if not present."""
    if not METADATA_PATH.exists():
        return None
    try:
        return json.loads(METADATA_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None


@router.get("/app/version")
async def get_app_version():
    """Public endpoint — returns latest mobile app version info.

    No auth required so the app can check for updates before login.
    """
    meta = _read_metadata()
    if meta is None:
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
        "version": meta.get("version"),
        "versionCode": meta.get("versionCode"),
        "download_url": "/api/app/download",
        "released_at": meta.get("released_at"),
        "changelog": meta.get("changelog"),
        "size_bytes": meta.get("size_bytes"),
    }


@router.get("/app/download")
async def download_app():
    """Public endpoint — downloads the latest APK.

    No auth required so users can download without logging in.
    Returns the APK file with proper headers for Android installation.
    """
    if not APK_PATH.exists():
        raise HTTPException(status_code=404, detail="Nenhum APK disponível")

    meta = _read_metadata() or {}
    version = meta.get("version", "unknown")

    return FileResponse(
        path=str(APK_PATH),
        media_type="application/vnd.android.package-archive",
        filename=f"gpcg-{version}.apk",
        headers={
            "Content-Disposition": f'attachment; filename="gpcg-{version}.apk"',
            "X-App-Version": str(version),
        },
    )
