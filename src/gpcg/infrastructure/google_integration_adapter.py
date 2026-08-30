"""Adapter for the google-integration service (YouTube/Drive uploads).

Calls the Fastify API exposed by the google-integration service to upload
generated videos to YouTube. The service handles OAuth token management,
BullMQ queueing, and retry logic — GPCG just enqueues the job and polls
for completion.

Configuration (via Settings):
    gpcg_youtube_upload_enabled      — master switch
    gpcg_google_integration_url      — base URL (e.g. http://localhost:3200)
    gpcg_google_integration_secret   — INTERNAL_API_SECRET shared secret
    gpcg_youtube_user_id             — user ID in the google-integration DB
    gpcg_youtube_privacy             — public | private | unlisted
    gpcg_youtube_category_id         — YouTube category (20 = Gaming)
    gpcg_youtube_default_tags        — comma-separated default tags
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from gpcg.config import Settings, get_settings
from gpcg.logging import get_logger

log = get_logger(__name__)


@dataclass
class UploadResult:
    """Result of a YouTube upload attempt."""
    success: bool
    job_id: Optional[str] = None
    youtube_video_id: Optional[str] = None
    youtube_url: Optional[str] = None
    error: Optional[str] = None


class GoogleIntegrationAdapter:
    """Thin HTTP client for the google-integration service."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.base_url = self.settings.gpcg_google_integration_url.rstrip("/")
        self.secret = self.settings.gpcg_google_integration_secret
        self._headers = {
            "Content-Type": "application/json",
            "X-Internal-API-Secret": self.secret,
        }

    def _default_tags(self) -> list[str]:
        raw = self.settings.gpcg_youtube_default_tags
        return [t.strip() for t in raw.split(",") if t.strip()]

    def upload_to_youtube(
        self,
        video_path: Path | str,
        *,
        title: str,
        description: str,
        tags: Optional[list[str]] = None,
        user_id: Optional[int] = None,
        privacy: Optional[str] = None,
        category_id: Optional[int] = None,
        thumbnail_path: Optional[str] = None,
        language: str = "pt-BR",
    ) -> UploadResult:
        """Enqueue a YouTube upload and wait for completion.

        Calls POST /api/upload/youtube to enqueue, then polls
        GET /api/upload/status/:jobId until the BullMQ job completes.

        Note: video_path is the path on the GPCG container (/app/data/videos/...).
        The google-integration container mounts the gpcg-data volume at
        /app/gpcg-data, so we translate the path accordingly.
        """
        video_path_str = str(video_path)
        # Translate /app/data/... (gpcg-api mount) → /app/gpcg-data/... (google-integration mount)
        if video_path_str.startswith("/app/data/"):
            remote_path = "/app/gpcg-data/" + video_path_str[len("/app/data/"):]
        else:
            remote_path = video_path_str

        # REFACTORY_V2: user_id is mandatory — never fall back to a global default.
        # The global gpcg_youtube_user_id was a single-user-era fallback that
        # could cause cross-user publication if a caller forgot to pass user_id.
        # Now we fail explicitly instead of silently publishing to the wrong channel.
        if user_id is None:
            raise ValueError(
                "upload_to_youtube requires user_id — global fallback removed "
                "for multi-user safety (REFACTORY_V2)"
            )
        uid = user_id
        priv = privacy or self.settings.gpcg_youtube_privacy
        cat = str(category_id or self.settings.gpcg_youtube_category_id)
        all_tags = list(dict.fromkeys((tags or []) + self._default_tags()))

        body = {
            "userId": uid,
            "videoPath": remote_path,
            "title": title,
            "description": description,
            "tags": all_tags,
            "categoryId": cat,
            "privacy": priv,
            "language": language,
        }
        # Presentation Layer: include thumbnail if available
        if thumbnail_path:
            # Translate path for google-integration mount (same as videoPath)
            tp = str(thumbnail_path)
            if tp.startswith("/app/data/"):
                body["thumbnailPath"] = "/app/gpcg-data/" + tp[len("/app/data/"):]
            else:
                body["thumbnailPath"] = tp

        log.info(f"Enqueuing YouTube upload: {title} ({video_path})")
        try:
            resp = requests.post(
                f"{self.base_url}/api/upload/youtube",
                json=body,
                headers=self._headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            return UploadResult(success=False, error=f"enqueue failed: {e}")

        if not data.get("success"):
            return UploadResult(
                success=False,
                error=data.get("error", "enqueue returned success=false"),
            )

        job_id = data.get("jobId")
        if not job_id:
            return UploadResult(success=False, error="no jobId returned")

        log.info(f"YouTube upload enqueued: job {job_id}")
        return self._poll_completion(job_id)

    def _poll_completion(
        self, job_id: str, *, timeout: int = 600, interval: int = 5
    ) -> UploadResult:
        """Poll BullMQ job status until completed/failed or timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = requests.get(
                    f"{self.base_url}/api/upload/status/{job_id}",
                    headers=self._headers,
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                log.warning(f"status poll failed: {e}")
                time.sleep(interval)
                continue

            state = data.get("state", "unknown")
            result = data.get("result") or {}
            # Provider returns { id, url, provider } — id is the YouTube video ID
            yt_id = result.get("id") or result.get("youtubeVideoId")
            failed_reason = data.get("failedReason")

            if state == "completed" and yt_id:
                log.info(f"YouTube upload completed: {yt_id}")
                return UploadResult(
                    success=True,
                    job_id=job_id,
                    youtube_video_id=yt_id,
                    youtube_url=f"https://youtu.be/{yt_id}",
                )

            if state == "failed":
                return UploadResult(
                    success=False,
                    job_id=job_id,
                    error=failed_reason or "upload failed (no reason)",
                )

            log.debug(f"Upload job {job_id} state: {state}")
            time.sleep(interval)

        return UploadResult(
            success=False,
            job_id=job_id,
            error=f"timed out after {timeout}s (last state: {state})",
        )

    def get_oauth_url(self, user_id: int) -> str:
        """Get the Google OAuth URL for a user to connect their YouTube channel.

        The user's browser will be redirected to this URL to authorize.
        """
        try:
            resp = requests.get(
                f"{self.base_url}/api/youtube/auth/google/url",
                params={"user_id": user_id},
                headers=self._headers,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("url", "")
        except requests.RequestException as e:
            log.warning(f"get_oauth_url failed: {e}")
            return ""

    def get_auth_status(self, user_id: int) -> dict:
        """Check if a user has connected their YouTube channel.

        Returns {"connected": bool, "channelId": str, "channelTitle": str, ...}
        """
        try:
            resp = requests.get(
                f"{self.base_url}/api/youtube/auth/status",
                params={"user_id": user_id},
                headers=self._headers,
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            log.warning(f"get_auth_status failed: {e}")
            return {"connected": False, "error": str(e)}

    def revoke_auth(self, user_id: int) -> bool:
        """Revoke a user's YouTube OAuth access."""
        try:
            resp = requests.post(
                f"{self.base_url}/api/youtube/auth/revoke",
                json={"user_id": user_id},
                headers=self._headers,
                timeout=15,
            )
            resp.raise_for_status()
            return True
        except requests.RequestException as e:
            log.warning(f"revoke_auth failed: {e}")
            return False
