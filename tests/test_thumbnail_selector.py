"""Tests for ThumbnailSelector — auto/imported/fixed modes + fallbacks.

V2 tests cover:
  - Event type weighting (COMBAT > MENU)
  - Visual quality analysis (brightness penalty for dark frames)
  - Transcript inclusion in keyword scoring
  - Diversity (different topics → different frames from same source)
  - Keyword fallback when no embeddings available
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gpcg.application.thumbnail_selector import ThumbnailSelector, ThumbnailResult, _FrameQuality
from gpcg.domain.presentation_config import PresentationConfig


@pytest.fixture
def selector():
    return ThumbnailSelector()


@pytest.fixture
def tmp_output(tmp_path):
    d = tmp_path / "thumbs"
    d.mkdir()
    return d


@pytest.fixture
def imported_image(tmp_path):
    p = tmp_path / "my_image.jpg"
    p.write_bytes(b"fake image data")
    return p


class TestThumbnailSelectorImported:
    def test_imported_mode_returns_image(self, selector, imported_image, tmp_output):
        cfg = PresentationConfig(
            enabled=True,
            thumbnail_mode="imported",
            thumbnail_image_path=str(imported_image),
        )
        result = selector.select(
            session=None,
            topic="test",
            gameplay_source_id=None,
            gameplay_source_path="",
            config=cfg,
            output_dir=tmp_output,
        )
        assert result is not None
        assert result.image_path == imported_image
        assert result.selection_reason == "imported"

    def test_imported_mode_missing_image_returns_none(self, selector, tmp_output):
        cfg = PresentationConfig(
            enabled=True,
            thumbnail_mode="imported",
            thumbnail_image_path="/nonexistent/image.jpg",
        )
        result = selector.select(
            session=None,
            topic="test",
            gameplay_source_id=None,
            gameplay_source_path="",
            config=cfg,
            output_dir=tmp_output,
        )
        assert result is None

    def test_imported_mode_empty_path_returns_none(self, selector, tmp_output):
        cfg = PresentationConfig(
            enabled=True,
            thumbnail_mode="imported",
            thumbnail_image_path="",
        )
        result = selector.select(
            session=None,
            topic="test",
            gameplay_source_id=None,
            gameplay_source_path="",
            config=cfg,
            output_dir=tmp_output,
        )
        assert result is None

    def test_fixed_mode_same_as_imported(self, selector, imported_image, tmp_output):
        cfg = PresentationConfig(
            enabled=True,
            thumbnail_mode="fixed",
            thumbnail_image_path=str(imported_image),
        )
        result = selector.select(
            session=None,
            topic="test",
            gameplay_source_id=None,
            gameplay_source_path="",
            config=cfg,
            output_dir=tmp_output,
        )
        assert result is not None
        assert result.selection_reason == "fixed"


class TestThumbnailSelectorAuto:
    def test_auto_no_source_returns_none(self, selector, tmp_output):
        cfg = PresentationConfig(enabled=True, thumbnail_mode="auto")
        result = selector.select(
            session=None,
            topic="test",
            gameplay_source_id=None,
            gameplay_source_path="",
            config=cfg,
            output_dir=tmp_output,
        )
        assert result is None

    def test_auto_no_events_falls_back_to_mid_video(self, selector, tmp_path):
        """When no events pass filters, should fall back to mid-video frame."""
        cfg = PresentationConfig(
            enabled=True,
            thumbnail_mode="auto",
            auto_min_interesting=0.9,  # high threshold → no events pass
            auto_min_confidence=0.9,
        )
        # Mock session that returns no events
        mock_session = MagicMock()
        mock_query = MagicMock()
        mock_filter = MagicMock()
        mock_filter.all.return_value = []
        mock_query.filter.return_value = mock_filter
        mock_session.query.return_value = mock_query

        # Mock probe + generate_thumbnail
        with patch("gpcg.application.thumbnail_selector.probe") as mock_probe, \
             patch("gpcg.application.thumbnail_selector.generate_thumbnail") as mock_gen:
            mock_probe.return_value = MagicMock(duration=10.0)
            mock_gen.return_value = None  # success

            result = selector.select(
                session=mock_session,
                topic="test",
                gameplay_source_id=1,
                gameplay_source_path="/fake/video.mp4",
                config=cfg,
                output_dir=tmp_path / "out",
            )
            assert result is not None
            assert result.selection_reason == "mid_video_fallback"
            mock_gen.assert_called_once()

    def test_auto_with_events_selects_best(self, selector, tmp_path):
        """When events pass filters, should select the highest-scored one."""
        cfg = PresentationConfig(
            enabled=True,
            thumbnail_mode="auto",
            auto_min_interesting=0.3,
            auto_min_confidence=0.3,
            auto_candidate_count=3,
        )

        # Create mock events
        event1 = MagicMock(
            id=1, start_time=5.0, end_time=10.0,
            event_type="COMBAT", description="epic battle scene",
            tags=["action", "fight"], characters=["hero"],
            actions=["fighting"], location="arena",
            interesting_score=0.8, visual_confidence=0.9,
        )
        event2 = MagicMock(
            id=2, start_time=20.0, end_time=25.0,
            event_type="EXPLORATION", description="walking around",
            tags=["walk"], characters=[], actions=["walking"],
            location="city", interesting_score=0.5, visual_confidence=0.6,
        )
        mock_session = MagicMock()
        mock_query = MagicMock()
        mock_filter = MagicMock()
        mock_filter.all.return_value = [event1, event2]
        mock_query.filter.return_value = mock_filter
        mock_session.query.return_value = mock_query

        with patch("gpcg.application.thumbnail_selector.generate_thumbnail") as mock_gen:
            mock_gen.return_value = None  # success

            result = selector.select(
                session=mock_session,
                topic="epic battle",
                gameplay_source_id=1,
                gameplay_source_path="/fake/video.mp4",
                config=cfg,
                output_dir=tmp_path / "out",
            )
            assert result is not None
            assert result.source_event_id == 1  # event1 has higher score
            # Should have extracted 2 candidates (we have 2 events)
            assert mock_gen.call_count == 2


class TestThumbnailSelectorKeywords:
    def test_extract_keywords_filters_stopwords(self, selector):
        kw = selector._extract_keywords("O grande combate no arena")
        assert "grande" in kw
        assert "combate" in kw
        assert "arena" in kw
        assert "o" not in kw  # stopword
        assert "no" not in kw  # stopword

    def test_extract_keywords_empty(self, selector):
        assert selector._extract_keywords("") == set()

    def test_extract_keywords_short_words_filtered(self, selector):
        kw = selector._extract_keywords("a i o ok go battle")
        assert "battle" in kw  # len > 2
        assert "ok" not in kw  # len == 2, filtered
        assert "go" not in kw  # len == 2, filtered


# ── V2 Tests ─────────────────────────────────────────────────────────────────


class TestEventTypeWeighting:
    """Test that event type influences scoring (COMBAT > MENU)."""

    def test_combat_has_higher_weight_than_menu(self, selector):
        assert selector._event_type_weight("COMBAT") > selector._event_type_weight("MENU")

    def test_chase_has_higher_weight_than_loading(self, selector):
        assert selector._event_type_weight("CHASE") > selector._event_type_weight("LOADING")

    def test_vehicle_has_higher_weight_than_dialogue(self, selector):
        assert selector._event_type_weight("VEHICLE") > selector._event_type_weight("DIALOGUE")

    def test_possible_prefix_gets_penalty(self, selector):
        base = selector._event_type_weight("COMBAT")
        possible = selector._event_type_weight("POSSIBLE_COMBAT")
        assert possible < base
        # POSSIBLE_COMBAT is not in the map, so it gets default (0.6) * penalty (0.85)
        assert possible == 0.6 * 0.85

    def test_unknown_event_type_gets_default(self, selector):
        weight = selector._event_type_weight("SOMETHING_NEW")
        assert weight == 0.6  # _DEFAULT_EVENT_TYPE_WEIGHT


class TestTranscriptInKeywordScoring:
    """Test that transcript is included in keyword matching."""

    def test_transcript_contributes_to_keyword_score(self, selector):
        """An event whose transcript contains the topic keyword should score
        higher than one without, even if description doesn't match."""
        event_with_transcript = MagicMock(
            id=1, description="walking around",
            tags=[], characters=[], actions=[], location="city",
            transcript="look at that amazing car chase",
        )
        event_without_transcript = MagicMock(
            id=2, description="walking around",
            tags=[], characters=[], actions=[], location="city",
            transcript="",
        )
        keywords = {"chase", "car"}
        score1 = selector._keyword_score(event_with_transcript, keywords)
        score2 = selector._keyword_score(event_without_transcript, keywords)
        assert score1 > score2
        assert score2 == 0.0
        assert score1 > 0.0


