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

from sqlalchemy import select
from sqlalchemy.orm import Session

from gpcg.domain.models import GameplayAsset, GameplaySource
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
    """Selects gameplay assets to cover the target duration."""

    def select(
        self,
        session: Session,
        game_id: int,
        target_duration: float,
        *,
        scene_duration: float = 0.0,
        rng: Optional[random.Random] = None,
    ) -> list[SelectedClip]:
        """Select clips totaling ~target_duration seconds.

        Args:
            session: DB session.
            game_id: Game to select gameplay from.
            target_duration: Total duration to fill (narration duration).
            scene_duration: Target duration of each scene. 0 = legacy mode
                (use asset durations). >0 = scene-based mode with chaining.
            rng: Random generator (for deterministic tests).

        Returns:
            List of SelectedClip, each with scene_index indicating which scene
            it belongs to. Clips in the same scene should be concatenated.
        """
        rng = rng or random.Random()
        assets = session.execute(
            select(GameplayAsset)
            .join(GameplaySource, GameplayAsset.source_id == GameplaySource.id)
            .where(GameplaySource.game_id == game_id)
            .order_by(GameplayAsset.used_count.asc())
        ).scalars().all()

        if not assets:
            log.warning(f"no gameplay assets for game #{game_id}")
            return []

        # Preload source paths
        source_cache: dict[int, GameplaySource] = {}
        for a in assets:
            if a.source_id not in source_cache:
                source_cache[a.source_id] = session.get(GameplaySource, a.source_id)

        if scene_duration > 0:
            clips = self._select_scene_based(
                assets, source_cache, target_duration, scene_duration, rng
            )
        else:
            clips = self._select_legacy(assets, source_cache, target_duration, rng)

        total = sum(c.duration for c in clips)
        n_scenes = max(c.scene_index for c in clips) + 1 if clips else 0
        log.info(
            f"selected {len(clips)} clip(s) in {n_scenes} scene(s) "
            f"totaling {total:.1f}s for target {target_duration:.1f}s "
            f"(scene_duration={scene_duration}s)"
        )
        return clips

    def _select_legacy(
        self,
        assets: list[GameplayAsset],
        source_cache: dict[int, GameplaySource],
        target_duration: float,
        rng: random.Random,
    ) -> list[SelectedClip]:
        """Legacy mode: each clip = one asset, accumulate until target."""
        weights = [1.0 / (a.used_count + 1) for a in assets]
        selected: list[SelectedClip] = []
        total = 0.0
        last_source_id: Optional[int] = None
        max_iter = len(assets) * 3

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
            clip = SelectedClip(
                asset=asset,
                source_path=source.file_path,
                start_sec=asset.start_sec,
                end_sec=asset.end_sec,
                duration=asset.duration,
                scene_index=len(selected),  # each clip is its own scene in legacy
            )
            selected.append(clip)
            total += clip.duration
            last_source_id = asset.source_id

        return selected

    def _select_scene_based(
        self,
        assets: list[GameplayAsset],
        source_cache: dict[int, GameplaySource],
        target_duration: float,
        scene_duration: float,
        rng: random.Random,
    ) -> list[SelectedClip]:
        """Scene-based mode: group clips into scenes of scene_duration each.

        For each scene:
        1. Pick a random gameplay video
        2. Pick a random start point within [0, video_duration]
        3. If start + scene_target <= video_duration: take contiguous segment
        4. If overflow: take [start, end] from this video, chain another for remainder
        """
        weights = [1.0 / (a.used_count + 1) for a in assets]
        all_clips: list[SelectedClip] = []
        remaining_total = target_duration
        scene_idx = 0
        last_source_id: Optional[int] = None

        while remaining_total > 0.01:
            # This scene's target duration
            scene_target = min(scene_duration, remaining_total)
            scene_clips: list[SelectedClip] = []
            scene_remaining = scene_target

            while scene_remaining > 0.01:
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

                # How much can we take from this asset?
                available = asset.duration

                if available >= scene_remaining:
                    # Fits in one segment — pick a random start point
                    max_start = available - scene_remaining
                    start_offset = rng.uniform(0, max_start) if max_start > 0.01 else 0.0
                    clip = SelectedClip(
                        asset=asset,
                        source_path=source.file_path,
                        start_sec=asset.start_sec + start_offset,
                        end_sec=asset.start_sec + start_offset + scene_remaining,
                        duration=scene_remaining,
                        scene_index=scene_idx,
                    )
                    scene_clips.append(clip)
                    scene_remaining = 0
                else:
                    # Doesn't fit — take the whole asset and chain another
                    # Pick a random start point, take until end of asset
                    start_offset = rng.uniform(0, max(0.01, available * 0.3))
                    take = min(available - start_offset, scene_remaining)
                    if take < 0.5:
                        take = min(available, scene_remaining)
                        start_offset = 0.0
                    clip = SelectedClip(
                        asset=asset,
                        source_path=source.file_path,
                        start_sec=asset.start_sec + start_offset,
                        end_sec=asset.start_sec + start_offset + take,
                        duration=take,
                        scene_index=scene_idx,
                    )
                    scene_clips.append(clip)
                    scene_remaining -= take

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
