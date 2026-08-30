"""Redis adapter — singleton wrapping pub/sub, streams, and cache.

All Redis operations degrade gracefully: if Redis is unreachable, pub/sub
becomes a no-op, cache returns None, and stream operations return empty.
The caller (job queue, event publisher, SSE endpoint) is responsible for
falling back to SQLite polling when Redis is down.

Architecture:
    - Lazy connection (connects on first use)
    - Retry with backoff (5 attempts, 1s apart) before declaring Redis down
    - Thread-safe singleton via module-level _adapter
    - Health check via ping()

Usage:
    from gpcg.infrastructure.redis_adapter import get_redis

    redis = get_redis()
    if redis.is_available():
        redis.publish("user:1:events", "job.status_changed", {"job_id": 42})
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Generator, Optional

log = logging.getLogger(__name__)

# ── Lazy imports ────────────────────────────────────────────────────────────
# redis-py is a hard dependency (in pyproject.toml), but we import lazily
# so that the module can be imported even if redis is not installed (e.g.
# in a minimal worker that doesn't need Redis). The adapter will simply
# report is_available()=False.

_redis_lib: Any = None
_redis_import_error: Optional[str] = None


def _ensure_redis_lib() -> None:
    """Import redis-py lazily. Sets _redis_import_error on failure."""
    global _redis_lib, _redis_import_error
    if _redis_lib is not None or _redis_import_error is not None:
        return
    try:
        import redis  # type: ignore[import-untyped]
        _redis_lib = redis
    except ImportError as e:
        _redis_import_error = str(e)
        log.warning(f"redis-py not installed: {_redis_import_error}")


class RedisAdapter:
    """Singleton Redis adapter with graceful fallback.

    All methods are safe to call even when Redis is down — they return
    None/empty/false instead of raising.
    """

    def __init__(self, url: str, maxlen: int = 10000) -> None:
        self._url = url
        self._maxlen = maxlen
        self._client: Any = None
        self._pubsub: Any = None
        self._available: bool = False
        self._connect_attempts: int = 0
        self._last_check: float = 0.0

    # ── Connection management ──────────────────────────────────────────────

    def _connect(self) -> bool:
        """Try to connect to Redis with retry. Returns True if available."""
        _ensure_redis_lib()
        if _redis_lib is None:
            return False
        if not self._url:
            return False
        if self._client is not None and self._available:
            return True

        # Throttle: after a full failure cycle, wait 30s before trying again.
        # This prevents the worker from spending 5s per poll cycle retrying a
        # Redis that is permanently unreachable (e.g. worker running outside
        # Docker with redis_url=redis://redis:6379/0).
        now = time.time()
        if self._connect_attempts >= 5 and now - self._last_check < 30.0:
            return self._available

        max_retries = 5
        for attempt in range(max_retries):
            self._last_check = time.time()
            self._connect_attempts = attempt + 1
            try:
                self._client = _redis_lib.Redis.from_url(
                    self._url,
                    decode_responses=True,
                    socket_connect_timeout=5,
                    socket_timeout=5,
                    retry_on_timeout=True,
                )
                self._client.ping()
                self._available = True
                self._connect_attempts = 0
                log.info(f"Redis connected: {self._url}")
                return True
            except Exception as e:
                # DNS resolution errors (hostname doesn't resolve) are
                # permanent — no point retrying 5 times with 1s sleep.
                # Fail fast on the first attempt to avoid blocking the
                # worker loop for 5 seconds every cycle.
                err_str = str(e).lower()
                if "name or service not known" in err_str or \
                   "temporary failure in name resolution" in err_str or \
                   "nodename nor servname provided" in err_str or \
                   "getaddrinfo" in err_str:
                    log.warning(f"Redis unreachable (DNS failure): {self._url} — not retrying")
                    self._client = None
                    self._available = False
                    return False

                log.warning(f"Redis connect attempt {attempt + 1}/{max_retries} failed: {e}")
                self._client = None
                self._available = False
                if attempt < max_retries - 1:
                    time.sleep(1.0)

        log.error(f"Redis unavailable after {max_retries} attempts — falling back to no-op")
        return False

    def is_available(self) -> bool:
        """Check if Redis is connected. Reconnects if needed."""
        if self._available and self._client is not None:
            try:
                self._client.ping()
                return True
            except Exception:
                self._available = False
                self._client = None
        return self._connect()

    def ping(self) -> bool:
        """Health check."""
        return self.is_available()

    # ── Pub/Sub ────────────────────────────────────────────────────────────

    def publish(self, channel: str, event_type: str, payload: dict) -> bool:
        """Publish an event to a pub/sub channel. Returns True if published."""
        if not self.is_available():
            return False
        try:
            event = json.dumps({
                "id": str(uuid.uuid4()),
                "type": event_type,
                "payload": payload,
                "ts": time.time(),
            })
            self._client.publish(channel, event)
            return True
        except Exception as e:
            log.warning(f"Redis publish failed: {e}")
            self._available = False
            return False

    def subscribe(self, channels: list[str]) -> Generator[dict, None, None]:
        """Subscribe to channels and yield events as dicts.

        Yields: {"id": str, "type": str, "payload": dict, "ts": float, "channel": str}

        Blocks until the generator is closed or Redis disconnects.
        """
        if not self.is_available():
            return
        try:
            pubsub = self._client.pubsub()
            pubsub.subscribe(*channels)
            for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        data["channel"] = message["channel"]
                        yield data
                    except (json.JSONDecodeError, KeyError):
                        continue
        except Exception as e:
            log.warning(f"Redis subscribe ended: {e}")
            self._available = False

    # ── Streams (job queue) ────────────────────────────────────────────────

    def xadd(self, stream: str, fields: dict, maxlen: Optional[int] = None) -> Optional[str]:
        """Add an entry to a stream. Returns the entry ID or None on failure."""
        if not self.is_available():
            return None
        try:
            ml = maxlen if maxlen is not None else self._maxlen
            entry_id = self._client.xadd(stream, fields, maxlen=ml, approximate=True)
            return entry_id
        except Exception as e:
            log.warning(f"Redis xadd failed: {e}")
            self._available = False
            return None

    def xreadgroup(
        self,
        group: str,
        consumer: str,
        streams: dict[str, str],
        block_ms: int = 5000,
        count: int = 1,
    ) -> list[tuple[str, list[tuple[str, dict]]]]:
        """Read from a consumer group. Returns [(stream, [(id, fields), ...]), ...]."""
        if not self.is_available():
            return []
        try:
            # Ensure consumer group exists (create if not)
            for stream_name in streams:
                try:
                    self._client.xgroup_create(stream_name, group, id="0", mkstream=True)
                except Exception:
                    pass  # Group already exists

            result = self._client.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams=streams,
                count=count,
                block=block_ms,
            )
            return result or []
        except Exception as e:
            log.warning(f"Redis xreadgroup failed: {e}")
            self._available = False
            return []

    def xautoclaim(
        self,
        stream: str,
        group: str,
        consumer: str,
        min_idle_ms: int,
        count: int = 100,
    ) -> tuple[str, list[tuple[str, dict]], list[tuple[str, dict]]]:
        """Claim stale pending messages. Returns (next_start_id, claimed_messages, deleted_ids)."""
        if not self.is_available():
            return ("0-0", [], [])
        try:
            result = self._client.xautoclaim(
                stream, group, consumer, min_idle_time=min_idle_ms, count=count
            )
            # xautoclaim returns (next_start_id, claimed_messages, deleted_ids)
            if len(result) == 3:
                return result
            elif len(result) == 2:
                return (result[0], result[1], [])
            else:
                return ("0-0", [], [])
        except Exception as e:
            log.warning(f"Redis xautoclaim failed: {e}")
            self._available = False
            return ("0-0", [], [])

    def xack(self, stream: str, group: str, *entry_ids: str) -> int:
        """Acknowledge processed messages. Returns number of acked entries."""
        if not self.is_available():
            return 0
        try:
            return self._client.xack(stream, group, *entry_ids)
        except Exception as e:
            log.warning(f"Redis xack failed: {e}")
            self._available = False
            return 0

    def xpending(self, stream: str, group: str) -> dict:
        """Get pending messages summary. Returns dict with pending count."""
        if not self.is_available():
            return {"pending": 0}
        try:
            return self._client.xpending(stream, group)
        except Exception as e:
            log.warning(f"Redis xpending failed: {e}")
            self._available = False
            return {"pending": 0}

    # ── Cache ──────────────────────────────────────────────────────────────

    def set(self, key: str, value: Any, ttl: int = 60) -> bool:
        """Set a cache key with TTL in seconds."""
        if not self.is_available():
            return False
        try:
            serialized = json.dumps(value) if not isinstance(value, str) else value
            self._client.setex(key, ttl, serialized)
            return True
        except Exception as e:
            log.warning(f"Redis set failed: {e}")
            self._available = False
            return False

    def get(self, key: str) -> Optional[Any]:
        """Get a cache key. Returns None if missing or Redis down."""
        if not self.is_available():
            return None
        try:
            raw = self._client.get(key)
            if raw is None:
                return None
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return raw
        except Exception as e:
            log.warning(f"Redis get failed: {e}")
            self._available = False
            return None

    def delete(self, *keys: str) -> int:
        """Delete cache keys. Returns number of deleted keys."""
        if not self.is_available():
            return 0
        try:
            return self._client.delete(*keys)
        except Exception as e:
            log.warning(f"Redis delete failed: {e}")
            self._available = False
            return 0

    def invalidate_pattern(self, pattern: str) -> int:
        """Delete all keys matching a glob pattern (e.g. 'cache:dashboard:*')."""
        if not self.is_available():
            return 0
        try:
            keys = list(self._client.scan_iter(match=pattern, count=100))
            if keys:
                return self._client.delete(*keys)
            return 0
        except Exception as e:
            log.warning(f"Redis invalidate_pattern failed: {e}")
            self._available = False
            return 0


# ── Singleton ───────────────────────────────────────────────────────────────

_adapter: Optional[RedisAdapter] = None


def get_redis() -> RedisAdapter:
    """Get the singleton RedisAdapter instance."""
    global _adapter
    if _adapter is None:
        from gpcg.config import get_settings
        settings = get_settings()
        _adapter = RedisAdapter(
            url=settings.redis_url,
            maxlen=settings.redis_stream_maxlen,
        )
    return _adapter


def reset_redis() -> None:
    """Reset the singleton (for testing)."""
    global _adapter
    _adapter = None