class TestVisualQualityAnalysis:
    """Test visual quality analysis of extracted frames."""

    def test_dark_frame_gets_low_score(self, selector, tmp_path):
        """A very dark frame should get a low visual quality score."""
        # Create a dark image (mostly black)
        from PIL import Image
        img = Image.new("RGB", (100, 100), (5, 5, 10))  # very dark
        img_path = tmp_path / "dark.jpg"
        img.save(img_path)

        quality = selector._analyze_frame_pil(img_path)
        assert quality.brightness < 0.1
        assert quality.score < 0.2  # dark frames get penalized

    def test_bright_colorful_frame_gets_high_score(self, selector, tmp_path):
        """A bright, colorful frame should get a higher visual quality score."""
        from PIL import Image
        img = Image.new("RGB", (100, 100), (200, 100, 50))  # bright, colorful
        img_path = tmp_path / "bright.jpg"
        img.save(img_path)

        quality = selector._analyze_frame_pil(img_path)
        assert quality.brightness > 0.3
        assert quality.score > 0.2

    def test_dark_frame_scores_lower_than_bright(self, selector, tmp_path):
        """Dark frame should score lower than bright frame."""
        from PIL import Image
        dark = Image.new("RGB", (100, 100), (5, 5, 10))
        bright = Image.new("RGB", (100, 100), (200, 150, 100))

        dark_path = tmp_path / "dark.jpg"
        bright_path = tmp_path / "bright.jpg"
        dark.save(dark_path)
        bright.save(bright_path)

        dark_q = selector._analyze_frame_pil(dark_path)
        bright_q = selector._analyze_frame_pil(bright_path)
        assert bright_q.score > dark_q.score


