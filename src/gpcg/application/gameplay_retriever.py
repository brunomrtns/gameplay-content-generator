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

from gpcg.application.clip_usage_service import (
    get_used_ranges,
    is_range_available,
    is_range_eligible,
    count_overlapping_uses,
    UsedRange,
)
from gpcg.application.gameplay_index_service import GameplayIndexService
from gpcg.application.gameplay_selector import GameplaySelector, SelectedClip
from gpcg.config import get_settings
from gpcg.domain.creative_plan import VideoCreativePlan
from gpcg.domains.games.models import (
    ContentScope,
    Game,
    GameplayEvent,
    GameplaySource,
)
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
        narrative_beats: Optional[list] = None,
        recent_game_ids: Optional[list[int]] = None,
        max_uses: int = 1,
        gameplay_preference_game_id: Optional[int] = None,
    ) -> list[SelectedClip]:
        """Retrieve gameplay clips for a video.

        Args:
            session: DB session
            game_id: game to retrieve gameplay from. V2: accepts list[int]
                     for cross-game retrieval. If int, wraps to [game_id].
                     NOTE: for GENERAL_TOPIC videos, the source search is
                     expanded to ALL gameplay sources the user has access to
                     (the subject is NOT about the game, so any gameplay can
                     serve as background). game_id is only used as a fallback.
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
            narrative_beats: narrative beats from the creative plan (used to
                     score source fit for GENERAL_TOPIC source selection).
                     If None, falls back to creative_plan.narrative_beats.
            recent_game_ids: game_ids that appeared in recent videos (for
                     diversity penalty in source scoring). Optional.

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

        # If user specified a gameplay preference (chose a specific game),
        # override the game_ids to only that game. This takes precedence
        # over the automatic GENERAL_TOPIC source selection.
        if gameplay_preference_game_id is not None:
            game_ids = [gameplay_preference_game_id]
            primary_game_id = gameplay_preference_game_id
            # Force video_type to GAME_RELATED so _retrieve_semantic filters
            # by game_id instead of searching ALL sources (GENERAL_TOPIC).
            # The user chose this game — we only use its gameplay.
            if video_type == "GENERAL_TOPIC":
                video_type = "GAME_RELATED"
                log.info(f"gameplay_preference: user chose game #{gameplay_preference_game_id}, "
                         f"switching GENERAL_TOPIC → GAME_RELATED for source filtering")

        # Decide whether to use semantic retrieval or fallback
        use_semantic = self._should_use_semantic(session, game_ids, creative_plan, video_type, user_id)

        if use_semantic and creative_plan is not None:
            clips = self._retrieve_semantic(
                session, game_ids, target_duration, creative_plan, scene_duration, rng,
                user_id=user_id, accept_public=accept_public,
                narrative_beats=narrative_beats, recent_game_ids=recent_game_ids,
                max_uses=max_uses, video_type=video_type,
            )
            if clips:
                return clips
            # If semantic retrieval found nothing, fall back
            log.info(f"semantic retrieval found no clips for games {game_ids}, falling back to random")

        # Fallback: random selection via GameplaySelector
        # V2: pass user_id and accept_public to the selector
        # V3: pass max_uses for configurable reuse policy
        if len(game_ids) > 1:
            # Cross-game fallback: try expanded games, then contract to primary
            for gid in game_ids:
                clips = self.fallback_selector.select(
                    session, gid, target_duration,
                    scene_duration=scene_duration, rng=rng,
                    user_id=user_id, accept_public=accept_public,
                    max_uses=max_uses,
                )
                if clips:
                    return clips
            # All games failed — try primary as last resort
            return self.fallback_selector.select(
                session, primary_game_id, target_duration,
                scene_duration=scene_duration, rng=rng,
                user_id=user_id, accept_public=accept_public,
                max_uses=max_uses,
            )
        return self.fallback_selector.select(
            session, primary_game_id, target_duration,
            scene_duration=scene_duration, rng=rng,
            user_id=user_id, accept_public=accept_public,
            max_uses=max_uses,
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

        # For GENERAL_TOPIC, search ALL gameplay sources the user has access to
        # (the subject is NOT about the game, so any gameplay can be background).
        # For GAME_RELATED, keep filtering by game_ids (the video IS about that game).
        if video_type == "GENERAL_TOPIC":
            query = select(GameplaySource).where(
                GameplaySource.ingestion_status == "ready",
                GameplaySource.enabled == True,
            )
        else:
            query = select(GameplaySource).where(
                GameplaySource.game_id.in_(game_ids),
                GameplaySource.ingestion_status == "ready",
                GameplaySource.enabled == True,
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

    def _beat_event_mapping(self, beats: list) -> set[str]:
        """Map narrative beat tones/energies to preferred event types.

        This is a SOFT signal (not a hard filter) used to score source fit.

        High energy beats (hook, payoff, escalation) → {COMBAT, CHASE, VEHICLE}
        Medium energy beats (development, context) → {EXPLORATION, TRAVEL, INTERACTION}
        Low energy beats (conclusion, transition, others) → {DIALOGUE, CUTSCENE, IDLE}

        Args:
            beats: list of NarrativeBeat objects (or dicts with a "label" key).

        Returns:
            Set of preferred event_type strings.
        """
        preferred: set[str] = set()
        high_energy = {"hook", "payoff", "escalation"}
        medium_energy = {"development", "context"}
        for beat in beats:
            # Support both NarrativeBeat dataclass and plain dict
            label = getattr(beat, "label", None)
            if label is None and isinstance(beat, dict):
                label = beat.get("label", "")
            label = (label or "").lower()
            if label in high_energy:
                preferred.update({"COMBAT", "CHASE", "VEHICLE"})
            elif label in medium_energy:
                preferred.update({"EXPLORATION", "TRAVEL", "INTERACTION"})
            else:
                preferred.update({"DIALOGUE", "CUTSCENE", "IDLE"})
        return preferred

    def _score_source_fit(
        self,
        session: Session,
        source: GameplaySource,
        narrative_beats: list,
        video_type: str,
        topic_game_id: Optional[int],
        consumer_user_id: Optional[int] = None,
        recent_game_ids: Optional[list[int]] = None,
    ) -> float:
        """Score a gameplay source by how well its events fit the narrative.

        Scoring components (max ~110):
          1. Event type coverage: fraction of events matching preferred types
             from the narrative beats (0-40 points)
          2. Interesting score: average interesting_score of events (0-20)
          3. Available clips: count of event time ranges NOT in cooldown/USED
             for this consumer (0-20 points)
          4. Game relevance bonus: +20 if GAME_RELATED and source.game_id ==
             topic_game_id (the gameplay IS about the game being discussed)
          5. Visual confidence: average visual_confidence of events (0-10)
          6. Diversity penalty: -10 per recent appearance of source.game_id in
             recent_game_ids

        Args:
            session: DB session
            source: the GameplaySource to score
            narrative_beats: narrative beats (NarrativeBeat or dict)
            video_type: GAME_RELATED or GENERAL_TOPIC
            topic_game_id: the game_id the video is about (for relevance bonus)
            consumer_user_id: consumer for USED/cooldown checks
            recent_game_ids: game_ids seen in recent videos (diversity penalty)

        Returns:
            Float score (higher = better fit).
        """
        events = session.execute(
            select(GameplayEvent).where(GameplayEvent.source_id == source.id)
        ).scalars().all()
        if not events:
            return 0.0

        n_events = len(events)
        score = 0.0

        # 1. Event type coverage (0-40)
        preferred_types = self._beat_event_mapping(narrative_beats)
        if preferred_types:
            matching = sum(1 for e in events if e.event_type in preferred_types)
            coverage = matching / n_events
            score += coverage * 40.0
        else:
            # No beats → neutral midpoint
            score += 20.0

        # 2. Interesting score (0-20)
        avg_interesting = sum(e.interesting_score for e in events) / n_events
        score += avg_interesting * 20.0

        # 3. Available clips (0-20) — count ranges NOT in USED/cooldown
        settings = get_settings()
        cooldown_sec = settings.gpcg_gameplay_cooldown_sec
        used_ranges = get_used_ranges(
            session, source.id, consumer_user_id=consumer_user_id,
        )
        available = 0
        for e in events:
            if not is_range_available(used_ranges, e.start_time, e.end_time, tolerance=1.0):
                continue
            # Check cooldown: skip if within cooldown window of any used region
            in_cooldown = False
            for ur in used_ranges:
                if ur.end_sec > 0 and e.start_time < ur.end_sec + cooldown_sec and e.end_time > ur.start_sec - cooldown_sec:
                    in_cooldown = True
                    break
            if not in_cooldown:
                available += 1
        score += (available / n_events) * 20.0

        # 4. Game relevance bonus (+20)
        if video_type == "GAME_RELATED" and source.game_id == topic_game_id:
            score += 20.0

        # 5. Visual confidence (0-10)
        avg_vc = sum(e.visual_confidence for e in events) / n_events
        score += avg_vc * 10.0

        # 6. Diversity penalty (-10 per recent appearance)
        if recent_game_ids and source.game_id is not None:
            score -= 10.0 * recent_game_ids.count(source.game_id)

        return score

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
        narrative_beats: Optional[list] = None,
        recent_game_ids: Optional[list[int]] = None,
        max_uses: int = 1,
        video_type: str = "GAME_RELATED",
    ) -> list[SelectedClip]:
        """Retrieve clips using the semantic index.

        V2: searches across multiple game_ids (cross-game).
        V2: filters by user_id with public fallback.
        V3: For GENERAL_TOPIC, considers ALL gameplay sources the user has
            access to, scores each by narrative fit, and selects the ONE best
            source. Clips come ONLY from that source (one source per video).

        Strategy:
        1. Find sources with ready analysis + compatible
        2. Query events matching the plan's gameplay_query (if any)
        3. If no query or no matches, get the most interesting events
        4. Build SelectedClips from event time ranges
        5. If total < target_duration, fill remaining with random selection
           (GAME_RELATED only — GENERAL_TOPIC never mixes sources)
        """
        # Resolve narrative beats (explicit param or from the plan)
        beats = narrative_beats if narrative_beats is not None else plan.narrative_beats
        topic_game_id = game_ids[0] if game_ids else None

        # Find compatible sources with ready analysis.
        # For GENERAL_TOPIC: search ALL gameplay sources (any game) the user
        # has access to — the subject is NOT about the game, so any gameplay
        # can serve as background. Then score and pick the ONE best source.
        # For GAME_RELATED: keep filtering by game_ids (the video IS about
        # that game, so its gameplay is preferred).
        # NOTE: we check the effective video_type (the parameter), not
        # plan.video_type, because gameplay_preference may have overridden
        # GENERAL_TOPIC → GAME_RELATED to force filtering by the chosen game.
        if video_type == "GENERAL_TOPIC" and plan.video_type == "GENERAL_TOPIC":
            query = select(GameplaySource).where(
                GameplaySource.ingestion_status == "ready",
                GameplaySource.enabled == True,
            )
        else:
            query = select(GameplaySource).where(
                GameplaySource.game_id.in_(game_ids),
                GameplaySource.ingestion_status == "ready",
                GameplaySource.enabled == True,
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
            if video_type == "GAME_RELATED" and not compat.get("game_related", True):
                continue
            if video_type == "GENERAL_TOPIC" and not compat.get("general_topic", True):
                continue
            compatible_sources.append(src)

        if not compatible_sources:
            return []

        # V3: For GENERAL_TOPIC, score each source and select the ONE best fit.
        # This implements intelligent source selection based on narrative fit
        # rather than treating all compatible sources equally.
        # NOTE: only when the effective video_type is still GENERAL_TOPIC
        # (gameplay_preference may have forced GAME_RELATED — in that case
        # we already filtered to the chosen game's sources above).
        if video_type == "GENERAL_TOPIC" and plan.video_type == "GENERAL_TOPIC" and len(compatible_sources) > 1:
            scored = []
            for src in compatible_sources:
                fit = self._score_source_fit(
                    session, src, beats, plan.video_type, topic_game_id,
                    consumer_user_id=user_id, recent_game_ids=recent_game_ids,
                )
                scored.append((fit, src))
            scored.sort(key=lambda x: x[0], reverse=True)
            best_src = scored[0][1]
            log.info(
                f"GENERAL_TOPIC source selection: {len(compatible_sources)} candidates, "
                f"best=source#{best_src.id} (game={best_src.game_id}) "
                f"score={scored[0][0]:.1f}"
            )
            compatible_sources = [best_src]

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

        # Load persisted clip usage history from the DB (per consumer).
        # This is the KEY fix: without this, each job selected clips without
        # knowing what was already used in previous videos.
        settings = get_settings()
        cooldown_sec = settings.gpcg_gameplay_cooldown_sec
        persisted_used: dict[int, list[UsedRange]] = {}
        for src in compatible_sources:
            persisted_used[src.id] = get_used_ranges(
                session, src.id, consumer_user_id=user_id,
            )
        log.info(
            f"semantic retrieval: loaded usage history for {len(persisted_used)} sources, "
            f"cooldown={cooldown_sec}s, consumer={user_id}"
        )

        # Track used event time ranges to avoid picking overlapping segments
        # within this selection AND across previous videos (persisted history).
        used_ranges: list[tuple[float, float, int]] = []  # (start, end, source_id)

        def _is_used(ev_start: float, ev_end: float, src_id: int) -> bool:
            # Check intra-selection overlaps (clips already picked in this job)
            for ur_start, ur_end, ur_src in used_ranges:
                if ur_src == src_id and ev_start < ur_end and ev_end > ur_start:
                    return True
            # Check persisted history with configurable reuse policy
            history = persisted_used.get(src_id, [])
            if not is_range_eligible(history, ev_start, ev_end, max_uses=max_uses, tolerance=1.0):
                return True
            return False

        def _count_uses(ev_start: float, ev_end: float, src_id: int) -> int:
            """Count how many existing uses overlap this range (for auditability)."""
            history = persisted_used.get(src_id, [])
            return count_overlapping_uses(history, ev_start, ev_end, tolerance=1.0)

        def _is_in_cooldown(ev_start: float, ev_end: float, src_id: int) -> bool:
            """Check if event is within cooldown window of any used region."""
            history = persisted_used.get(src_id, [])
            for ur in history:
                if ur.end_sec > 0 and ev_start < ur.end_sec + cooldown_sec and ev_end > ur.start_sec - cooldown_sec:
                    return True
            return False

        # Build clips from events, accumulating until target_duration.
        # Two-pass strategy:
        #   Pass 1: prefer events NOT in cooldown (far from previously used regions)
        #   Pass 2: if target not filled, accept events in cooldown (but still
        #           block significant overlaps with used ranges)
        clips: list[SelectedClip] = []
        total = 0.0
        scene_idx = 0
        scene_accum = 0.0  # accumulated duration within current scene
        from gpcg.domains.games.models import GameplayAsset

        # Cache assets per source to avoid repeated queries
        asset_cache: dict[int, GameplayAsset] = {}

        def _get_asset(src: GameplaySource) -> GameplayAsset:
            if src.id not in asset_cache:
                asset = session.execute(
                    select(GameplayAsset).where(GameplayAsset.source_id == src.id).limit(1)
                ).scalars().first()
                if asset is None:
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
                asset_cache[src.id] = asset
            return asset_cache[src.id]

        def _try_select(src: GameplaySource, ev: GameplayEvent) -> bool:
            """Try to select an event as a clip. Returns True if selected."""
            nonlocal total, scene_idx, scene_accum
            if total >= target_duration:
                return False
            if _is_used(ev.start_time, ev.end_time, src.id):
                return False
            event_duration = ev.end_time - ev.start_time
            if event_duration <= 0:
                return False
            clip_duration = event_duration
            if scene_duration > 0:
                scene_remaining = scene_duration - scene_accum
                clip_duration = min(event_duration, scene_remaining)
            remaining = target_duration - total
            clip_duration = min(clip_duration, remaining)
            if clip_duration < 0.5:
                return False
            asset = _get_asset(src)
            n_uses = _count_uses(ev.start_time, ev.start_time + clip_duration, src.id)
            reason = "semantic_event" if n_uses == 0 else "semantic_event_reused"
            clip = SelectedClip(
                asset=asset,
                source_path=src.file_path,
                start_sec=ev.start_time,
                end_sec=ev.start_time + clip_duration,
                duration=clip_duration,
                scene_index=scene_idx,
                event_id=ev.id,
                selection_reason=reason,
                usage_count_at_selection=n_uses,
            )
            clips.append(clip)
            total += clip_duration
            used_ranges.append((ev.start_time, ev.start_time + clip_duration, src.id))
            if scene_duration > 0:
                scene_accum += clip_duration
                if scene_accum >= scene_duration - 0.1:
                    scene_idx += 1
                    scene_accum = 0.0
            elif scene_duration == 0:
                scene_idx += 1
            return True

        # Pass 1: select events that are NOT in cooldown
        for src, ev in all_events:
            if total >= target_duration:
                break
            if _is_in_cooldown(ev.start_time, ev.end_time, src.id):
                continue
            _try_select(src, ev)

        # Pass 2: if target not filled, accept events in cooldown (but still
        # block significant overlaps)
        if total < target_duration - 0.5:
            log.info(f"pass 1 filled {total:.1f}s/{target_duration:.1f}s, "
                     f"accepting cooldown events for remaining")
            for src, ev in all_events:
                if total >= target_duration:
                    break
                _try_select(src, ev)

        log.info(
            f"semantic retrieval: {len(clips)} clips from {len(compatible_sources)} sources, "
            f"{total:.1f}s / {target_duration:.1f}s target"
        )

        # If we didn't fill the target, supplement with random selection.
        # V3: For GENERAL_TOPIC, do NOT mix with other sources — respect the
        # "one source per video" rule. Just use what's available from the
        # selected source.
        if total < target_duration - 0.5 and plan.video_type != "GENERAL_TOPIC":
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
        elif total < target_duration - 0.5 and plan.video_type == "GENERAL_TOPIC":
            log.info(
                f"GENERAL_TOPIC: selected source filled {total:.1f}s/{target_duration:.1f}s, "
                f"not mixing with other sources (one source per video rule)"
            )

        return clips
