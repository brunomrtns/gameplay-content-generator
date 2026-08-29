"""Tests for PresentationConfig — serialization, defaults, backward compat."""

from gpcg.domain.presentation_config import PresentationConfig


class TestPresentationConfigDefaults:
    def test_default_disabled(self):
        cfg = PresentationConfig()
        assert cfg.enabled is False
        assert cfg.thumbnail_enabled is True
        assert cfg.opening_enabled is True
        assert cfg.thumbnail_mode == "auto"
        assert cfg.opening_duration == 2.5

    def test_empty_dict_returns_disabled(self):
        """Empty dict (automation without presentation) → disabled config."""
        cfg = PresentationConfig.from_dict({})
        assert cfg.enabled is False

    def test_none_dict_returns_disabled(self):
        """None dict → disabled config."""
        cfg = PresentationConfig.from_dict(None)
        assert cfg.enabled is False


class TestPresentationConfigSerialization:
    def test_round_trip(self):
        cfg = PresentationConfig(
            enabled=True,
            thumbnail_mode="fixed",
            thumbnail_image_path="/data/presentation/my_image.jpg",
            opening_duration=4.0,
        )
        d = cfg.to_dict()
        cfg2 = PresentationConfig.from_dict(d)
        assert cfg2.enabled is True
        assert cfg2.thumbnail_mode == "fixed"
        assert cfg2.thumbnail_image_path == "/data/presentation/my_image.jpg"
        assert cfg2.opening_duration == 4.0

    def test_from_dict_ignores_unknown_keys(self):
        """Forward compat: unknown keys are silently ignored."""
        cfg = PresentationConfig.from_dict({
            "enabled": True,
            "future_field": "something",
            "another_unknown": 42,
        })
        assert cfg.enabled is True
        # Unknown fields don't cause errors

    def test_from_dict_partial(self):
        """Partial dict → missing keys get defaults."""
        cfg = PresentationConfig.from_dict({"enabled": True})
        assert cfg.enabled is True
        assert cfg.thumbnail_mode == "auto"  # default
        assert cfg.opening_duration == 2.5  # default

    def test_to_dict_is_json_serializable(self):
        """to_dict produces a plain dict suitable for JSON storage."""
        import json
        cfg = PresentationConfig(enabled=True, thumbnail_text_custom="Olá")
        d = cfg.to_dict()
        # Must be JSON serializable
        json.dumps(d)
