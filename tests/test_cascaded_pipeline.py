"""Tests for the cascaded gameplay analysis pipeline.

Tests the YOLO-based player detector, image enhancer, and the cascaded
analysis mode of the GameplayAnalyzer (camera_type != "unknown").

These tests use synthetic frames (numpy arrays) and mock VLM responses
to avoid requiring GPU/YOLO/Ollama at test time.
"""

from __future__ import annotations

import numpy as np
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from gpcg.infrastructure.image_enhancer import (
    EnhancementConfig,
    crop_with_padding,
    upscale_to_min,
    sharpen,
    enhance_contrast,
    enhance_crop,
)


# ── Image enhancer tests (pure OpenCV, no GPU needed) ────────────────────────


class TestImageEnhancer:
    """Tests for the image enhancement pipeline."""

    def _make_frame(self, w=1920, h=1080) -> np.ndarray:
        """Create a synthetic BGR frame with a colored rectangle (simulates a player)."""
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        frame[:] = (50, 100, 150)  # background
        # Draw a "player" rectangle in the center-bottom
        cv2 = pytest.importorskip("cv2")
        cv2.rectangle(frame, (900, 500), (1020, 700), (200, 50, 50), -1)
        return frame

    def test_crop_with_padding(self):
        frame = self._make_frame()
        crop, adj_bbox = crop_with_padding(frame, (900, 500, 1020, 700), padding_frac=0.3)
        # Crop should be larger than the bbox (padding added)
        x1, y1, x2, y2 = adj_bbox
        assert (x2 - x1) > (1020 - 900)  # wider with padding
        assert (y2 - y1) > (700 - 500)  # taller with padding
        assert crop.shape[0] > 0 and crop.shape[1] > 0

    def test_crop_with_padding_clamps_to_frame(self):
        """Padding should not exceed frame bounds."""
        frame = self._make_frame(w=100, h=100)
        crop, adj_bbox = crop_with_padding(frame, (0, 0, 50, 50), padding_frac=0.5)
        x1, y1, x2, y2 = adj_bbox
        assert x1 >= 0 and y1 >= 0
        assert x2 <= 100 and y2 <= 100

    def test_upscale_to_min_enlarges_small_image(self):
        small = np.zeros((100, 200, 3), dtype=np.uint8)
        upscaled = upscale_to_min(small, min_long_side=640)
        assert max(upscaled.shape[:2]) >= 640

    def test_upscale_to_min_keeps_large_image(self):
        large = np.zeros((800, 1200, 3), dtype=np.uint8)
        result = upscale_to_min(large, min_long_side=640)
        assert result.shape == large.shape  # unchanged

    def test_sharpen_returns_same_shape(self):
        frame = self._make_frame(w=200, h=200)
        sharpened = sharpen(frame, strength=0.4)
        assert sharpened.shape == frame.shape

    def test_sharpen_zero_strength_returns_original(self):
        frame = self._make_frame(w=200, h=200)
        result = sharpen(frame, strength=0.0)
        assert np.array_equal(result, frame)

    def test_enhance_contrast_returns_same_shape(self):
        frame = self._make_frame(w=200, h=200)
        enhanced = enhance_contrast(frame, clip_limit=2.0)
        assert enhanced.shape == frame.shape

    def test_enhance_contrast_zero_returns_original(self):
        frame = self._make_frame(w=200, h=200)
        result = enhance_contrast(frame, clip_limit=0.0)
        assert np.array_equal(result, frame)

    def test_enhance_crop_full_pipeline(self):
        """The main entry point: crop + upscale + sharpen + contrast."""
        frame = self._make_frame()
        crop, adj_bbox = enhance_crop(frame, (900, 500, 1020, 700))
        # Crop should be upscaled to at least min_long_side on the longest side
        assert max(crop.shape[:2]) >= 640 or crop.shape[0] == 0
        assert len(crop.shape) == 3  # BGR


# ── Player detector tests (mock YOLO to avoid GPU dependency) ────────────────


