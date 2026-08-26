"""Common building blocks for the worker API: auth, schemas, shared helpers.

This module is imported by every worker submodule. It must NOT import from any
sibling worker submodule (to avoid circular imports).
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.core.models import Worker, WorkerStatus

log = logging.getLogger(__name__)


# ── Helper: coerce JSON column values to dict ──────────────────────────────────


def _ensure_dict(value) -> dict:
    """Coerce a JSON column value to dict.

    SQLite/Postgres JSON columns normally return dicts, but older rows or
    rows written by other code paths may store a JSON string. This helper
    transparently parses strings so callers can always do dict operations.
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        import json
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            log.warning(f"_ensure_dict: could not parse JSON string: {value[:80]!r}")
            return {}
    return {}


# ── Auth dependency ────────────────────────────────────────────────────────────


def _verify_worker_key(x_worker_key: Optional[str]) -> None:
    """Validate the worker API key. Raises 401 if missing or invalid."""
    settings = get_settings()
    expected = settings.gpcg_worker_api_key
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Worker API disabled — GPCG_WORKER_API_KEY not configured",
        )
    if not x_worker_key or not secrets.compare_digest(x_worker_key, expected):
        raise HTTPException(status_code=401, detail="Invalid worker key")


def worker_auth(x_worker_key: Optional[str] = Header(None)) -> None:
    """FastAPI dependency: verify X-Worker-Key header."""
    _verify_worker_key(x_worker_key)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Request/Response schemas ─────────────────────────────────────────────────


class WorkerRegisterRequest(BaseModel):
    worker_id: str = Field(..., description="Unique worker identifier (e.g., 'home-pc')")
    hostname: str = Field("", description="Machine hostname")
    capabilities: list[str] = Field(
        default_factory=list,
        description="Worker capabilities (e.g., ['mapping', 'generation'])",
    )
    worker_version: str = Field("", description="Worker software version")
    git_commit: str = Field("", description="Git commit hash")
    build_number: str = Field("", description="Build number")
    gpu_name: str = Field("", description="GPU name (e.g., 'RTX 3060')")


class WorkerHeartbeatRequest(BaseModel):
    """Minimal — just 'I'm alive'. Sent frequently (every 10s)."""
    pass


class WorkerStatusRequest(BaseModel):
    """Full status — 'what I'm doing'. Sent on state change or periodically."""
    status: str = Field(..., description="online | busy | error")
    current_activity: str = Field("", description="Human-readable activity (e.g., 'Mapeando Bully.mp4')")
    current_job_id: Optional[int] = Field(None, description="Job currently being processed")
    gpu_usage: Optional[float] = Field(None, description="GPU usage 0-100%")
    cpu_usage: Optional[float] = Field(None, description="CPU usage 0-100%")
    ram_usage: Optional[float] = Field(None, description="RAM usage in GB")


class JobClaimRequest(BaseModel):
    worker_id: str = Field(..., description="Worker requesting a job")
    capabilities: list[str] = Field(
        default_factory=list,
        description="Capabilities the worker currently has (for matching)",
    )


class JobStatusUpdateRequest(BaseModel):
    status: str = Field(..., description="running | completed | failed | retrying")
    stage: str = Field("", description="Current pipeline stage")
    progress: float = Field(0.0, description="Progress 0.0-1.0")
    error: str = Field("", description="Error message if failed")
    artifacts: dict = Field(default_factory=dict, description="Updated artifacts")


class ConfirmDownloadRequest(BaseModel):
    worker_id: str = Field(..., description="Worker confirming the download")
    checksum: str = Field(..., description="SHA256 hash of the downloaded file")


class MappingResultRequest(BaseModel):
    """Worker sends gameplay analysis events (metadata only, no frames)."""
    events: list[dict] = Field(
        default_factory=list,
        description="GameplayEvent records (event_type, start_time, end_time, etc.)",
    )
    analysis_version: str = Field("v1", description="Analysis version tag")
    config_hash: str = Field("", description="Config hash for reprocessing detection")
    compatibility: dict = Field(
        default_factory=dict,
        description="Compatibility flags {game_related: bool, general_topic: bool}",
    )
    # Media metadata from ffprobe (synced back to GameplaySource so the VPS
    # has accurate duration/width/height without needing to probe the file)
    duration: Optional[float] = Field(None, description="File duration in seconds (from ffprobe)")
    width: Optional[int] = Field(None, description="Video width in pixels")
    height: Optional[int] = Field(None, description="Video height in pixels")
    fps: Optional[float] = Field(None, description="Frames per second")
    codec: Optional[str] = Field(None, description="Video codec name")
    has_audio: Optional[bool] = Field(None, description="Whether the file has an audio stream")


class JobResultRequest(BaseModel):
    """Worker sends final job result (video metadata or YouTube link)."""
    status: str = Field(..., description="completed | failed")
    error: str = Field("", description="Error message if failed")
    artifacts: dict = Field(default_factory=dict, description="Final artifacts")
    # Video metadata (if a video was produced)
    video: Optional[dict] = Field(
        None,
        description="Video metadata {duration, width, height, qa_score, qa_report, storage_key, youtube_url, youtube_video_id}",
    )


# ── Helper functions ─────────────────────────────────────────────────────────


def _resolve_storage_path(storage_key: str) -> Path:
    """Resolve a storage_key to a physical path on the VPS filesystem."""
    settings = get_settings()
    # storage_key is relative to temp_uploads_dir (for gameplays) or videos_dir
    # We try temp_uploads first, then videos
    temp_path = settings.temp_uploads_dir / storage_key
    if temp_path.exists():
        return temp_path
    video_path = settings.videos_dir / storage_key
    if video_path.exists():
        return video_path
    # Fallback: treat as absolute path (legacy compat)
    p = Path(storage_key)
    if p.is_absolute() and p.exists():
        return p
    return temp_path  # return expected path even if not found


def _generate_upload_token() -> str:
    """Generate a one-time download token."""
    return secrets.token_urlsafe(32)


def _check_worker_offline(worker: Worker) -> bool:
    """Check if a worker should be marked offline based on heartbeat timeout."""
    if not worker.last_heartbeat:
        return True
    settings = get_settings()
    timeout = settings.gpcg_worker_heartbeat_timeout
    # SQLite stores datetimes as naive — handle both aware and naive
    now = _utcnow().replace(tzinfo=None)
    last = worker.last_heartbeat
    if last.tzinfo:
        last = last.replace(tzinfo=None)
    elapsed = (now - last).total_seconds()
    return elapsed > timeout


_TRANSIENT_ERROR_PATTERNS = (
    "timeout",
    "timed out",
    "read operation timed out",
    "connect",
    "connection",
    "502",
    "503",
    "504",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "connection reset",
    "connection refused",
    "remote protocol error",
    "network",
)


def _is_transient_error(error: str | None) -> bool:
    """Check if an error message looks like a transient/network failure
    that warrants an automatic retry."""
    if not error:
        return False
    err_lower = error.lower()
    return any(pat in err_lower for pat in _TRANSIENT_ERROR_PATTERNS)
