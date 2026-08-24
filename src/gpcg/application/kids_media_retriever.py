"""Kids media retriever — semantic asset selection from the channel library.

Equivalent to ``GameplayRetriever`` in the Games domain, but for Kids
media assets (images + videos) stored in the channel library.

Three modes (same hierarchy as GameplayRetriever):
1. **Semantic event**: when a video asset has ``KidsMediaEvent`` records
   (from VLM+ASR mapping), selects segments whose event description,
   tags, or transcript match the query keywords. This is the same
   principle as GameplayRetriever using GameplayEvent for semantic
   clip matching.
2. **Semantic metadata**: when no events are available but the asset
   has ``tags`` or ``description``, matches against those.
3. **Fallback**: random selection from ready assets, weighted by
   ``used_count`` (prefer less-used assets for diversity).

For video assets: respects ``AssetClipUsage`` to avoid reusing the same
time ranges across videos (same principle as ``GameplayClipUsage``).
For image assets: can be reused freely (Ken Burns effect), but
``used_count`` is tracked for diversity scoring.

Key principle: clips are NOT extracted physically here. Only temporal
references (start_sec, end_sec) are returned for videos, and image
references for images. The render stage handles the actual extraction
or Ken Burns effect.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from gpcg.domain.creative_plan import VideoCreativePlan
from gpcg.domains.kids.models import (
    AssetClipUsage,
    AssetMediaKind,
    AssetProcessingStatus,
    KidsMediaEvent,
    StoryAsset,
)
from gpcg.logging import get_logger

log = get_logger(__name__)


@dataclass
class SelectedMedia:
    """A single media asset selected for use in a video.

    For images: start_sec=0, end_sec=0, duration = how long the image
    appears (Ken Burns effect duration).

    For videos: start_sec/end_sec define the segment to extract.
    duration = end_sec - start_sec.
    """
    asset: StoryAsset
    source_path: str
    start_sec: float = 0.0
    end_sec: float = 0.0
    duration: float = 0.0
    scene_index: int = 0
    # Why this media was selected: "semantic_event", "semantic_tag_match",
    # "semantic_description_match", "topic_scoped", "random_fallback"
    selection_reason: str = ""
    # How many times this asset was already used by this consumer
    usage_count_at_selection: int = 0
    # Which KidsMediaEvent informed this clip's boundaries (if any)
    event_id: Optional[int] = None


@dataclass
class MediaScene:
    """A scene = one or more media clips that play sequentially."""
    index: int
    clips: list[SelectedMedia] = field(default_factory=list)
    target_duration: float = 0.0

    @property
    def duration(self) -> float:
        return sum(c.duration for c in self.clips)

    @property
    def source_paths(self) -> list[str]:
        return [c.source_path for c in self.clips]


def _extract_keywords(query: str) -> list[str]:
    """Extract lowercase keywords from a query string.

    Splits on whitespace and punctuation, filters short tokens and
    common stopwords.
    """
    if not query:
        return []
    import re
    # Split on non-alphanumeric (handles PT-BR and EN)
    tokens = re.split(r'[^a-zA-Z0-9À-ÿ]+', query.lower())
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "have", "has", "had", "do", "does", "did", "will", "would",
        "could", "should", "may", "might", "must", "can",
        "o", "a", "os", "as", "de", "do", "da", "dos", "das",
        "e", "ou", "para", "por", "com", "sem", "em", "no", "na",
        "nos", "nas", "que", "se", "como", "mais", "menos",
        "um", "uma", "uns", "umas", "sobre", "entre",
    }
    return [t for t in tokens if len(t) >= 3 and t not in stopwords]


def _get_used_ranges(
    session: Session, asset_id: int, consumer_user_id: Optional[int] = None
) -> list[tuple[float, float]]:
    """Get used time ranges for a video asset (per consumer)."""
    query = select(AssetClipUsage.start_sec, AssetClipUsage.end_sec).where(
        AssetClipUsage.asset_id == asset_id,
    )
    if consumer_user_id is not None:
        query = query.where(AssetClipUsage.consumer_user_id == consumer_user_id)
    return [(r[0], r[1]) for r in session.execute(query).all()]


def _find_available_segment(
    duration: float, target: float, used_ranges: list[tuple[float, float]],
) -> tuple[float, float]:
    """Find an available segment of ~target seconds within a video of
    given duration, avoiding used ranges.

    Returns (start, end). If no clean segment found, returns a random
    segment (best effort).
    """
    if duration <= 0:
        return (0.0, 0.0)
    if target >= duration:
        return (0.0, duration)

    # Sort used ranges
    used = sorted(used_ranges)
    # Find gaps
    gaps = []
    prev_end = 0.0
    for u_start, u_end in used:
        if u_start > prev_end:
            gaps.append((prev_end, u_start))
        prev_end = max(prev_end, u_end)
    if prev_end < duration:
        gaps.append((prev_end, duration))

    # Find a gap that fits target
    suitable = [(s, e) for s, e in gaps if (e - s) >= target]
    if suitable:
        # Pick the largest gap
        s, e = max(suitable, key=lambda g: g[1] - g[0])
        start = s
        end = min(s + target, e)
        return (start, end)

    # No clean gap — take any gap (even partial)
    if gaps:
        s, e = max(gaps, key=lambda g: g[1] - g[0])
        start = s
        end = min(s + target, e)
        return (start, end)

    # No gaps at all — random segment (overlap is unavoidable)
    import random as _r
    max_start = max(0, duration - target)
    start = _r.uniform(0, max_start) if max_start > 0 else 0.0
    return (start, start + target)


class KidsMediaRetriever:
    """Retrieves Kids media assets for video generation.

    Selects assets from the channel library that match the video content.
    Uses tags + description for semantic matching when a creative plan
    or topic title is available. Falls back to random weighted selection.

    For videos: respects AssetClipUsage (don't reuse same segment).
    For images: freely reusable (Ken Burns effect), but weighted by
    usage count for diversity.
    """

    def retrieve(
        self,
        session: Session,
        user_id: int,
        target_duration: float,
        *,
        creative_plan: Optional[VideoCreativePlan] = None,
        topic_id: Optional[int] = None,
        topic_title: Optional[str] = None,
        scene_duration: float = 0.0,
        rng: Optional[random.Random] = None,
        accept_public: bool = False,
    ) -> list[SelectedMedia]:
        """Retrieve media clips for a Kids video.

        Args:
            session: DB session
            user_id: user's channel to select media from
            target_duration: total duration to fill (narration duration)
            creative_plan: editorial plan (may contain media_query)
            topic_id: if set, prioritize assets linked to this topic
            topic_title: used for keyword extraction if no plan
            scene_duration: scene grouping (0 = legacy mode)
            rng: random generator (for deterministic tests)
            accept_public: if True, fall back to public assets from
                           other users when user's own are exhausted

        Returns:
            List of SelectedMedia with references to assets
        """
        rng = rng or random.Random()

        # Build the query for semantic matching
        media_query = ""
        if creative_plan and creative_plan.success:
            media_query = getattr(creative_plan, "gameplay_query", "") or ""
        if not media_query and topic_title:
            media_query = topic_title

        keywords = _extract_keywords(media_query)
        log.info(
            f"KidsMediaRetriever: user={user_id} target={target_duration:.1f}s "
            f"topic_id={topic_id} keywords={keywords[:5]}..."
        )

        # Query ready assets from the user's library
        query = select(StoryAsset).where(
            StoryAsset.user_id == user_id,
            StoryAsset.processing_status == AssetProcessingStatus.ready.value,
        )
        if not accept_public:
            query = query.where(StoryAsset.is_public == False)

        assets = list(session.execute(query).scalars().all())

        # Fallback: public assets from other users
        if not assets and accept_public:
            log.info(f"KidsMediaRetriever: no own assets, trying public library")
            pub_query = select(StoryAsset).where(
                StoryAsset.is_public == True,
                StoryAsset.processing_status == AssetProcessingStatus.ready.value,
                StoryAsset.user_id != user_id,
            )
            assets = list(session.execute(pub_query).scalars().all())

        if not assets:
            log.warning(f"KidsMediaRetriever: no ready assets for user {user_id}")
            return []

        # Score assets by semantic relevance
        scored = self._score_assets(assets, keywords, topic_id, session)

        # Select clips to fill target_duration
        if scene_duration > 0:
            clips = self._select_scene_based(
                scored, target_duration, scene_duration, rng, session, user_id,
                keywords=keywords,
            )
        else:
            clips = self._select_legacy(
                scored, target_duration, rng, session, user_id,
                keywords=keywords,
            )

        total = sum(c.duration for c in clips)
        log.info(
            f"KidsMediaRetriever: selected {len(clips)} clip(s) "
            f"totaling {total:.1f}s for target {target_duration:.1f}s"
        )
        return clips

    def _score_assets(
        self,
        assets: list[StoryAsset],
        keywords: list[str],
        topic_id: Optional[int],
        session: Session,
    ) -> list[tuple[StoryAsset, float, str]]:
        """Score assets by semantic relevance.

        Scoring hierarchy (same principle as GameplayRetriever._score_source_fit):
        1. Event matches: KidsMediaEvent description/tags/transcript match
           keywords (strongest signal — VLM analyzed the actual content)
        2. Tag matches: asset.tags match keywords (manual metadata)
        3. Description matches: asset.description matches keywords
        4. Topic-scoped: asset linked to the topic
        5. Diversity penalty: prefer less-used assets

        Returns list of (asset, score, reason) sorted by score descending.
        """
        scored: list[tuple[StoryAsset, float, str]] = []
        for asset in assets:
            score = 0.0
            reason = "random_fallback"

            # Topic-scoped bonus: assets linked to the topic get priority
            if topic_id and asset.topic_id == topic_id:
                score += 50.0
                reason = "topic_scoped"

            # Semantic: event matches (strongest — VLM analyzed the content)
            if keywords and asset.media_kind == AssetMediaKind.video.value:
                events = session.execute(
                    select(KidsMediaEvent).where(KidsMediaEvent.asset_id == asset.id)
                ).scalars().all()
                event_match_count = 0
                for evt in events:
                    evt_text = " ".join([
                        evt.description or "",
                        " ".join(evt.tags or []),
                        evt.transcript or "",
                    ]).lower()
                    if any(kw in evt_text for kw in keywords):
                        event_match_count += 1
                if event_match_count > 0:
                    score += event_match_count * 30.0
                    reason = "semantic_event"

            # Semantic: tag matches (manual metadata)
            if keywords:
                asset_tags = [t.lower() for t in (asset.tags or [])]
                tag_matches = sum(1 for kw in keywords if kw in asset_tags)
                if tag_matches > 0:
                    score += tag_matches * 20.0
                    if reason == "random_fallback":
                        reason = "semantic_tag_match"

                # Semantic: description matches
                desc = (asset.description or "").lower()
                desc_matches = sum(1 for kw in keywords if kw in desc)
                if desc_matches > 0:
                    score += desc_matches * 10.0
                    if reason == "random_fallback":
                        reason = "semantic_description_match"

            # Diversity penalty: prefer less-used assets
            usage = (asset.metadata_json or {}).get("used_count", 0)
            score -= usage * 5.0

            scored.append((asset, score, reason))

        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _select_legacy(
        self,
        scored: list[tuple[StoryAsset, float, str]],
        target_duration: float,
        rng: random.Random,
        session: Session,
        user_id: int,
        keywords: Optional[list[str]] = None,
    ) -> list[SelectedMedia]:
        """Legacy mode: accumulate clips until target_duration is reached.

        Each image contributes a default display duration (5s).
        Each video contributes a segment of its duration.

        For videos with KidsMediaEvent records that match keywords,
        the segment is aligned to the matching event's time range
        (same principle as GameplayRetriever using GameplayEvent).
        """
        clips: list[SelectedMedia] = []
        total = 0.0
        image_display_duration = 5.0  # seconds per image (Ken Burns)
        used_asset_ids: set[int] = set()

        # First pass: use semantically matched assets
        for asset, score, reason in scored:
            if total >= target_duration:
                break
            if asset.id in used_asset_ids:
                continue

            if asset.media_kind == AssetMediaKind.video.value:
                # Video: take a segment
                video_dur = asset.duration or 0.0
                if video_dur <= 0:
                    continue
                needed = min(video_dur, target_duration - total)
                used_ranges = _get_used_ranges(session, asset.id, user_id)

                # Try to find a matching event for this segment
                event_id = None
                start, end = 0.0, 0.0

                if keywords and reason == "semantic_event":
                    matching_event = self._find_matching_event(
                        session, asset.id, keywords, used_ranges, needed,
                    )
                    if matching_event:
                        event_id = matching_event.id
                        start = matching_event.start_time
                        end = min(matching_event.end_time, start + needed)
                        if end <= start:
                            end = start + min(needed, video_dur - start)

                if event_id is None:
                    # No event match — find available segment
                    start, end = _find_available_segment(video_dur, needed, used_ranges)

                clip_dur = end - start
                if clip_dur <= 0:
                    continue
                clips.append(SelectedMedia(
                    asset=asset,
                    source_path=asset.storage_key,
                    start_sec=start,
                    end_sec=end,
                    duration=clip_dur,
                    selection_reason=reason,
                    usage_count_at_selection=len(used_ranges),
                    event_id=event_id,
                ))
                total += clip_dur
            else:
                # Image: display for image_display_duration
                needed = min(image_display_duration, target_duration - total)
                clips.append(SelectedMedia(
                    asset=asset,
                    source_path=asset.storage_key,
                    start_sec=0.0,
                    end_sec=0.0,
                    duration=needed,
                    selection_reason=reason,
                    usage_count_at_selection=(asset.metadata_json or {}).get("used_count", 0),
                ))
                total += needed
            used_asset_ids.add(asset.id)

        # Second pass: if still need more, cycle through assets (random)
        if total < target_duration and scored:
            remaining_assets = [a for a, _, _ in scored if a.id not in used_asset_ids]
            if not remaining_assets:
                # All used — cycle through again (images can repeat)
                remaining_assets = [a for a, _, _ in scored if a.media_kind == AssetMediaKind.image.value]

            while total < target_duration and remaining_assets:
                # Pick random (weighted by inverse usage)
                weights = [1.0 / ((a.metadata_json or {}).get("used_count", 0) + 1) for a in remaining_assets]
                idx = rng.choices(range(len(remaining_assets)), weights=weights, k=1)[0]
                asset = remaining_assets[idx]

                if asset.media_kind == AssetMediaKind.video.value:
                    video_dur = asset.duration or 0.0
                    if video_dur <= 0:
                        remaining_assets.pop(idx)
                        continue
                    needed = min(video_dur, target_duration - total)
                    used_ranges = _get_used_ranges(session, asset.id, user_id)
                    start, end = _find_available_segment(video_dur, needed, used_ranges)
                    clip_dur = end - start
                    clips.append(SelectedMedia(
                        asset=asset,
                        source_path=asset.storage_key,
                        start_sec=start,
                        end_sec=end,
                        duration=clip_dur,
                        selection_reason="random_fallback",
                        usage_count_at_selection=len(used_ranges),
                    ))
                    total += clip_dur
                else:
                    needed = min(image_display_duration, target_duration - total)
                    clips.append(SelectedMedia(
                        asset=asset,
                        source_path=asset.storage_key,
                        duration=needed,
                        selection_reason="random_fallback",
                        usage_count_at_selection=(asset.metadata_json or {}).get("used_count", 0),
                    ))
                    total += needed

                if asset.media_kind == AssetMediaKind.video.value:
                    remaining_assets.pop(idx)

        return clips

    def _find_matching_event(
        self,
        session: Session,
        asset_id: int,
        keywords: list[str],
        used_ranges: list[tuple[float, float]],
        needed_duration: float,
    ) -> Optional[KidsMediaEvent]:
        """Find the best KidsMediaEvent that matches keywords and is available.

        Same principle as GameplayRetriever querying GameplayEvent for
        semantic clip matching. Prefers events with:
        - Keyword matches in description/tags/transcript
        - Available time range (not in used_ranges)
        - Higher interesting_score
        - Higher visual_confidence
        """
        events = session.execute(
            select(KidsMediaEvent)
            .where(KidsMediaEvent.asset_id == asset_id)
            .order_by(KidsMediaEvent.interesting_score.desc())
        ).scalars().all()

        best: Optional[KidsMediaEvent] = None
        best_score = -1.0

        for evt in events:
            evt_text = " ".join([
                evt.description or "",
                " ".join(evt.tags or []),
                evt.transcript or "",
            ]).lower()
            if not any(kw in evt_text for kw in keywords):
                continue

            # Check if this event's range is available
            is_available = True
            for u_start, u_end in used_ranges:
                if evt.start_time < u_end and evt.end_time > u_start:
                    is_available = False
                    break
            if not is_available:
                continue

            # Score: keyword matches + interesting + confidence
            match_count = sum(1 for kw in keywords if kw in evt_text)
            score = match_count * 10.0 + evt.interesting_score * 5.0 + evt.visual_confidence * 2.0
            if score > best_score:
                best_score = score
                best = evt

        return best

    def _select_scene_based(
        self,
        scored: list[tuple[StoryAsset, float, str]],
        target_duration: float,
        scene_duration: float,
        rng: random.Random,
        session: Session,
        user_id: int,
        keywords: Optional[list[str]] = None,
    ) -> list[SelectedMedia]:
        """Scene-based mode: group clips into scenes of scene_duration each."""
        if scene_duration >= target_duration:
            # One long scene covering the whole video
            return self._select_legacy(
                scored, target_duration, rng, session, user_id, keywords=keywords,
            )

        n_scenes = max(1, int(target_duration / scene_duration) + 1)
        all_clips: list[SelectedMedia] = []

        for scene_idx in range(n_scenes):
            scene_target = min(scene_duration, target_duration - sum(c.duration for c in all_clips))
            if scene_target <= 0:
                break
            scene_clips = self._select_legacy(
                scored, scene_target, rng, session, user_id, keywords=keywords,
            )
            for c in scene_clips:
                c.scene_index = scene_idx
            all_clips.extend(scene_clips)

        return all_clips