class TestPlayerDetector:
    """Tests for the YOLO-based player detector.

    Uses a mock YOLO model to avoid requiring GPU/model download at test time.
    """

    def _make_mock_detection(self, cls, conf, bbox):
        """Create a mock Detection-like object."""
        from gpcg.infrastructure.player_detector import Detection
        return Detection(cls=cls, confidence=conf, bbox=bbox)

    def _make_mock_detector(self, detections: list):
        """Create a PlayerDetector with a mocked YOLO model."""
        from gpcg.infrastructure.player_detector import PlayerDetector, DetectorConfig
        detector = PlayerDetector(DetectorConfig())
        detector._model = MagicMock()
        detector._model.names = {0: "person", 1: "bicycle", 2: "car"}

        # Mock the YOLO inference result
        mock_result = MagicMock()
        mock_boxes = MagicMock()
        mock_boxes.__iter__ = lambda self: iter([
            self._make_mock_box(d) for d in detections
        ])
        mock_result.boxes = mock_boxes
        detector._model.return_value = [mock_result]
        return detector

    def _make_mock_box(self, det):
        """Create a mock YOLO box."""
        import torch
        box = MagicMock()
        box.cls = torch.tensor([list(detector_cls_map.values()).index(det.cls) if det.cls in detector_cls_map else 0])
        box.conf = torch.tensor([det.confidence])
        box.xyxy = torch.tensor([det.bbox])
        return box

    def test_select_player_single_person(self):
        from gpcg.infrastructure.player_detector import PlayerDetector, Detection
        detector = PlayerDetector()
        persons = [Detection(cls="person", confidence=0.9, bbox=(900, 500, 1000, 700))]
        player = detector._select_player(persons, frame_w=1920, frame_h=1080)
        assert player.bbox == (900, 500, 1000, 700)

    def test_select_player_prefers_central_bottom(self):
        """When multiple persons, prefer the one closest to center-bottom."""
        from gpcg.infrastructure.player_detector import PlayerDetector, Detection
        detector = PlayerDetector()
        # Person A: center-bottom (typical player position)
        # Person B: top-left (likely an NPC or background character)
        persons = [
            Detection(cls="person", confidence=0.7, bbox=(100, 100, 200, 300)),  # top-left
            Detection(cls="person", confidence=0.7, bbox=(900, 600, 1020, 850)),  # center-bottom
        ]
        player = detector._select_player(persons, frame_w=1920, frame_h=1080)
        assert player.bbox == (900, 600, 1020, 850)  # center-bottom selected

    def test_select_player_prefers_larger(self):
        """When both are similarly positioned, prefer the larger bbox."""
        from gpcg.infrastructure.player_detector import PlayerDetector, Detection
        detector = PlayerDetector()
        persons = [
            Detection(cls="person", confidence=0.7, bbox=(900, 600, 950, 700)),  # small
            Detection(cls="person", confidence=0.7, bbox=(880, 580, 1040, 850)),  # large
        ]
        player = detector._select_player(persons, frame_w=1920, frame_h=1080)
        # Larger bbox should be selected (closer to camera = more likely player)
        assert player.bbox == (880, 580, 1040, 850)

    def test_iou_no_overlap(self):
        from gpcg.infrastructure.player_detector import _iou
        assert _iou((0, 0, 100, 100), (200, 200, 300, 300)) == 0.0

    def test_iou_full_overlap(self):
        from gpcg.infrastructure.player_detector import _iou
        assert _iou((0, 0, 100, 100), (0, 0, 100, 100)) == 1.0

    def test_iou_partial_overlap(self):
        from gpcg.infrastructure.player_detector import _iou
        iou = _iou((0, 0, 100, 100), (50, 50, 150, 150))
        assert 0.0 < iou < 1.0

    def test_bbox_contains(self):
        from gpcg.infrastructure.player_detector import _bbox_contains
        assert _bbox_contains((0, 0, 200, 200), (50, 50, 100, 100)) is True
        assert _bbox_contains((0, 0, 100, 100), (50, 50, 200, 200)) is False


# ── GameplayAnalyzer cascade mode tests ───────────────────────────────────────


