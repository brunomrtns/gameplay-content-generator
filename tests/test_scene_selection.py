"""Tests for the scene-based gameplay selector and video profiles."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from gpcg.application.gameplay_selector import GameplaySelector, SelectedClip
from gpcg.domain.video_profiles import (
    SubtitleConfig,
    PROFILES,
    get_profile_dict,
    get_profile_name,
    get_resolution,
)
from gpcg.infrastructure.database import session_scope
from gpcg.domain.game_repository import get_or_create
from gpcg.domains.games.models import GameplaySource, GameplayAsset, IngestionStatus


# ── Video profiles ──────────────────────────────────────────────────────────


class TestVideoProfiles:
    def test_all_four_formats_defined(self):
        assert set(PROFILES.keys()) == {"9:16", "16:9", "1:1", "4:5"}

    def test_resolutions(self):
        assert get_resolution("9:16") == (1080, 1920)
        assert get_resolution("16:9") == (1920, 1080)
        assert get_resolution("1:1") == (1080, 1080)
        assert get_resolution("4:5") == (1080, 1350)

    def test_profile_names(self):
        assert get_profile_name("9:16") == "gpcg_9_16"
        assert get_profile_name("16:9") == "gpcg_16_9"
        assert get_profile_name("1:1") == "gpcg_1_1"
        assert get_profile_name("4:5") == "gpcg_4_5"

    def test_unknown_format_falls_back_to_9_16(self):
        assert get_resolution("invalid") == (1080, 1920)
        assert get_profile_name("invalid") == "gpcg_9_16"

    def test_subtitle_overrides(self):
        sub = SubtitleConfig(
            font="LiberationSans-Bold",
            font_size=60,
            color="yellow",
            outline_color="white",
            position="top",
            case_transform="lower",
        )
        pd = get_profile_dict("9:16", sub)
        assert pd["subtitle"]["font_size"] == 60
        assert pd["subtitle"]["font_color"] == "yellow"
        assert pd["subtitle"]["outline_color"] == "white"
        assert pd["subtitle"]["case_transform"] == "lower"
        assert pd["subtitle"]["position_y_ratio"] == 0.85  # "top"
        assert "LiberationSans-Bold" in pd["subtitle"]["font_file"]

    def test_subtitle_position_middle(self):
        sub = SubtitleConfig(position="middle")
        pd = get_profile_dict("16:9", sub)
        assert pd["subtitle"]["position_y_ratio"] == 0.45

    def test_subtitle_position_bottom(self):
        sub = SubtitleConfig(position="bottom")
        pd = get_profile_dict("1:1", sub)
        assert pd["subtitle"]["position_y_ratio"] == 0.15

    def test_no_subtitle_override_uses_defaults(self):
        pd = get_profile_dict("9:16", None)
        assert pd["subtitle"]["font_size"] == 48  # default for 9:16
        assert pd["subtitle"]["font_color"] == "white"

    def test_partial_subtitle_override(self):
        sub = SubtitleConfig(font_size=72)  # only font_size
        pd = get_profile_dict("4:5", sub)
        assert pd["subtitle"]["font_size"] == 72
        assert pd["subtitle"]["font_color"] == "white"  # default
        assert pd["subtitle"]["case_transform"] == "upper"  # default

    def test_profile_dict_has_custom_profile_key_for_adapter(self):
        pd = get_profile_dict("1:1")
        assert pd["name"] == "gpcg_1_1"
        assert pd["width"] == 1080
        assert pd["height"] == 1080


# ── Gameplay selector (scene-based mode) ────────────────────────────────────


@pytest.fixture
def game_with_assets(tmp_path):
    """Create a game with multiple gameplay assets of varying durations."""
    import uuid
    # Create dummy video files (content doesn't matter for selection logic)
    video1 = tmp_path / "video1.mp4"
    video2 = tmp_path / "video2.mp4"
    video1.write_bytes(b"fake1")
    video2.write_bytes(b"fake2")

    # Unique hashes per test run (file_hash has UNIQUE constraint)
    hash1 = uuid.uuid4().hex
    hash2 = uuid.uuid4().hex

    with session_scope() as s:
        game = get_or_create(s, "TestGame")
        game_id = game.id

        # Source 1: 20s video with 1 asset
        src1 = GameplaySource(
            game_id=game_id, filename="video1.mp4", file_path=str(video1),
            file_hash=hash1, file_size=100, duration=20.0, width=1920, height=1080,
            codec="h264", capture_source="test",
            ingestion_status=IngestionStatus.ready.value,
        )
        s.add(src1); s.flush()
        s.add(GameplayAsset(source_id=src1.id, start_sec=0.0, end_sec=20.0, label="full", duration=20.0))

        # Source 2: 30s video with 1 asset
        src2 = GameplaySource(
            game_id=game_id, filename="video2.mp4", file_path=str(video2),
            file_hash=hash2, file_size=100, duration=30.0, width=1920, height=1080,
            codec="h264", capture_source="test",
            ingestion_status=IngestionStatus.ready.value,
        )
        s.add(src2); s.flush()
        s.add(GameplayAsset(source_id=src2.id, start_sec=0.0, end_sec=30.0, label="full", duration=30.0))

    return game_id


class TestGameplaySelectorSceneBased:
    def test_legacy_mode_no_scene_duration(self, game_with_assets):
        """Legacy mode: scene_duration=0, each clip = full asset."""
        selector = GameplaySelector()
        rng = random.Random(42)
        with session_scope() as s:
            clips = selector.select(s, game_with_assets, target_duration=40.0, rng=rng)
        # Should select 2 clips (20s + 30s = 50s > 40s target)
        assert len(clips) >= 2
        # Each clip should be a full asset
        for clip in clips:
            assert clip.duration in (20.0, 30.0)

    def test_scene_based_single_long_scene(self, game_with_assets):
        """scene_duration >= target_duration → 1 scene, 1 clip."""
        selector = GameplaySelector()
        rng = random.Random(42)
        with session_scope() as s:
            clips = selector.select(
                s, game_with_assets, target_duration=15.0,
                scene_duration=7200.0,  # 2h — way more than target
                rng=rng,
            )
        # Should be 1 scene with 1 clip of 15s (target_duration)
        assert len(clips) == 1
        assert clips[0].scene_index == 0
        assert clips[0].duration == pytest.approx(15.0, abs=0.01)
        # Clip should be a sub-segment (not full asset)
        assert clips[0].start_sec >= 0
        assert clips[0].end_sec <= clips[0].asset.end_sec

    def test_scene_based_multiple_scenes(self, game_with_assets):
        """scene_duration=10s, target=25s → 3 scenes (10+10+5)."""
        selector = GameplaySelector()
        rng = random.Random(42)
        with session_scope() as s:
            clips = selector.select(
                s, game_with_assets, target_duration=25.0,
                scene_duration=10.0,
                rng=rng,
            )
        # Should have 3 scenes: 0, 1, 2
        scene_indices = {c.scene_index for c in clips}
        assert scene_indices == {0, 1, 2}
        # Each scene's clips sum to scene_target (10 or 5)
        for idx in scene_indices:
            scene_clips = [c for c in clips if c.scene_index == idx]
            scene_dur = sum(c.duration for c in scene_clips)
            if idx < 2:
                assert abs(scene_dur - 10.0) < 0.1
            else:
                assert abs(scene_dur - 5.0) < 0.1

    def test_scene_based_chaining_when_video_shorter(self, game_with_assets):
        """scene_duration=50s, target=50s → 1 scene that chains multiple videos."""
        selector = GameplaySelector()
        rng = random.Random(42)
        with session_scope() as s:
            clips = selector.select(
                s, game_with_assets, target_duration=50.0,
                scene_duration=50.0,
                rng=rng,
            )
        # All clips in scene 0
        assert all(c.scene_index == 0 for c in clips)
        # Total = 50s
        total = sum(c.duration for c in clips)
        assert abs(total - 50.0) < 0.1
        # Must have chained (since max asset is 30s < 50s)
        assert len(clips) >= 2

    def test_scene_based_random_start_point(self, game_with_assets):
        """Long scene should pick a random start point within the asset."""
        selector = GameplaySelector()
        # Run multiple times with different seeds, verify different start points
        start_points = set()
        for seed in range(20):
            rng = random.Random(seed)
            with session_scope() as s:
                clips = selector.select(
                    s, game_with_assets, target_duration=10.0,
                    scene_duration=10.0,
                    rng=rng,
                )
            if clips:
                start_points.add(round(clips[0].start_sec, 1))
        # Should have variety (not all the same start)
        assert len(start_points) > 1

    def test_select_scenes_groups_clips(self, game_with_assets):
        """select_scenes() returns Scene objects with grouped clips."""
        selector = GameplaySelector()
        rng = random.Random(42)
        with session_scope() as s:
            scenes = selector.select_scenes(
                s, game_with_assets, target_duration=25.0,
                scene_duration=10.0,
                rng=rng,
            )
        assert len(scenes) == 3
        assert scenes[0].index == 0
        assert scenes[1].index == 1
        assert scenes[2].index == 2
        # Each scene has at least 1 clip
        for scene in scenes:
            assert len(scene.clips) >= 1
