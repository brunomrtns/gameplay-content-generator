"""SSE (Server-Sent Events) endpoint for real-time event streaming.

Subscribes to Redis pub/sub channels and streams events to the client.
Falls back to keepalive-only if Redis is unavailable.

Authentication:
    - Web: BI Identity cookie (bi_auth) — same-site, EventSource withCredentials
    - Mobile: Bearer JWT token — react-native-sse with headers

Channels subscribed:
    - user:{user.id}:events — per-user events (jobs, gameplays, videos)
    - global:workers — worker status changes
    - global:games — game enrichment events
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from gpcg.infrastructure.auth import get_current_user
from gpcg.infrastructure.database import get_db
from gpcg.infrastructure.redis_adapter import get_redis

log = logging.getLogger(__name__)

router = APIRouter()

KEEPALIVE_INTERVAL = 5.0  # seconds between keepalive comments


@router.get("/events/stream")
async def event_stream(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """SSE endpoint that streams real-time events to the client.

    Returns a StreamingResponse with text/event-stream content type.
    The connection stays open until the client disconnects or the server
    shuts down.

    Events are JSON-encoded: data: {"type":"job.status_changed","payload":{...}}\n\n
    Keepalive comments (:\n\n) are sent every 5 seconds to prevent
    nginx send_timeout from closing the connection.
    """

    user_id = user.id
    channels = [
        f"user:{user_id}:events",
        "global:workers",
        "global:games",
    ]

    async def event_generator():
        """Async generator that yields SSE-formatted events."""
        redis = get_redis()

        # If Redis is not available, send keepalive-only stream
        # (client will still receive events when Redis comes back and
        # client reconnects)
        if not redis.is_available():
            log.info(f"SSE: Redis unavailable, serving keepalive-only stream for user {user_id}")
            while True:
                if await request.is_disconnected():
                    return
                yield ": keepalive\n\n"
                await asyncio.sleep(KEEPALIVE_INTERVAL)

        # Start a background task for keepalive
        # We use a queue to bridge sync Redis pubsub -> async SSE
        event_queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        async def keepalive_loop():
            """Send keepalive comments periodically."""
            while True:
                await asyncio.sleep(KEEPALIVE_INTERVAL)
                try:
                    await event_queue.put(None)  # None = keepalive signal
                except asyncio.QueueFull:
                    pass

        def redis_subscribe_loop():
            """Run Redis pubsub in a thread (redis-py is sync)."""
            try:
                for event in redis.subscribe(channels):
                    if event is None:
                        continue
                    # Put event in queue (thread-safe via run_coroutine_threadsafe)
                    try:
                        loop.call_soon_threadsafe(event_queue.put_nowait, event)
                    except Exception:
                        break
            except Exception as e:
                log.warning(f"SSE: redis subscribe loop ended: {e}")

        # Start background tasks
        keepalive_task = asyncio.create_task(keepalive_loop())
        import threading
        redis_thread = threading.Thread(target=redis_subscribe_loop, daemon=True)
        redis_thread.start()

        try:
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    log.info(f"SSE: client disconnected (user {user_id})")
                    break

                try:
                    # Wait for events with timeout (so we can check disconnect)
                    item = await asyncio.wait_for(event_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                if item is None:
                    # Keepalive
                    yield ": keepalive\n\n"
                else:
                    # Real event
                    event_data = json.dumps(item)
                    yield f"data: {event_data}\n\n"

        except asyncio.CancelledError:
            log.info(f"SSE: generator cancelled (user {user_id})")
        except Exception as e:
            log.warning(f"SSE: generator error: {e}")
        finally:
            keepalive_task.cancel()
            log.info(f"SSE: stream closed (user {user_id})")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