class TestGameplayAnalyzerCascade:
    """Tests for the cascaded analysis mode of GameplayAnalyzer."""

    def test_cascade_mode_enabled_when_camera_type_set(self):
        from gpcg.application.gameplay_analyzer import GameplayAnalyzer
        analyzer = GameplayAnalyzer(camera_type="third_person")
        assert analyzer.use_cascade is True

    def test_cascade_mode_disabled_when_unknown(self):
        from gpcg.application.gameplay_analyzer import GameplayAnalyzer
        analyzer = GameplayAnalyzer(camera_type="unknown")
        assert analyzer.use_cascade is False

    def test_cascade_mode_disabled_by_default(self):
        from gpcg.application.gameplay_analyzer import GameplayAnalyzer
        analyzer = GameplayAnalyzer()
        assert analyzer.use_cascade is False

    def test_infer_event_type_combat(self):
        from gpcg.application.gameplay_analyzer import GameplayAnalyzer
        from gpcg.infrastructure.player_detector import PlayerDetection
        analyzer = GameplayAnalyzer()
        det = PlayerDetection(
            player_bbox=(100, 100, 200, 300),
            player_detection=None,
        )
        assert analyzer._infer_event_type(
            "on_foot", "fighting", det, "none", "none"
        ) == "COMBAT"

    def test_infer_event_type_vehicle(self):
        from gpcg.application.gameplay_analyzer import GameplayAnalyzer
        from gpcg.infrastructure.player_detector import PlayerDetection
        analyzer = GameplayAnalyzer()
        det = PlayerDetection(
            player_bbox=(100, 100, 200, 300),
            player_detection=None,
        )
        assert analyzer._infer_event_type(
            "on_bike", "none", det, "none", "none"
        ) == "VEHICLE"

    def test_infer_event_type_dialogue_from_ui(self):
        from gpcg.application.gameplay_analyzer import GameplayAnalyzer
        from gpcg.infrastructure.player_detector import PlayerDetection
        analyzer = GameplayAnalyzer()
        det = PlayerDetection(
            player_bbox=(100, 100, 200, 300),
            player_detection=None,
        )
        assert analyzer._infer_event_type(
            "on_foot", "none", det, "none", "dialogue"
        ) == "DIALOGUE"

    def test_infer_event_type_chase_from_running(self):
        from gpcg.application.gameplay_analyzer import GameplayAnalyzer
        from gpcg.infrastructure.player_detector import PlayerDetection
        analyzer = GameplayAnalyzer()
        det = PlayerDetection(
            player_bbox=(100, 100, 200, 300),
            player_detection=None,
        )
        assert analyzer._infer_event_type(
            "on_foot", "none", det, "none", "none",
            movement_detail="running",
        ) == "CHASE"

    def test_merge_cascade_to_observation_combines_player_and_env(self):
        from gpcg.application.gameplay_analyzer import GameplayAnalyzer
        from gpcg.infrastructure.player_detector import PlayerDetection
        analyzer = GameplayAnalyzer()

        player_data = {
            "movement": "on_bike",
            "movement_detail": "riding",
            "combat_state": "none",
            "posture": "standing",
            "held_item": "none",
            "held_item_detail": "none",
            "interaction": "none",
            "action_description": "The player is riding a bicycle.",
            "confidence": 0.9,
        }
        env_data = {
            "location": "outdoor",
            "location_detail": "city street",
            "time_of_day": "day",
            "weather": "clear",
            "setting_details": "buildings and cars",
            "other_characters": "few",
            "other_characters_detail": "pedestrians walking",
            "ui_elements": "none",
            "environment_description": "A city street with pedestrians.",
            "confidence": 0.85,
        }
        det = PlayerDetection(
            player_bbox=(900, 500, 1020, 700),
            player_detection=None,
            nearby_vehicles=[],
            weapons=[],
        )

        obs = analyzer._merge_cascade_to_observation(
            player_data, env_data, det, timestamp=15.0
        )

        assert obs.event_type == "VEHICLE"
        assert "riding" in obs.actions
        assert "city street" in obs.location
        assert obs.activity_level == 0.4  # on_bike
        assert obs.visual_confidence > 0.0
        assert "on_bike" in obs.tags

    def test_merge_batch_observations_detects_progression(self):
        from gpcg.application.gameplay_analyzer import GameplayAnalyzer
        from gpcg.domain.gameplay_events import RawFrameObservation
        analyzer = GameplayAnalyzer()

        observations = [
            RawFrameObservation(
                timestamp=0.0, event_type="EXPLORATION",
                description="Player is standing.",
                actions=["standing"], activity_level=0.1,
                visual_confidence=0.8, tags=["on_foot"], characters=[],
                location="street",
            ),
            RawFrameObservation(
                timestamp=3.0, event_type="CHASE",
                description="Player is running.",
                actions=["running"], activity_level=0.5,
                visual_confidence=0.9, tags=["on_foot", "running"], characters=[],
                location="street",
            ),
            RawFrameObservation(
                timestamp=6.0, event_type="COMBAT",
                description="Player is fighting.",
                actions=["fighting"], activity_level=0.9,
                visual_confidence=0.85, tags=["on_foot", "fighting"], characters=[],
                location="street",
            ),
        ]

        merged = analyzer._merge_batch_observations(observations, start_time=0.0)

        # Should detect progression in description
        assert "standing" in merged.description.lower()
        assert "fighting" in merged.description.lower()
        # Activity = max
        assert merged.activity_level == 0.9
        # Actions = union in order
        assert "standing" in merged.actions
        assert "running" in merged.actions
        assert "fighting" in merged.actions
        # Tags = union
        assert "on_foot" in merged.tags
        assert "running" in merged.tags
        assert "fighting" in merged.tags


# ── CameraType enum tests ────────────────────────────────────────────────────


class TestCameraType:
    def test_camera_type_values(self):
        from gpcg.domain.models import CameraType
        assert CameraType.third_person.value == "third_person"
        assert CameraType.first_person.value == "first_person"
        assert CameraType.top_down.value == "top_down"
        assert CameraType.isometric.value == "isometric"
        assert CameraType.fixed.value == "fixed"
        assert CameraType.unknown.value == "unknown"

    def test_camera_type_is_string_enum(self):
        from gpcg.domain.models import CameraType
        assert CameraType("third_person") == CameraType.third_person
        assert isinstance(CameraType.third_person.value, str)
