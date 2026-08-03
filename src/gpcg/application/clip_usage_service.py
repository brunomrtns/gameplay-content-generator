"""Service for tracking and querying gameplay clip usage across videos.

This module provides functions to:
- Check if a time range in a gameplay source has been used (in non-deleted videos)
- Record clip usage when a video is rendered
- Release clip usage when a video is deleted

The goal is to prevent reusing the same gameplay segment in multiple videos,
ensuring variety across the channel's content.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select, delete
from sqlalchemy.orm import Session

from gpcg.domain.models import GameplayClipUsage, GameplaySource

log = logging.getLogger(__name__)


@dataclass
class UsedRange:
    """A time range within a gameplay source that has been used in a video."""
    start_sec: float
    end_sec: float


def get_used_ranges(
    session: Session,
    source_id: int,
    *,
    consumer_user_id: Optional[int] = None,
) -> list[UsedRange]:
    """Get all used time ranges for a gameplay source (from non-deleted videos).

    Since we use cascade delete on the Video → GameplayClipUsage relationship
    (via the delete endpoint), any usage records here belong to active videos.

    REFACTORY_V2: when ``consumer_user_id`` is provided, only returns ranges
    consumed by that user. This implements per-consumer usage history for
    public gameplays — user A using a public segment doesn't block user B.
    When ``consumer_user_id`` is None (legacy/CLI), returns all ranges
    regardless of consumer (backward compatible).
    """
    stmt = (
        select(GameplayClipUsage.start_sec, GameplayClipUsage.end_sec)
        .where(GameplayClipUsage.source_id == source_id)
    )
    if consumer_user_id is not None:
        stmt = stmt.where(GameplayClipUsage.consumer_user_id == consumer_user_id)
    stmt = stmt.order_by(GameplayClipUsage.start_sec)
    rows = session.execute(stmt).all()
    return [UsedRange(start_sec=r[0], end_sec=r[1]) for r in rows]


def is_range_available(
    used_ranges: list[UsedRange],
    start: float,
    end: float,
    tolerance: float = 1.0,
) -> bool:
    """Check if a time range [start, end] does not overlap with any used range.

    Args:
        used_ranges: List of already-used ranges (sorted by start).
        start: Proposed clip start time.
        end: Proposed clip end time.
        tolerance: Minimum gap required between clips (seconds).
            A proposed clip is "available" if it doesn't overlap any used range
            by more than `tolerance` seconds.

    Returns:
        True if the range is available (no significant overlap), False otherwise.
    """
    for ur in used_ranges:
        # Check overlap: [start, end] vs [ur.start, ur.end]
        overlap_start = max(start, ur.start_sec)
        overlap_end = min(end, ur.end_sec)
        if overlap_end - overlap_start > tolerance:
            return False
    return True


def find_available_segment(
    source_duration: float,
    needed_duration: float,
    used_ranges: list[UsedRange],
    *,
    min_segment: float = 2.0,
    rng=None,
) -> Optional[tuple[float, float]]:
    """Find an available segment of `needed_duration` within a source of
    `source_duration` duration, avoiding all `used_ranges`.

    Strategy:
    1. Compute the gaps between used ranges (and before/after).
    2. Find gaps that are large enough for the needed duration.
    3. Pick a random start within a random suitable gap.

    Args:
        source_duration: Total duration of the gameplay source.
        needed_duration: Duration needed for the clip.
        used_ranges: Already-used ranges (will be sorted).
        min_segment: Minimum gap size to consider.
        rng: Random generator (for deterministic tests).

    Returns:
        (start, end) tuple if a segment was found, None otherwise.
    """
    import random as _random
    rng = rng or _random.Random()

    if source_duration < needed_duration:
        return None

    # Sort used ranges by start
    sorted_ranges = sorted(used_ranges, key=lambda r: r.start_sec)

    # Build list of available gaps: (gap_start, gap_end)
    gaps: list[tuple[float, float]] = []
    prev_end = 0.0
    for ur in sorted_ranges:
        if ur.start_sec > prev_end + 0.5:
            gaps.append((prev_end, ur.start_sec))
        prev_end = max(prev_end, ur.end_sec)
    # Final gap after last used range
    if prev_end < source_duration - 0.5:
        gaps.append((prev_end, source_duration))

    # Filter gaps that can fit the needed duration
    suitable = [
        (gs, ge) for gs, ge in gaps
        if (ge - gs) >= max(needed_duration, min_segment)
    ]

    if not suitable:
        return None

    # Pick a random suitable gap
    gap_start, gap_end = rng.choice(suitable)
    available = gap_end - gap_start

    if available <= needed_duration:
        # Use the whole gap
        return (gap_start, gap_start + min(needed_duration, available))

    # Pick a random start within the gap
    max_start = available - needed_duration
    start = gap_start + rng.uniform(0, max_start)
    return (start, start + needed_duration)


def record_clip_usage(
    session: Session,
    video_id: int,
    source_id: int,
    start_sec: float,
    end_sec: float,
    *,
    consumer_user_id: Optional[int] = None,
) -> GameplayClipUsage:
    """Record that a specific time range of a gameplay source was used in a video.

    REFACTORY_V2: ``consumer_user_id`` is the user who consumed this segment
    (the video owner). This allows per-consumer usage history for public
    gameplays — user A using a public gameplay segment doesn't block user B.
    """
    usage = GameplayClipUsage(
        video_id=video_id,
        source_id=source_id,
        consumer_user_id=consumer_user_id,
        start_sec=start_sec,
        end_sec=end_sec,
        duration=end_sec - start_sec,
    )
    session.add(usage)
    session.flush()
    log.info(
        f"recorded clip usage: video={video_id} source={source_id} "
        f"consumer={consumer_user_id} [{start_sec:.1f}s-{end_sec:.1f}s]"
    )
    return usage


def release_clip_usage(session: Session, video_id: int) -> int:
    """Release all clip usage records for a video (when video is deleted).

    Returns the number of records released.
    """
    result = session.execute(
        delete(GameplayClipUsage).where(GameplayClipUsage.video_id == video_id)
    )
    count = result.rowcount or 0
    if count > 0:
        log.info(f"released {count} clip usage records for video #{video_id}")
    return count
