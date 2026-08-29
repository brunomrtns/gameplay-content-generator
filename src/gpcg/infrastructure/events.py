"""Event publisher — thin wrapper over Redis pub/sub for GPCG events.

Centralizes event channel naming and payload construction so that
backend code doesn't need to know Redis details.

Usage:
    from gpcg.infrastructure.events import publish_user_event, publish_global_event

    publish_user_event(user_id, "job.status_changed", {"job_id": 42, "status": "running"})
    publish_global_event("workers", "worker.status_changed", {"worker_id": "home-pc"})

All calls are safe when Redis is down — they silently no-op.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from gpcg.infrastructure.redis_adapter import get_redis

log = logging.getLogger(__name__)


def _channel_user(user_id: Optional[int]) -> str:
    return f"user:{user_id}:events"


def _channel_global(name: str) -> str:
    return f"global:{name}"


def publish_user_event(user_id: Optional[int], event_type: str, payload: dict) -> bool:
    """Publish an event to a user's personal channel.

    If user_id is None, the event is dropped (no channel to publish to).
    This is intentional — global events use publish_global_event.
    """
    if user_id is None:
        log.debug(f"publish_user_event: dropping {event_type} (user_id is None)")
        return False
    redis = get_redis()
    channel = _channel_user(user_id)
    ok = redis.publish(channel, event_type, payload)
    if not ok:
        log.debug(f"publish_user_event: Redis unavailable, dropped {event_type} for user {user_id}")
    # Invalidate relevant caches
    try:
        from gpcg.infrastructure.cache import invalidate_caches_for_event
        invalidate_caches_for_event(event_type, user_id)
    except Exception:
        pass
    return ok


def publish_global_event(channel_name: str, event_type: str, payload: dict) -> bool:
    """Publish an event to a global channel (e.g. 'workers', 'games')."""
    redis = get_redis()
    channel = _channel_global(channel_name)
    ok = redis.publish(channel, event_type, payload)
    if not ok:
        log.debug(f"publish_global_event: Redis unavailable, dropped {event_type} for {channel_name}")
    # Invalidate relevant caches (global — invalidate all matching patterns)
    try:
        from gpcg.infrastructure.cache import invalidate_caches_for_event
        invalidate_caches_for_event(event_type, None)
    except Exception:
        pass
    return ok


# ── Convenience helpers for common event types ──────────────────────────────


def publish_job_status_changed(
    user_id: Optional[int],
    job_id: int,
    status: str,
    stage: str = "",
    progress: float = 0.0,
    job_type: str = "",
) -> bool:
    return publish_user_event(user_id, "job.status_changed", {
        "job_id": job_id,
        "status": status,
        "stage": stage,
        "progress": progress,
        "type": job_type,
        "user_id": user_id,
    })


def publish_job_created(
    user_id: Optional[int],
    job_id: int,
    job_type: str,
    priority: str = "normal",
) -> bool:
    return publish_user_event(user_id, "job.created", {
        "job_id": job_id,
        "type": job_type,
        "priority": priority,
        "user_id": user_id,
    })


def publish_gameplay_status_changed(
    user_id: Optional[int],
    source_id: int,
    processing_status: str,
    filename: str = "",
) -> bool:
    return publish_user_event(user_id, "gameplay.status_changed", {
        "source_id": source_id,
        "processing_status": processing_status,
        "filename": filename,
        "user_id": user_id,
    })


def publish_video_created(
    user_id: Optional[int],
    video_id: int,
    title: str = "",
    status: str = "",
) -> bool:
    return publish_user_event(user_id, "video.created", {
        "video_id": video_id,
        "title": title,
        "status": status,
        "user_id": user_id,
    })


def publish_video_updated(
    user_id: Optional[int],
    video_id: int,
    status: str = "",
    youtube_url: str = "",
) -> bool:
    return publish_user_event(user_id, "video.updated", {
        "video_id": video_id,
        "status": status,
        "youtube_url": youtube_url,
        "user_id": user_id,
    })


def publish_worker_status_changed(
    worker_id: str,
    status: str,
    activity: str = "",
    gpu_usage: Any = None,
    cpu_usage: Any = None,
) -> bool:
    return publish_global_event("workers", "worker.status_changed", {
        "worker_id": worker_id,
        "status": status,
        "activity": activity,
        "gpu_usage": gpu_usage,
        "cpu_usage": cpu_usage,
    })


def publish_automation_status_changed(
    user_id: Optional[int],
    automation_id: Optional[int],
    status: str,
) -> bool:
    return publish_user_event(user_id, "automation.status_changed", {
        "automation_id": automation_id,
        "status": status,
        "user_id": user_id,
    })


def publish_idea_queue_updated(user_id: Optional[int], queue_size: int = 0) -> bool:
    return publish_user_event(user_id, "idea_queue.updated", {
        "user_id": user_id,
        "queue_size": queue_size,
    })


def publish_kids_idea_updated(
    user_id: Optional[int],
    idea_id: int,
    status: str,
) -> bool:
    return publish_user_event(user_id, "kids_idea.updated", {
        "idea_id": idea_id,
        "status": status,
        "user_id": user_id,
    })


def publish_game_enriched(game_id: int, canonical_name: str = "") -> bool:
    return publish_global_event("games", "game.enriched", {
        "game_id": game_id,
        "canonical_name": canonical_name,
    })
