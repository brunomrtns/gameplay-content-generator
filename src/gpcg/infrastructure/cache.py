"""Redis cache helpers — simple get/set with TTL and pattern invalidation.

All operations are safe when Redis is down — they return None/False
and the caller computes the value on-demand.

Usage:
    from gpcg.infrastructure.cache import cache_get, cache_set, cache_invalidate

    # Try cache
    cached = cache_get(f"dashboard:{user_id}")
    if cached is not None:
        return cached

    # Compute and cache
    result = compute_dashboard(user_id)
    cache_set(f"dashboard:{user_id}", result, ttl=10)

    # Invalidate
    cache_invalidate(f"dashboard:{user_id}")
    # Or by pattern
    cache_invalidate_pattern("dashboard:*")
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from gpcg.infrastructure.redis_adapter import get_redis

log = logging.getLogger(__name__)


def cache_get(key: str) -> Optional[Any]:
    """Get a value from cache. Returns None if missing or Redis down."""
    redis = get_redis()
    if not redis.is_available():
        return None
    return redis.get(f"cache:{key}")


def cache_set(key: str, value: Any, ttl: int = 60) -> bool:
    """Set a value in cache with TTL in seconds."""
    redis = get_redis()
    if not redis.is_available():
        return False
    return redis.set(f"cache:{key}", value, ttl=ttl)


def cache_delete(key: str) -> bool:
    """Delete a specific cache key."""
    redis = get_redis()
    if not redis.is_available():
        return False
    return redis.delete(f"cache:{key}") > 0


def cache_invalidate_pattern(pattern: str) -> int:
    """Delete all cache keys matching a glob pattern.

    Example: cache_invalidate_pattern("dashboard:*")
    """
    redis = get_redis()
    if not redis.is_available():
        return 0
    return redis.invalidate_pattern(f"cache:{pattern}")


# ── Event-driven invalidation ───────────────────────────────────────────────

# Map event types to cache patterns that should be invalidated
_EVENT_CACHE_MAP = {
    "job.status_changed": ["dashboard:*"],
    "video.created": ["dashboard:*", "videos:*"],
    "video.updated": ["dashboard:*", "videos:*"],
    "automation.status_changed": ["dashboard:*", "automation:*"],
    "game.enriched": ["games:*"],
    "gameplay.status_changed": ["dashboard:*"],
}


def invalidate_caches_for_event(event_type: str, user_id: Optional[int] = None) -> None:
    """Invalidate cache keys based on an event type.

    Called when an event is published. If user_id is provided, invalidates
    user-specific patterns (e.g. "dashboard:{user_id}"). Otherwise invalidates
    all matching patterns (e.g. "dashboard:*").
    """
    patterns = _EVENT_CACHE_MAP.get(event_type, [])
    for pattern in patterns:
        if user_id is not None and ":" in pattern:
            # Replace wildcard with user_id for user-specific invalidation
            base = pattern.split(":")[0]
            cache_delete(f"{base}:{user_id}")
        cache_invalidate_pattern(pattern)
