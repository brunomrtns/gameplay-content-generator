"""Service for tracking and querying gameplay clip usage across videos.

This module provides functions to:
- Check if a time range in a gameplay source has been used (in non-deleted videos)
- Record clip usage when a video is rendered
- Release clip usage when a video is deleted
- Count overlapping uses for configurable reuse policies (max_uses)
- Find available segments with optional event-boundary awareness

The goal is to prevent reusing the same gameplay segment in multiple videos,
ensuring variety across the channel's content. The reuse policy is
configurable per-consumer via ``max_uses`` (1 = strict, 0 = unlimited).
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


# ── Reuse Policy Engine ─────────────────────────────────────────────────────


def count_overlapping_uses(
    used_ranges: list[UsedRange],
    start: float,
    end: float,
    *,
    tolerance: float = 1.0,
) -> int:
    """Count how many used ranges overlap significantly with [start, end].

    "Significantly" means overlap duration > tolerance (default 1.0s).
    A small overlap of < 1s (e.g. a transition frame) does NOT count as
    a reuse. This implements the policy described in §11 of the spec:
    small overlaps at boundaries don't invalidate mostly-new content.

    This is the core function for configurable reuse policies. Instead of
    a boolean "available/not available", it returns the count of existing
    uses that overlap the candidate range. The caller then compares this
    count against ``max_uses`` to determine eligibility.

    Examples:
        used: [100-120], candidate: [105-125] → overlap=15s > 1s → count=1
        used: [100-120], candidate: [119.5-140] → overlap=0.5s < 1s → count=0
        used: [100-120, 110-130], candidate: [115-125] → count=2
    """
    count = 0
    for ur in used_ranges:
        overlap_start = max(start, ur.start_sec)
        overlap_end = min(end, ur.end_sec)
        if overlap_end - overlap_start > tolerance:
            count += 1
    return count


def is_range_eligible(
    used_ranges: list[UsedRange],
    start: float,
    end: float,
    *,
    max_uses: int = 1,
    tolerance: float = 1.0,
) -> bool:
    """Check if a time range [start, end] is eligible for selection.

    A range is eligible if the number of significant overlaps with existing
    used ranges is less than ``max_uses``.

    Args:
        used_ranges: List of already-used ranges.
        start: Proposed clip start time.
        end: Proposed clip end time.
        max_uses: Maximum number of times a region can be used.
            1 = strict (default, backward compatible with is_range_available).
            2 = allow one reuse. 3 = allow two reuses.
            0 = unlimited (always eligible, but usage is still recorded).
        tolerance: Overlap threshold in seconds (default 1.0).

    Returns:
        True if the range is eligible, False otherwise.
    """
    if max_uses <= 0:
        # Unlimited — always eligible (history still recorded for analytics)
        return True
    return count_overlapping_uses(used_ranges, start, end, tolerance=tolerance) < max_uses


def is_range_available(
    used_ranges: list[UsedRange],
    start: float,
    end: float,
    tolerance: float = 1.0,
) -> bool:
    """Check if a time range [start, end] does not overlap with any used range.

    Backward-compatible alias for ``is_range_eligible(max_uses=1)``.
    Existing callers that don't know about max_uses continue to work.

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
    return is_range_eligible(used_ranges, start, end, max_uses=1, tolerance=tolerance)


