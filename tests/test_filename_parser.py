"""Tests for the filename parser — deterministic game/capture/timestamp extraction."""

from gpcg.domain.filename_parser import parse_filename, is_capture_source, CAPTURE_SOURCES


class TestFilenameParser:
    def test_obs_style_game_first(self):
        r = parse_filename("Bully_2026-07-26_14-32-11.mp4")
        assert r.candidate_game == "Bully"
        assert r.capture_source is None
        assert r.is_capture_source_only is False
        assert r.confidence == 0.9
        assert r.recorded_at is not None
        assert r.recorded_at.year == 2026
        assert r.recorded_at.hour == 14

    def test_emulator_as_capture_source(self):
        r = parse_filename("Yuzu_2026-07-26_15-07-43.mp4")
        # Yuzu is an emulator, not a game
        assert r.candidate_game is None
        assert r.capture_source == "Yuzu"
        assert r.is_capture_source_only is True
        assert r.confidence == 0.0

    def test_obs_recorder_as_capture_source(self):
        r = parse_filename("OBS_2026-07-26_16-48-33.mp4")
        assert r.candidate_game is None
        assert r.capture_source == "OBS"
        assert r.is_capture_source_only is True

    def test_game_with_spaces(self):
        r = parse_filename("Crash CTR_2026-07-26_17-12-51.mp4")
        assert r.candidate_game == "Crash CTR"
        assert r.confidence == 0.9

    def test_date_first_pattern(self):
        r = parse_filename("2026-07-26_18-00-00_Bully.mp4")
        assert r.candidate_game == "Bully"
        assert r.confidence == 0.9
        assert r.recorded_at is not None

    def test_no_match_fallback_to_stem(self):
        r = parse_filename("random_video.mp4")
        # Falls back to stem as weak candidate
        assert r.candidate_game == "random video"
        assert "weak_candidate_from_stem" in r.extra_tokens

    def test_no_match_capture_source_stem(self):
        r = parse_filename("yuzu.mp4")
        assert r.capture_source == "yuzu"
        assert r.is_capture_source_only is True

    def test_mkv_extension(self):
        r = parse_filename("Bully_2026-07-26_14-32-11.mkv")
        assert r.candidate_game == "Bully"

    def test_is_capture_source_known(self):
        assert is_capture_source("yuzu") is True
        assert is_capture_source("OBS") is True
        assert is_capture_source("Bully") is False
        assert is_capture_source("dolphin") is True

    def test_capure_sources_contains_common(self):
        assert "yuzu" in CAPTURE_SOURCES
        assert "obs" in CAPTURE_SOURCES
        assert "dolphin" in CAPTURE_SOURCES
        assert "pcsx2" in CAPTURE_SOURCES
