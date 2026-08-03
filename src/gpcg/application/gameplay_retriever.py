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

V2: Supports cross-game retrieval via game_ids: list[int]. When
GPCG_CROSS_GAME_GAMEPLAY_ENABLED is on, _expand_game_ids expands the
game_id to include games in the same franchise/developer.

Key principle: clips are NOT extracted physically here. Only temporal
references (start_sec, end_sec) are returned. The render stage extracts
the actual video segments on-demand.
"""

from __future__ import annotations

import random
from typing import Optional, Union

from sqlalchemy import select
from sqlalchemy.orm import Session

from gpcg.application.gameplay_index_service import GameplayIndexService
from gpcg.application.gameplay_selector import GameplaySelector, SelectedClip
from gpcg.config import get_settings
from gpcg.domain.creative_plan import VideoCreativePlan
from gpcg.domain.models import ContentScope, Game, GameplayEvent, GameplaySource
from gpcg.logging import get_logger

log = get_logger(__name__)


def _expand_game_ids(
    session: Session,
    game_id: int,
    scope: str = ContentScope.game.value,
) -> list[int]:
    """Expand a game_id to include games in the same franchise/developer.

    V2: Cross-game expansion per ARCHITECTURE_V2.md §8.2.

    Args:
        session: DB session
        game_id: the primary game ID
        scope: "game" (no expansion), "franchise", or "developer"

    Returns:
        List of game IDs to search for gameplay. Always includes game_id.
    """
    game = session.get(Game, game_id)
    if not game or scope == ContentScope.game.value:
        return [game_id]

    ids = [game_id]
    if scope == ContentScope.franchise.value and game.franchise:
        related = session.execute(
            select(Game.id).where(
                Game.franchise == game.franchise,
                Game.id != game_id,
            )
        ).scalars().all()
        ids.extend(related)
    elif scope == ContentScope.developer.value and game.developer:
        related = session.execute(
            select(Game.id).where(
                Game.developer == game.developer,
                Game.id != game_id,
            )
        ).scalars().all()
        ids.extend(related)

    return ids


class GameplayRetriever:
    """Retrieves gameplay clips using the semantic index when available.

    Falls back to GameplaySelector (random) when:
    - No VideoCreativePlan is provided
    - The plan's strategy is "background_filler"
    - No analyzed gameplay events exist for the game

    V2: Supports cross-game retrieval via game_ids list.
    """

    def __init__(self, index_service: Optional[GameplayIndexService] = None) -> None:
        self.index_service = index_service or GameplayIndexService()
        self.fallback_selector = GameplaySelector()

    def retrieve(
        self,
        session: Session,
        game_id: Union[int, list[int]],
        target_duration: float,
        *,
        creative_plan: Optional[VideoCreativePlan] = None,
        scene_duration: float = 0.0,
        video_type: str = "GAME_RELATED",
        rng: Optional[random.Random] = None,
        scope: str = ContentScope.game.value,
        user_id: Optional[int] = None,
        accept_public: bool = False,
    ) -> list[SelectedClip]:
        """Retrieve gameplay clips for a video.

        Args:
            session: DB session
            game_id: game to retrieve gameplay from. V2: accepts list[int]
                     for cross-game retrieval. If int, wraps to [game_id].
            target_duration: total duration to fill (narration duration)
            creative_plan: the editorial plan (may be None for fallback)
            scene_duration: scene grouping duration (0 = legacy mode)
            video_type: GAME_RELATED or GENERAL_TOPIC (for compatibility check)
            rng: random generator (for deterministic tests)
            scope: V2 cross-game scope — "game", "franchise", or "developer".
                    Only used when GPCG_CROSS_GAME_GAMEPLAY_ENABLED is on.
            user_id: V2 — if provided, filter gameplays by this user first,
                     with public fallback when accept_public=True.
            accept_public: V2 — if True and user_id is set, fall back to public
                           gameplays when user's own are exhausted.

        Returns:
            List of SelectedClip with temporal references to gameplay segments
        """
        rng = rng or random.Random()

        # V2: Normalize game_id to list and expand if cross-game is enabled
        if isinstance(game_id, list):
            game_ids = game_id
        else:
            game_ids = [game_id]
            # V2: expand game_ids if cross-game is enabled
            settings = get_settings()
            if settings.gpcg_cross_game_gameplay_enabled and scope != ContentScope.game.value:
                expanded = _expand_game_ids(session, game_id, scope)
                if len(expanded) > 1:
                    log.info(f"cross-game expansion: game #{game_id} scope={scope} → {expanded}")
                    game_ids = expanded

        # Use the first game_id for compatibility checks (primary game)
        primary_game_id = game_ids[0] if game_ids else game_id

        # Decide whether to use semantic retrieval or fallback
        use_semantic = self._should_use_semantic(session, game_ids, creative_plan, video_type, user_id)

        if use_semantic and creative_plan is not None:
            clips = self._retrieve_semantic(
                session, game_ids, target_duration, creative_plan, scene_duration, rng,
                user_id=user_id, accept_public=accept_public,
            )
            if clips:
                return clips
            # If semantic retrieval found nothing, fall back
            log.info(f"semantic retrieval found no clips for games {game_ids}, falling back to random")

        # Fallback: random selection via GameplaySelector
        # V2: pass user_id and accept_public to the selector
        if len(game_ids) > 1:
            # Cross-game fallback: try expanded games, then contract to primary
            for gid in game_ids:
                clips = self.fallback_selector.select(
                    session, gid, target_duration,
                    scene_duration=scene_duration, rng=rng,
                    user_id=user_id, accept_public=accept_public,
                )
                if clips:
                    return clips
            # All games failed — try primary as last resort
            return self.fallback_selector.select(
                session, primary_game_id, target_duration,
                scene_duration=scene_duration, rng=rng,
                user_id=user_id, accept_public=accept_public,
            )
        return self.fallback_selector.select(
            session, primary_game_id, target_duration,
            scene_duration=scene_duration, rng=rng,
            user_id=user_id, accept_public=accept_public,
        )

    def _should_use_semantic(
        self,
        session: Session,
        game_ids: list[int],
        plan: Optional[VideoCreativePlan],
        video_type: str,
        user_id: Optional[int] = None,
    ) -> bool:
        """Check if semantic retrieval should be used."""
        if plan is None or not plan.success:
            return False

        # background_filler = gameplay is just visual background, no semantic match needed
        if plan.gameplay_strategy == "background_filler":
            return False

        # V2: check sources across all game_ids (cross-game)
        query = select(GameplaySource).where(
            GameplaySource.game_id.in_(game_ids),
            GameplaySource.ingestion_status == "ready",
        )
        # V2: filter by user_id if provided
        if user_id is not None:
            query = query.where(GameplaySource.user_id == user_id)

        sources = session.execute(query).scalars().all()

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
        game_ids: list[int],
        target_duration: float,
        plan: VideoCreativePlan,
        scene_duration: float,
        rng: random.Random,
        user_id: Optional[int] = None,
        accept_public: bool = False,
    ) -> list[SelectedClip]:
        """Retrieve clips using the semantic index.

        V2: searches across multiple game_ids (cross-game).
        V2: filters by user_id with public fallback.

        Strategy:
        1. Find sources with ready analysis + compatible
        2. Query events matching the plan's gameplay_query (if any)
        3. If no query or no matches, get the most interesting events
        4. Build SelectedClips from event time ranges
        5. If total < target_duration, fill remaining with random selection
        """
        # Find compatible sources with ready analysis (V2: across all game_ids)
        query = select(GameplaySource).where(
            GameplaySource.game_id.in_(game_ids),
            GameplaySource.ingestion_status == "ready",
        )
        # V2: filter by user_id if provided
        if user_id is not None:
            query = query.where(GameplaySource.user_id == user_id)

        sources = session.execute(query).scalars().all()

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
            log.info(f"no interesting events (score>=0.3) for games {game_ids}, "
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

        # Sort by interesting score (descending) but add randomization
        # to avoid always picking the same clips. We group events into
        # score tiers and shuffle within each tier.
        rng.shuffle(all_events)
        all_events.sort(key=lambda x: x[1].interesting_score, reverse=True)
        # Re-shuffle within score tiers to add variety while still preferring
        # higher-scoring ones. Events within 0.1 of each other are same tier.
        tiered: list = []
        current_tier: list = []
        current_score = None
        for ev in all_events:
            score = ev[1].interesting_score
            if current_score is None or abs(score - current_score) < 0.1:
                current_tier.append(ev)
                current_score = score
            else:
                rng.shuffle(current_tier)
                tiered.extend(current_tier)
                current_tier = [ev]
                current_score = score
        if current_tier:
            rng.shuffle(current_tier)
            tiered.extend(current_tier)
        all_events = tiered

        # Track used event time ranges to avoid picking overlapping segments
        used_ranges: list[tuple[float, float, int]] = []  # (start, end, source_id)

        def _is_used(ev_start: float, ev_end: float, src_id: int) -> bool:
            for ur_start, ur_end, ur_src in used_ranges:
                if ur_src == src_id and ev_start < ur_end and ev_end > ur_start:
                    return True
            return False

        # Build clips from events, accumulating until target_duration
        clips: list[SelectedClip] = []
        total = 0.0
        scene_idx = 0

        for src, ev in all_events:
            if total >= target_duration:
                break

            # Skip events that overlap with already-selected clips
            if _is_used(ev.start_time, ev.end_time, src.id):
                continue

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

            # Track the used range to avoid overlapping clips
            used_ranges.append((ev.start_time, ev.start_time + clip_duration, src.id))

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
            # V2: try primary game first, then other game_ids
            primary_game_id = game_ids[0] if game_ids else 0
            supplement = self.fallback_selector.select(
                session, primary_game_id, remaining,
                scene_duration=scene_duration, rng=rng,
            )
            # Adjust scene indices for supplements
            for clip in supplement:
                clip.scene_index += scene_idx
                clips.append(clip)

        return clips