def find_available_segment(
    source_duration: float,
    needed_duration: float,
    used_ranges: list[UsedRange],
    *,
    min_segment: float = 2.0,
    rng=None,
    event_boundaries: Optional[list[tuple[float, float]]] = None,
    max_uses: int = 1,
    tolerance: float = 1.0,
) -> Optional[tuple[float, float]]:
    """Find an available segment of `needed_duration` within a source of
    `source_duration` duration, avoiding all `used_ranges`.

    Strategy:
    1. Compute the gaps between used ranges (and before/after).
    2. Find gaps that are large enough for the needed duration.
    3. If event_boundaries are provided, prefer starts that align with
       event boundaries (semantic cuts). Fall back to random if none fit.
    4. Pick a random start within a random suitable gap.

    Args:
        source_duration: Total duration of the gameplay source.
        needed_duration: Duration needed for the clip.
        used_ranges: Already-used ranges (will be sorted).
        min_segment: Minimum gap size to consider.
        rng: Random generator (for deterministic tests).
        event_boundaries: Optional list of (start, end) tuples from
            GameplayEvent. When provided, the function prefers to align
            the segment start with an event boundary for coherent cuts.
            This does NOT force event alignment — it's a preference.
        max_uses: Reuse policy. 1 = strict (default), 0 = unlimited.
        tolerance: Overlap threshold in seconds (default 1.0).

    Returns:
        (start, end) tuple if a segment was found, None otherwise.
    """
    import random as _random
    rng = rng or _random.Random()

    if source_duration < needed_duration:
        return None

    # Defensive: max_uses=None means "not configured" → unlimited (0)
    max_uses = max_uses if max_uses is not None else 0

    # For unlimited reuse, any segment works — but still prefer unused
    if max_uses <= 0 and not used_ranges:
        # No used ranges at all — pick random or event-aligned
        if event_boundaries:
            for ev_start, ev_end in event_boundaries:
                if ev_end - ev_start >= needed_duration:
                    return (ev_start, ev_start + needed_duration)
        start = rng.uniform(0, max(0, source_duration - needed_duration))
        return (start, start + needed_duration)

    # Sort used ranges by start
    sorted_ranges = sorted(used_ranges, key=lambda r: r.start_sec)

    # Build list of available gaps: (gap_start, gap_end)
    # For max_uses > 1, gaps include partially-used regions that still
    # have eligibility remaining. But for simplicity and diversity,
    # we first try completely unused gaps, then fall back to used regions
    # if max_uses allows.
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

    if not suitable and max_uses <= 1:
        # Strict mode with no gaps — nothing available
        # (For max_uses > 1, we'll try event-based selection below)
        if not event_boundaries:
            return None
        # Try event boundaries even in used regions if max_uses > 1
        # (handled below)
        if max_uses <= 1:
            return None

    # If event boundaries are provided, try to find an event-aligned segment
    # that is eligible according to the reuse policy.
    if event_boundaries:
        eligible_events = []
        for ev_start, ev_end in event_boundaries:
            # Candidate segment: start at event boundary, take needed_duration
            cand_start = ev_start
            cand_end = min(ev_start + needed_duration, ev_end)
            if cand_end - cand_start < min(needed_duration, min_segment):
                continue
            # Check eligibility
            if is_range_eligible(used_ranges, cand_start, cand_end,
                                 max_uses=max_uses, tolerance=tolerance):
                eligible_events.append((cand_start, cand_end))

        if eligible_events:
            # Prefer eligible events. Pick randomly among them for variety.
            return rng.choice(eligible_events)

        # If no event-aligned segment is eligible but max_uses > 1,
        # try events that have the FEWEST overlapping uses (diversity preference)
        if max_uses > 1 and not suitable:
            scored_events = []
            for ev_start, ev_end in event_boundaries:
                cand_start = ev_start
                cand_end = min(ev_start + needed_duration, ev_end)
                if cand_end - cand_start < min(needed_duration, min_segment):
                    continue
                n = count_overlapping_uses(used_ranges, cand_start, cand_end,
                                           tolerance=tolerance)
                if n < max_uses:
                    scored_events.append((n, cand_start, cand_end))
            if scored_events:
                # Sort by usage count (fewest first = prefer unused)
                scored_events.sort(key=lambda x: x[0])
                _, s, e = scored_events[0]
                return (s, e)

    if not suitable:
        return None

    # Pick a random suitable gap
    gap_start, gap_end = rng.choice(suitable)
    available = gap_end - gap_start

    if available <= needed_duration:
        # Use the whole gap
        return (gap_start, gap_start + min(needed_duration, available))

    # If event boundaries exist, try to align the start to the nearest
    # event boundary within this gap for a coherent cut.
    if event_boundaries:
        for ev_start, ev_end in event_boundaries:
            if gap_start <= ev_start < gap_end:
                if ev_start + needed_duration <= gap_end:
                    return (ev_start, ev_start + needed_duration)

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


# ── Availability Estimation ─────────────────────────────────────────────────


def estimate_availability(
    source_duration: float,
    used_ranges: list[UsedRange],
    events: list[tuple[float, float]],
    *,
    max_uses: int = 1,
    tolerance: float = 1.0,
) -> dict:
    """Estimate gameplay availability for a source from a consumer's perspective.

    Returns a dict with:
        status: "abundant" | "partial" | "low" | "none" | "reuse_only"
        available_seconds: estimated seconds of eligible material
        total_seconds: source duration
        used_seconds: total seconds in used ranges
        eligible_events: count of events that are still eligible
        total_events: total events
    """
    total = source_duration
    used_sec = sum(r.end_sec - r.start_sec for r in used_ranges)

    if not events:
        # No semantic events — estimate from gaps
        sorted_ranges = sorted(used_ranges, key=lambda r: r.start_sec)
        gap_total = 0.0
        prev_end = 0.0
        for ur in sorted_ranges:
            if ur.start_sec > prev_end:
                gap_total += ur.start_sec - prev_end
            prev_end = max(prev_end, ur.end_sec)
        if prev_end < total:
            gap_total += total - prev_end

        if max_uses <= 0:
            avail = total
        elif max_uses == 1:
            avail = gap_total
        else:
            # Can reuse some used regions
            avail = gap_total + (used_sec * (max_uses - 1) / max(max_uses, 1))

        if avail <= 0:
            status = "reuse_only" if max_uses > 1 else "none"
        elif avail < 15:
            status = "low"
        elif avail < total * 0.3:
            status = "low"
        elif avail < total * 0.6:
            status = "partial"
        else:
            status = "abundant"

        return {
            "status": status,
            "available_seconds": round(avail, 1),
            "total_seconds": round(total, 1),
            "used_seconds": round(used_sec, 1),
            "eligible_events": 0,
            "total_events": 0,
        }

    # With events: count eligible events
    eligible = 0
    for ev_start, ev_end in events:
        if is_range_eligible(used_ranges, ev_start, ev_end,
                             max_uses=max_uses, tolerance=tolerance):
            eligible += 1

    total_events = len(events)
    eligible_sec = sum(e - s for s, e in events
                       if is_range_eligible(used_ranges, s, e,
                                            max_uses=max_uses, tolerance=tolerance))

    if eligible == 0:
        if max_uses > 1 and total_events > 0:
            status = "reuse_only"
        else:
            status = "none"
    elif eligible < total_events * 0.25:
        status = "low"
    elif eligible < total_events * 0.6:
        status = "partial"
    else:
        status = "abundant"

    return {
        "status": status,
        "available_seconds": round(eligible_sec, 1),
        "total_seconds": round(total, 1),
        "used_seconds": round(used_sec, 1),
        "eligible_events": eligible,
        "total_events": total_events,
    }
