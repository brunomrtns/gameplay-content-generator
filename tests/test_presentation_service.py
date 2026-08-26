"""Tests for PresentationService — orchestration + non-fatal behavior."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gpcg.application.presentation_service import PresentationService, PresentationResult
from gpcg.domain.presentation_config import PresentationConfig


@pytest.fixture
def service():
    return PresentationService()


@pytest.fixture
def tmp_scene_dir(tmp_path):
    d = tmp_path / "scene_dir"
    d.mkdir()
    return d


class TestPresentationServiceDisabled:
    def test_disabled_config_returns_failure(self, service, tmp_scene_dir):
        cfg = PresentationConfig(enabled=False)
        result = service.apply(
            session=None,
            job_id=1,
            topic="test",
            title="Test Video",
            script_first_line="Hello",
            gameplay_source_id=None,
            gameplay_source_path="",
            selected_clips=[],
            config=cfg,
            scene_dir=tmp_scene_dir,
            video_format="9:16",
        )
        assert result.success is False
        assert "disabled" in result.error


class TestPresentationServiceEnabled:
    def test_apply_with_thumbnail_only(self, service, tmp_scene_dir):
        """When opening is disabled but thumbnail is enabled, should produce thumbnail."""
        cfg = PresentationConfig(
            enabled=True,
            thumbnail_enabled=True,
            thumbnail_mode="imported",
            thumbnail_image_path="",  # will be set
            opening_enabled=False,
        )

        # Mock the selector to return a fake image
        fake_image = tmp_scene_dir / "_presentation" / "base.jpg"
        fake_image.parent.mkdir(parents=True, exist_ok=True)
        fake_image.write_bytes(b"fake jpg")

        cfg.thumbnail_image_path = str(fake_image)

        # Mock the renderer's compose_thumbnail
        with patch.object(service.renderer, "compose_thumbnail") as mock_compose:
            thumb_out = tmp_scene_dir / "_presentation" / "thumbnail_final.jpg"
            mock_compose.return_value = thumb_out

            result = service.apply(
                session=None,
                job_id=1,
                topic="test",
                title="Test Video",
                script_first_line="Hello",
                gameplay_source_id=None,
                gameplay_source_path="",
                selected_clips=[],
                config=cfg,
                scene_dir=tmp_scene_dir,
                video_format="9:16",
            )
            assert result.success is True
            assert result.thumbnail_path is not None
            assert result.opening_scene_path is None  # opening disabled

    def test_apply_failure_is_non_fatal(self, service, tmp_scene_dir):
        """When an exception occurs, the service should not raise."""
        cfg = PresentationConfig(
            enabled=True,
            thumbnail_enabled=True,
            thumbnail_mode="auto",
        )

        # Mock selector to raise
        with patch.object(service.selector, "select", side_effect=Exception("boom")):
            result = service.apply(
                session=MagicMock(),
                job_id=1,
                topic="test",
                title="Test",
                script_first_line="",
                gameplay_source_id=1,
                gameplay_source_path="/fake.mp4",
                selected_clips=[],
                config=cfg,
                scene_dir=tmp_scene_dir,
                video_format="9:16",
            )
            assert result.success is False
            assert "boom" in result.error


class TestPresentationServiceTextResolution:
    def test_resolve_text_title(self, service):
        text = service._resolve_text("title", "", "My Title", "First line")
        assert text == "My Title"

    def test_resolve_text_hook(self, service):
        text = service._resolve_text("hook", "", "My Title", "First line")
        assert text == "First line"

    def test_resolve_text_custom(self, service):
        text = service._resolve_text("custom", "Custom Text", "My Title", "")
        assert text == "Custom Text"

    def test_resolve_text_custom_empty_falls_back_to_title(self, service):
        text = service._resolve_text("custom", "", "My Title", "")
        assert text == "My Title"
