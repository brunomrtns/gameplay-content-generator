"""Gameplay retriever — semantic clip selection using the GameplayEvent index.

When a VideoCreativePlan is available with gameplay_strategy="related" or
"thematic_match", this retriever queries the semantic index for events
that match the plan's gameplay_query. It then builds SelectedClip objects
from the event time ranges.

When the plan's strategy is "background_filler" or no plan is available,
it falls back to the GameplaySelector (random selection).

The retriever respects:
  - GameplaySource.compatibility (game_related / general_topic flags)
  - GameplayEvent.interesting_score (prefer more interesting events)
  - GameplayEvent.visual_confidence (skip low-confidence events)
  - The plan's gameplay_query (semantic search over descriptions + transcripts)

Key principle: clips are NOT extracted physically here. Only temporal
references (start_sec, end_sec) are returned. The render stage extracts
the actual video segments on-demand.
"""

from __future__ import annotations

import random
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from gpcg.application.gameplay_index_service import GameplayIndexService
from gpcg.application.gameplay_selector import GameplaySelector, SelectedClip
from gpcg.domain.creative_plan import VideoCreativePlan
from gpcg.domain.models import GameplayEvent, GameplaySource
from gpcg.logging import get_logger

log = get_logger(__name__)


class GameplayRetriever:
    """Retrieves gameplay clips using the semantic index when available.

    Falls back to GameplaySelector (random) when:
    - No VideoCreativePlan is provided
    - The plan's strategy is "background_filler"
    - No analyzed gameplay events exist for the game
    """

    def __init__(self, index_service: Optional[GameplayIndexService] = None) -> None:
        self.index_service = index_service or GameplayIndexService()
        self.fallback_selector = GameplaySelector()

    def retrieve(
        self,
        session: Session,
        game_id: int,
        target_duration: float,
        *,
        creative_plan: Optional[VideoCreativePlan] = None,
        scene_duration: float = 0.0,
        video_type: str = "GAME_RELATED",
        rng: Optional[random.Random] = None,
    ) -> list[SelectedClip]:
        """Retrieve gameplay clips for a video.

        Args:
            session: DB session
            game_id: game to retrieve gameplay from
            target_duration: total duration to fill (narration duration)
            creative_plan: the editorial plan (may be None for fallback)
            scene_duration: scene grouping duration (0 = legacy mode)
            video_type: GAME_RELATED or GENERAL_TOPIC (for compatibility check)
            rng: random generator (for deterministic tests)

        Returns:
            List of SelectedClip with temporal references to gameplay segments
        """
        rng = rng or random.Random()

        # Decide whether to use semantic retrieval or fallback
        use_semantic = self._should_use_semantic(session, game_id, creative_plan, video_type)

        if use_semantic and creative_plan is not None:
            clips = self._retrieve_semantic(
                session, game_id, target_duration, creative_plan, scene_duration, rng
            )
            if clips:
                return clips
            # If semantic retrieval found nothing, fall back
            log.info(f"semantic retrieval found no clips for game #{game_id}, falling back to random")

        # Fallback: random selection via GameplaySelector
        return self.fallback_selector.select(
            session, game_id, target_duration,
            scene_duration=scene_duration, rng=rng,
        )

    def _should_use_semantic(
        self,
        session: Session,
        game_id: int,
        plan: Optional[VideoCreativePlan],
        video_type: str,
    ) -> bool:
        """Check if semantic retrieval should be used."""
        if plan is None or not plan.success:
            return False

        # background_filler = gameplay is just visual background, no semantic match needed
        if plan.gameplay_strategy == "background_filler":
            return False

        # Check if any source for this game has a ready semantic index
        sources = session.execute(
            select(GameplaySource).where(
                GameplaySource.game_id == game_id,
                GameplaySource.ingestion_status == "ready",
            )
        ).scalars().all()

        for src in sources:
            if src.is_analysis_ready:
                # Check compatibility
                compat = src.compatibility
                if video_type == "GAME_RELATED" and not compat.get("game_related", True):
                    continue
                if video_type == "GENERAL_TOPIC" and not compat.get("general_topic", True):
                    continue
                return True

        return False

    def _retrieve_semantic(
        self,
        session: Session,
        game_id: int,
        target_duration: float,
        plan: VideoCreativePlan,
        scene_duration: float,
        rng: random.Random,
    ) -> list[SelectedClip]:
        """Retrieve clips using the semantic index.

        Strategy:
        1. Find sources with ready analysis + compatible
        2. Query events matching the plan's gameplay_query (if any)
        3. If no query or no matches, get the most interesting events
        4. Build SelectedClips from event time ranges
        5. If total < target_duration, fill remaining with random selection
        """
        # Find compatible sources with ready analysis
        sources = session.execute(
            select(GameplaySource).where(
                GameplaySource.game_id == game_id,
                GameplaySource.ingestion_status == "ready",
            )
        ).scalars().all()

        compatible_sources = []
        for src in sources:
            if not src.is_analysis_ready:
                continue
            compat = src.compatibility
            if plan.video_type == "GAME_RELATED" and not compat.get("game_related", True):
                continue
            if plan.video_type == "GENERAL_TOPIC" and not compat.get("general_topic", True):
                continue
            compatible_sources.append(src)

        if not compatible_sources:
            return []

        # Collect events from all compatible sources
        all_events: list[tuple[GameplaySource, GameplayEvent]] = []

        # First try: semantic search with the plan's query
        if plan.gameplay_query:
            for src in compatible_sources:
                events = self.index_service.search_events(
                    session, src.id, plan.gameplay_query,
                    min_confidence=0.3, limit=10,
                )
                for ev in events:
                    all_events.append((src, ev))

        # If no query or no matches, get the most interesting events
        if not all_events:
            for src in compatible_sources:
                events = self.index_service.get_interesting_events(
                    session, src.id,
                    min_interesting=0.3, limit=10,
                )
                for ev in events:
                    all_events.append((src, ev))

        # Last resort: if still no events (e.g. interesting_score not set),
        # grab random events regardless of score
        if not all_events:
            log.info(f"no interesting events (score>=0.3) for game #{game_id}, "
                     f"using random events from {len(compatible_sources)} sources")
            for src in compatible_sources:
                events = session.execute(
                    select(GameplayEvent)
                    .where(GameplayEvent.source_id == src.id)
                    .order_by(GameplayEvent.id)
                    .limit(20)
                ).scalars().all()
                for ev in events:
                    all_events.append((src, ev))

        if not all_events:
            return []

        # Sort by interesting score (descending) for prioritization
        all_events.sort(key=lambda x: x[1].interesting_score, reverse=True)

        # Build clips from events, accumulating until target_duration
        clips: list[SelectedClip] = []
        total = 0.0
        scene_idx = 0

        for src, ev in all_events:
            if total >= target_duration:
                break

            # Determine clip duration
            event_duration = ev.end_time - ev.start_time
            if event_duration <= 0:
                continue

            # If scene_duration > 0, cap clip at scene_duration
            clip_duration = event_duration
            if scene_duration > 0:
                clip_duration = min(event_duration, scene_duration)

            # Don't exceed remaining target
            remaining = target_duration - total
            clip_duration = min(clip_duration, remaining)

            if clip_duration < 0.5:
                continue

            # Build the SelectedClip
            # We need a GameplayAsset — but the semantic index doesn't use assets.
            # We create a pseudo-asset using the source directly.
            # The render stage will extract the segment from the source file.
            from gpcg.domain.models import GameplayAsset
            # Find or create a pseudo-asset for this source
            asset = session.execute(
                select(GameplayAsset).where(GameplayAsset.source_id == src.id).limit(1)
            ).scalars().first()

            if asset is None:
                # No asset registered — create a pseudo-asset for this source
                # so we can build a SelectedClip from the event's time range
                asset = GameplayAsset(
                    source_id=src.id,
                    label=f"auto:{src.filename}",
                    start_sec=0.0,
                    end_sec=src.duration or 0.0,
                    duration=src.duration or 0.0,
                    used_count=0,
                )
                session.add(asset)
                session.flush()

            clip = SelectedClip(
                asset=asset,
                source_path=src.file_path,
                start_sec=ev.start_time,
                end_sec=ev.start_time + clip_duration,
                duration=clip_duration,
                scene_index=scene_idx,
            )
            clips.append(clip)
            total += clip_duration

            # Advance scene index if using scene_duration
            if scene_duration > 0 and clip_duration >= scene_duration - 0.1:
                scene_idx += 1
            elif scene_duration == 0:
                scene_idx += 1

        log.info(
            f"semantic retrieval: {len(clips)} clips from {len(compatible_sources)} sources, "
            f"{total:.1f}s / {target_duration:.1f}s target"
        )

        # If we didn't fill the target, supplement with random selection
        if total < target_duration - 0.5:
            remaining = target_duration - total
            log.info(f"supplementing with {remaining:.1f}s of random selection")
            supplement = self.fallback_selector.select(
                session, game_id, remaining,
                scene_duration=scene_duration, rng=rng,
            )
            # Adjust scene indices for supplements
            for clip in supplement:
                clip.scene_index += scene_idx
                clips.append(clip)

        return clips
