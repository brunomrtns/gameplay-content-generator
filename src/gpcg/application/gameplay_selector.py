"""Gameplay selection — pick clips to fill the narration duration.

Two modes:
1. **Legacy (scene_duration=0)**: Each clip's duration = asset duration.
   Accumulate clips until total >= target_duration.
2. **Scene-based (scene_duration > 0)**: Group clips into scenes of
   scene_duration each. For each scene:
   - Pick a random gameplay video and a random start point
   - If the segment fits within the video: take a contiguous segment
   - If it overflows: chain multiple videos to fill the scene duration
   - If scene_duration >= target_duration: just 1 scene covering the whole video

The scene-based mode supports the "long single scene" use case where the user
wants one contiguous gameplay segment (e.g. scene_duration=7200 for 2h, but
the actual video is only 60s — so we take a 60s segment from a random point).

Each SelectedClip represents one contiguous segment from one source video.
A scene may consist of multiple SelectedClips (when chaining).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from gpcg.domains.games.models import GameplayAsset, GameplaySource
from gpcg.application.clip_usage_service import (
    get_used_ranges,
    find_available_segment,
)
from gpcg.domain.visibility import gameplay_visible_to_user
from gpcg.logging import get_logger

log = get_logger(__name__)


@dataclass
class SelectedClip:
    """A single contiguous segment from one gameplay source video."""
    asset: GameplayAsset
    source_path: str
    start_sec: float
    end_sec: float
    duration: float
    # For scene-based mode: which scene this clip belongs to (0-indexed)
    scene_index: int = 0
    # Auditability: which GameplayEvent informed this clip's boundaries (if any)
    event_id: Optional[int] = None
    # Why this clip was selected: "semantic_event", "event_aligned_fallback",
    # "random_fallback", "semantic_event_reused"
    selection_reason: str = ""
    # How many times this region was already used by this consumer at selection time
    usage_count_at_selection: int = 0


@dataclass
class Scene:
    """A scene = one or more clips that play sequentially.
    When scene_duration > individual video duration, a scene chains multiple clips.
    """
    index: int
    clips: list[SelectedClip] = field(default_factory=list)
    target_duration: float = 0.0

    @property
    def duration(self) -> float:
        return sum(c.duration for c in self.clips)

    @property
    def source_paths(self) -> list[str]:
        return [c.source_path for c in self.clips]


class GameplaySelector:
    """Selects gameplay assets to cover the target duration.

    V2: Supports user-scoped gameplay selection with public fallback.
    - First tries the user's own gameplays (user_id match).
    - If the user's gameplays are exhausted (all segments used), falls back
      to public gameplays from other users (is_public=True).
    - Uses GameplayClipUsage to avoid reusing the same time ranges.
    """

    def select(
        self,
        session: Session,
        game_id: int,
        target_duration: float,
        *,
        scene_duration: float = 0.0,
        rng: Optional[random.Random] = None,
        user_id: Optional[int] = None,
        accept_public: bool = False,
        max_uses: int = 1,
    ) -> list[SelectedClip]:
        """Select clips totaling ~target_duration seconds.

        Args:
            session: DB session.
            game_id: Game to select gameplay from.
            target_duration: Total duration to fill (narration duration).
            scene_duration: Target duration of each scene. 0 = legacy mode
                (use asset durations). >0 = scene-based mode with chaining.
            rng: Random generator (for deterministic tests).
            user_id: If provided, filter gameplays by this user first.
            accept_public: If True and user_id is set, fall back to public
                gameplays when the user's own are exhausted.
            max_uses: Reuse policy. 1 = strict (default), 0 = unlimited.

        Returns:
            List of SelectedClip, each with scene_index indicating which scene
            it belongs to. Clips in the same scene should be concatenated.
        """
        rng = rng or random.Random()

        # V2: Try user's own gameplays first
        if user_id is not None:
            clips = self._select_with_filters(
                session, game_id, target_duration,
                scene_duration=scene_duration, rng=rng,
                user_id=user_id, public_only=False,
                accept_public=accept_public,
                max_uses=max_uses,
            )
            if clips:
                return clips

            # Fallback: public gameplays from other users
            if accept_public:
                log.info(
                    f"no available own gameplay for game #{game_id} user #{user_id}, "
                    f"falling back to public gameplays"
                )
                clips = self._select_with_filters(
                    session, game_id, target_duration,
                    scene_duration=scene_duration, rng=rng,
                    user_id=user_id, public_only=True,
                    max_uses=max_uses,
                )
                return clips

            log.warning(f"no available own gameplay for game #{game_id} user #{user_id}")
            return []

        # Legacy: no user_id filter (backward compat)
        return self._select_with_filters(
            session, game_id, target_duration,
            scene_duration=scene_duration, rng=rng,
            user_id=None, public_only=False,
            max_uses=max_uses,
        )

    def _select_with_filters(
        self,
        session: Session,
        game_id: int,
        target_duration: float,
        *,
        scene_duration: float,
        rng: random.Random,
        user_id: Optional[int],
        public_only: bool,
        accept_public: bool = False,
        max_uses: int = 1,
    ) -> list[SelectedClip]:
        """Internal: select clips with user/public filters applied."""
        # Build query for assets
        query = (
            select(GameplayAsset)
            .join(GameplaySource, GameplayAsset.source_id == GameplaySource.id)
            .where(GameplaySource.ingestion_status == "ready")
            .where(GameplaySource.enabled == True)
        )

        # For GENERAL_TOPIC (game_id=None), don't filter by game_id —
        # any gameplay can serve as background. NULL game_id in SQL
        # never matches (= NULL is always false), so we must skip the
        # filter entirely.
        if game_id is not None:
            query = query.where(GameplaySource.game_id == game_id)

        if public_only:
            # Public gameplays from OTHER users
            query = query.where(
                GameplaySource.is_public == True,
                GameplaySource.user_id != user_id,
            )
        elif user_id is not None:
            # User's own gameplays, plus public gameplays when accept_public
            query = query.where(
                gameplay_visible_to_user(
                    GameplaySource.user_id,
                    GameplaySource.is_public,
                    user_id,
                    allows_public=accept_public,
                )
            )

        query = query.order_by(GameplayAsset.used_count.asc())
        assets = session.execute(query).scalars().all()

        if not assets:
            if public_only:
                log.info(f"no public gameplay assets for game #{game_id}")
            else:
                log.warning(f"no gameplay assets for game #{game_id} user={user_id}")
            return []

        # Preload source paths and used ranges
        # REFACTORY_V2: filter used ranges by consumer_user_id so that public
        # gameplay usage by user A doesn't block user B from the same segment.
        source_cache: dict[int, GameplaySource] = {}
        used_ranges_cache: dict[int, list] = {}
        # V3: preload GameplayEvent boundaries for event-aware fallback selection
        event_boundaries_cache: dict[int, list[tuple[float, float]]] = {}
        for a in assets:
            if a.source_id not in source_cache:
                src = session.get(GameplaySource, a.source_id)
                source_cache[a.source_id] = src
                used_ranges_cache[a.source_id] = get_used_ranges(
                    session, a.source_id, consumer_user_id=user_id,
                )
                # Load event boundaries for this source (for coherent cuts)
                from gpcg.domains.games.models import GameplayEvent
                evs = session.execute(
                    select(GameplayEvent.start_time, GameplayEvent.end_time)
                    .where(GameplayEvent.source_id == a.source_id)
                    .where(GameplayEvent.interesting_score >= 0.3)
                    .order_by(GameplayEvent.start_time)
                ).all()
                event_boundaries_cache[a.source_id] = [(r[0], r[1]) for r in evs]

        if scene_duration > 0:
            clips = self._select_scene_based(
                assets, source_cache, target_duration, scene_duration, rng,
                used_ranges_cache=used_ranges_cache,
                event_boundaries_cache=event_boundaries_cache,
                max_uses=max_uses,
            )
        else:
            clips = self._select_legacy(
                assets, source_cache, target_duration, rng,
                used_ranges_cache=used_ranges_cache,
                event_boundaries_cache=event_boundaries_cache,
                max_uses=max_uses,
            )

        total = sum(c.duration for c in clips)
        n_scenes = max(c.scene_index for c in clips) + 1 if clips else 0
        scope = "public" if public_only else f"user={user_id}" if user_id else "global"
        log.info(
            f"selected {len(clips)} clip(s) in {n_scenes} scene(s) "
            f"totaling {total:.1f}s for target {target_duration:.1f}s "
            f"(scene_duration={scene_duration}s, scope={scope})"
        )
        return clips

    def _select_legacy(
        self,
        assets: list[GameplayAsset],
        source_cache: dict[int, GameplaySource],
        target_duration: float,
        rng: random.Random,
        used_ranges_cache: Optional[dict[int, list]] = None,
        event_boundaries_cache: Optional[dict[int, list[tuple[float, float]]]] = None,
        max_uses: int = 1,
    ) -> list[SelectedClip]:
        """Legacy mode: each clip = one asset, accumulate until target.

        V2: Uses find_available_segment to avoid already-used time ranges.
        V3: Passes event_boundaries for coherent cuts + max_uses for reuse policy.
        Used ranges are in absolute source coordinates; we convert to
        asset-local coordinates for the availability check.
        """
        from gpcg.application.clip_usage_service import UsedRange, count_overlapping_uses
        used_ranges_cache = used_ranges_cache or {}
        event_boundaries_cache = event_boundaries_cache or {}
        weights = [1.0 / (a.used_count + 1) for a in assets]
        selected: list[SelectedClip] = []
        total = 0.0
        last_source_id: Optional[int] = None
        max_iter = len(assets) * 5

        while total < target_duration and max_iter > 0:
            max_iter -= 1
            candidates = list(range(len(assets)))
            if last_source_id is not None and len(assets) > 1:
                no_repeat = [i for i in candidates if assets[i].source_id != last_source_id]
                if no_repeat:
                    candidates = no_repeat
            cand_weights = [weights[i] for i in candidates]
            idx = rng.choices(candidates, weights=cand_weights, k=1)[0]
            asset = assets[idx]
            source = source_cache[asset.source_id]

            # V2: Convert absolute used ranges to asset-local coordinates
            abs_used = used_ranges_cache.get(asset.source_id, [])
            local_used = []
            for ur in abs_used:
                # Convert to asset-local: subtract asset.start_sec
                local_start = ur.start_sec - asset.start_sec
                local_end = ur.end_sec - asset.start_sec
                # Clip to [0, asset.duration]
                if local_end <= 0 or local_start >= asset.duration:
                    continue  # No overlap with this asset
                local_used.append(UsedRange(
                    start_sec=max(0.0, local_start),
                    end_sec=min(asset.duration, local_end),
                ))

            needed = min(asset.duration, target_duration - total)

            # V3: Convert event boundaries to asset-local coordinates
            abs_events = event_boundaries_cache.get(asset.source_id, [])
            local_events = []
            for ev_start, ev_end in abs_events:
                ls = ev_start - asset.start_sec
                le = ev_end - asset.start_sec
                if le <= 0 or ls >= asset.duration:
                    continue
                local_events.append((max(0.0, ls), min(asset.duration, le)))

            # V2: If no used ranges, use full asset (legacy behavior).
            # Only use find_available_segment when there are used ranges to avoid.
            if not local_used and max_uses <= 1:
                start_offset = 0.0
                end_offset = asset.duration  # full asset, may overshoot target
            else:
                seg = find_available_segment(
                    asset.duration, needed, local_used, rng=rng,
                    event_boundaries=local_events if local_events else None,
                    max_uses=max_uses,
                )
                if seg is None:
                    continue
                start_offset, end_offset = seg

            # V3: Auditability — count existing uses for this region
            n_uses = count_overlapping_uses(abs_used, asset.start_sec + start_offset,
                                            asset.start_sec + end_offset)
            reason = "event_aligned_fallback" if local_events else "random_fallback"

            clip = SelectedClip(
                asset=asset,
                source_path=source.file_path,
                start_sec=asset.start_sec + start_offset,
                end_sec=asset.start_sec + end_offset,
                duration=end_offset - start_offset,
                scene_index=len(selected),
                selection_reason=reason,
                usage_count_at_selection=n_uses,
            )
            selected.append(clip)
            total += clip.duration
            last_source_id = asset.source_id
            # Track in absolute coordinates
            used_ranges_cache.setdefault(asset.source_id, []).append(
                UsedRange(
                    start_sec=asset.start_sec + start_offset,
                    end_sec=asset.start_sec + end_offset,
                )
            )

        return selected

    def _select_scene_based(
        self,
        assets: list[GameplayAsset],
        source_cache: dict[int, GameplaySource],
        target_duration: float,
        scene_duration: float,
        rng: random.Random,
        used_ranges_cache: Optional[dict[int, list]] = None,
        event_boundaries_cache: Optional[dict[int, list[tuple[float, float]]]] = None,
        max_uses: int = 1,
    ) -> list[SelectedClip]:
        """Scene-based mode: group clips into scenes of scene_duration each.

        For each scene:
        1. Pick a random gameplay video
        2. Find an available segment using find_available_segment
        3. If it fits: take contiguous segment
        4. If overflow: take available portion, chain another for remainder

        V2: Uses find_available_segment to avoid already-used time ranges.
        V3: Passes event_boundaries for coherent cuts + max_uses for reuse policy.
        Used ranges are in absolute source coordinates; we convert to
        asset-local coordinates for the availability check.
        """
        from gpcg.application.clip_usage_service import UsedRange, count_overlapping_uses
        used_ranges_cache = used_ranges_cache or {}
        event_boundaries_cache = event_boundaries_cache or {}
        weights = [1.0 / (a.used_count + 1) for a in assets]
        all_clips: list[SelectedClip] = []
        remaining_total = target_duration
        scene_idx = 0
        last_source_id: Optional[int] = None

        while remaining_total > 0.01:
            scene_target = min(scene_duration, remaining_total)
            scene_clips: list[SelectedClip] = []
            scene_remaining = scene_target
            consecutive_failures = 0
            max_failures = len(assets) * 3  # give up after exhausting all assets multiple times

            while scene_remaining > 0.01:
                if consecutive_failures >= max_failures:
                    log.warning(
                        f"scene {scene_idx}: no available segments after {consecutive_failures} attempts "
                        f"(all clips may be USED), stopping scene fill"
                    )
                    break
                # Pick a random asset (avoid back-to-back same source)
                candidates = list(range(len(assets)))
                if last_source_id is not None and len(assets) > 1:
                    no_repeat = [i for i in candidates if assets[i].source_id != last_source_id]
                    if no_repeat:
                        candidates = no_repeat
                cand_weights = [weights[i] for i in candidates]
                idx = rng.choices(candidates, weights=cand_weights, k=1)[0]
                asset = assets[idx]
                source = source_cache[asset.source_id]
                last_source_id = asset.source_id

                # V2: Convert absolute used ranges to asset-local coordinates
                abs_used = used_ranges_cache.get(asset.source_id, [])
                local_used = []
                for ur in abs_used:
                    local_start = ur.start_sec - asset.start_sec
                    local_end = ur.end_sec - asset.start_sec
                    if local_end <= 0 or local_start >= asset.duration:
                        continue
                    local_used.append(UsedRange(
                        start_sec=max(0.0, local_start),
                        end_sec=min(asset.duration, local_end),
                    ))

                # V3: Convert event boundaries to asset-local coordinates
                abs_events = event_boundaries_cache.get(asset.source_id, [])
                local_events = []
                for ev_start, ev_end in abs_events:
                    ls = ev_start - asset.start_sec
                    le = ev_end - asset.start_sec
                    if le <= 0 or ls >= asset.duration:
                        continue
                    local_events.append((max(0.0, ls), min(asset.duration, le)))

                available = asset.duration
                needed = min(available, scene_remaining)
                seg = find_available_segment(
                    available, needed, local_used, rng=rng,
                    event_boundaries=local_events if local_events else None,
                    max_uses=max_uses,
                )
                if seg is None:
                    consecutive_failures += 1
                    continue

                consecutive_failures = 0  # reset on success
                start_offset, end_offset = seg
                take = end_offset - start_offset

                # V3: Auditability
                n_uses = count_overlapping_uses(abs_used, asset.start_sec + start_offset,
                                                asset.start_sec + end_offset)
                reason = "event_aligned_fallback" if local_events else "random_fallback"

                clip = SelectedClip(
                    asset=asset,
                    source_path=source.file_path,
                    start_sec=asset.start_sec + start_offset,
                    end_sec=asset.start_sec + end_offset,
                    duration=take,
                    scene_index=scene_idx,
                    selection_reason=reason,
                    usage_count_at_selection=n_uses,
                )
                scene_clips.append(clip)
                scene_remaining -= take
                # Track in absolute coordinates
                used_ranges_cache.setdefault(asset.source_id, []).append(
                    UsedRange(
                        start_sec=asset.start_sec + start_offset,
                        end_sec=asset.start_sec + end_offset,
                    )
                )

            if not scene_clips:
                # Couldn't fill even one clip for this scene — all assets exhausted
                log.warning(
                    f"scene {scene_idx}: no clips available, stopping selection "
                    f"({remaining_total:.1f}s unfilled)"
                )
                break
            all_clips.extend(scene_clips)
            remaining_total -= scene_target
            scene_idx += 1

        return all_clips

    def select_scenes(
        self,
        session: Session,
        game_id: int,
        target_duration: float,
        *,
        scene_duration: float = 0.0,
        rng: Optional[random.Random] = None,
    ) -> list[Scene]:
        """Select scenes (groups of clips) to cover the target duration.

        Returns a list of Scene objects. Each Scene has one or more clips
        that should be concatenated into a single scene_NNN.mp4 file.
        """
        clips = self.select(
            session, game_id, target_duration,
            scene_duration=scene_duration, rng=rng,
        )
        if not clips:
            return []

        scenes: list[Scene] = []
        current_idx = clips[0].scene_index
        current_clips: list[SelectedClip] = []

        for clip in clips:
            if clip.scene_index != current_idx:
                if current_clips:
                    scenes.append(Scene(
                        index=current_idx,
                        clips=current_clips,
                        target_duration=sum(c.duration for c in current_clips),
                    ))
                current_idx = clip.scene_index
                current_clips = [clip]
            else:
                current_clips.append(clip)

        if current_clips:
            scenes.append(Scene(
                index=current_idx,
                clips=current_clips,
                target_duration=sum(c.duration for c in current_clips),
            ))

        return scenes
