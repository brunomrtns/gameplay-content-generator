"""Tests for ThumbnailSelector — auto/imported/fixed modes + fallbacks."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gpcg.application.thumbnail_selector import ThumbnailSelector, ThumbnailResult
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