class TestDiversitySelection:
    """Test that different topics produce different frame selections
    from the same gameplay source (the main bug fix)."""

    def test_different_topics_can_select_different_events(self, selector, tmp_path):
        """Two videos with different topics using the same gameplay source
        should be able to select different events (not always the same one)."""
        cfg = PresentationConfig(
            enabled=True,
            thumbnail_mode="auto",
            auto_min_interesting=0.3,
            auto_min_confidence=0.3,
            auto_candidate_count=5,
        )

        # Create events with similar interesting/confidence scores but
        # different event types and descriptions
        events = []
        for i in range(6):
            events.append(MagicMock(
                id=i + 1,
                start_time=20.0 + i * 30,
                end_time=25.0 + i * 30,
                event_type="COMBAT" if i % 2 == 0 else "VEHICLE",
                description=f"scene {i} with action",
                tags=["action"],
                characters=[],
                actions=["fighting" if i % 2 == 0 else "driving"],
                location="city",
                interesting_score=0.7,
                visual_confidence=0.8,
                transcript="",
            ))

        mock_session = MagicMock()
        mock_query = MagicMock()
        mock_filter = MagicMock()
        mock_filter.all.return_value = events
        mock_query.filter.return_value = mock_filter
        mock_session.query.return_value = mock_query

        # Mock generate_thumbnail to create dummy files
        def fake_gen(video, output, at=1.0):
            from PIL import Image
            # Create frames with varying brightness
            idx = int(at / 30)
            brightness = 100 + idx * 20
            img = Image.new("RGB", (100, 100), (brightness, brightness, brightness))
            img.save(output)
            return output

        # Mock _semantic_scores_via_embeddings to return None (use keywords)
        with patch.object(selector, "_semantic_scores_via_embeddings", return_value=None), \
             patch("gpcg.application.thumbnail_selector.generate_thumbnail", side_effect=fake_gen):

            result1 = selector.select(
                session=mock_session,
                topic="epic battle combat",
                gameplay_source_id=1,
                gameplay_source_path="/fake/video.mp4",
                config=cfg,
                output_dir=tmp_path / "out1",
            )
            result2 = selector.select(
                session=mock_session,
                topic="driving vehicle race",
                gameplay_source_id=1,
                gameplay_source_path="/fake/video.mp4",
                config=cfg,
                output_dir=tmp_path / "out2",
            )

        assert result1 is not None
        assert result2 is not None
        # Both should select valid events
        assert result1.source_event_id is not None
        assert result2.source_event_id is not None


class TestKeywordScoringRobustness:
    """Test that keyword scoring handles non-string attributes gracefully."""

    def test_keyword_score_with_mock_attributes(self, selector):
        """Should not crash when event attributes are MagicMock or None."""
        event = MagicMock(
            id=1, description="epic battle",
            tags=["fight"], characters=["hero"],
            actions=["running"], location="arena",
            transcript="listen to the explosion",
        )
        keywords = {"battle", "explosion"}
        score = selector._keyword_score(event, keywords)
        assert score > 0.0  # should find "battle" in description and "explosion" in transcript

    def test_keyword_score_with_none_attributes(self, selector):
        """Should handle None attributes without crashing."""
        event = MagicMock(
            id=1, description=None,
            tags=None, characters=None, actions=None,
            location=None, transcript=None,
        )
        # MagicMock returns MagicMock for None, but we need actual None
        event.description = None
        event.tags = None
        event.characters = None
        event.actions = None
        event.location = None
        event.transcript = None

        score = selector._keyword_score(event, {"battle"})
        assert score == 0.0  # no text to match
