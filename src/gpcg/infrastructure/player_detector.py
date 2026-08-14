"""Player detector — YOLO-based cascaded detection of the player character.

This is the first stage of the cascaded gameplay analysis pipeline. It uses
YOLOv8 (object detection) to locate the player character in the frame, then
crops and enhances that region for the VLM (Vision-Language Model) to
classify the player's action.

## Why YOLO + VLM (cascaded)?

YOLO is fast and reliable for LOCALIZATION (where is the person/vehicle?),
but its class taxonomy (COCO: 80 classes) doesn't cover game-specific
objects (go-karts, skateboards in Bully's art style, weapons that look
like baseball bats, etc.). The VLM (gemma3:12b) is excellent at
CLASSIFICATION in context ("that's a go-kart", "the player is holding a
weapon") but fails when the target is small in the frame.

The cascade combines both strengths:
  1. YOLO detects "person" (and optionally vehicle/bicycle) bboxes
  2. Heuristics select the most likely player bbox (largest, most central)
  3. The bbox is cropped + upscaled + enhanced (image_enhancer.py)
  4. The VLM analyzes the enhanced crop → action classification

## Camera-type awareness

The detection strategy adapts to the game's camera type:
  - third_person: player is a "person" bbox, typically center-low. Select
    the most central person. Also detect nearby vehicles/bicycles (the
    player might be riding them).
  - first_person: no player character visible. Instead, inspect the lower
    corners of the frame for held weapons/items (arms, gun barrels).
    YOLO can detect "cell phone", "bottle", etc. as proxies, but the VLM
    is better here — we crop the lower third and let the VLM find hands/
    weapons.
  - top_down / isometric: player is small, centered. Same as third_person
    but with a tighter crop.
  - fixed: player position varies. Rely on YOLO person detection.
  - unknown: fall back to full-frame analysis (legacy behavior).

## Vehicle detection

For third-person games with vehicles (Bully has bicycles, go-karts,
skateboards), we also detect COCO classes: bicycle, car, motorcycle, bus,
truck, boat. If a vehicle bbox overlaps or is very close to the player
bbox, the player is likely riding it. This is passed as context to the VLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from gpcg.logging import get_logger

log = get_logger(__name__)


# COCO classes relevant to gameplay player detection
PERSON_CLASS = "person"
VEHICLE_CLASSES = frozenset({
    "bicycle", "car", "motorcycle", "bus", "truck", "boat",
})
WEAPON_CLASSES = frozenset({
    "baseball bat", "knife", "scissors",
})


@dataclass
class Detection:
    """A single YOLO detection."""
    cls: str
    confidence: float
    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2 in pixels
    area: float = 0.0  # bbox area in pixels²

    def __post_init__(self):
        x1, y1, x2, y2 = self.bbox
        self.area = float((x2 - x1) * (y2 - y1))


@dataclass
class PlayerDetection:
    """Result of player detection on a frame.

    Attributes:
        player_bbox: the most likely player bbox (None if no person detected)
        player_detection: the Detection object for the player
        nearby_vehicles: vehicle detections overlapping or near the player
        weapons: weapon detections in the frame (any, not just near player)
        all_detections: all YOLO detections (for debugging)
        frame_size: (width, height) of the source frame
        strategy: which detection strategy was used (camera-type dependent)
    """
    player_bbox: Optional[tuple[int, int, int, int]]
    player_detection: Optional[Detection]
    nearby_vehicles: list[Detection] = field(default_factory=list)
    weapons: list[Detection] = field(default_factory=list)
    all_detections: list[Detection] = field(default_factory=list)
    frame_size: tuple[int, int] = (0, 0)
    strategy: str = "third_person"

    @property
    def has_player(self) -> bool:
        return self.player_bbox is not None

    @property
    def is_riding(self) -> bool:
        """True if a vehicle is overlapping or very close to the player."""
        return len(self.nearby_vehicles) > 0

    @property
    def is_armed(self) -> bool:
        """True if a weapon is detected near the player."""
        return len(self.weapons) > 0

    def to_dict(self) -> dict:
        return {
            "player_bbox": list(self.player_bbox) if self.player_bbox else None,
            "player_confidence": self.player_detection.confidence if self.player_detection else None,
            "is_riding": self.is_riding,
            "nearby_vehicles": [
                {"cls": d.cls, "confidence": d.confidence, "bbox": list(d.bbox)}
                for d in self.nearby_vehicles
            ],
            "is_armed": self.is_armed,
            "weapons": [
                {"cls": d.cls, "confidence": d.confidence, "bbox": list(d.bbox)}
                for d in self.weapons
            ],
            "strategy": self.strategy,
            "total_detections": len(self.all_detections),
        }


@dataclass
class DetectorConfig:
    """Configuration for the PlayerDetector."""
    # YOLO model path. yolov8m.pt is a good balance (medium size, good accuracy).
    # yolov8s.pt is faster but less accurate; yolov8l.pt is more accurate but slower.
    model_path: str = "yolov8m.pt"

    # Confidence threshold for YOLO detections
    confidence_threshold: float = 0.25

    # IoU threshold for NMS (non-maximum suppression)
    iou_threshold: float = 0.45

    # Device: "cuda" (GPU) or "cpu"
    device: str = "cuda"

    # Minimum bbox area (in pixels²) to consider a person detection valid.
    # Filters out tiny false positives in the background.
    min_person_area: float = 500.0

    # When selecting the player among multiple persons, prefer the one closest
    # to the center-bottom of the frame (typical third-person framing).
    # This weight controls how much center-bottom preference matters vs. size.
    # 0 = pure size preference, 1 = pure center-bottom preference.
    center_preference_weight: float = 0.4

    # A vehicle is "near" the player if its IoU with the player bbox is above
    # this threshold, OR if the distance between their centers is less than
    # this fraction of the player bbox size.
    vehicle_overlap_iou: float = 0.1
    vehicle_near_distance_frac: float = 0.5

    # For first_person: crop the lower fraction of the frame for weapon/hand
    # inspection.
    first_person_lower_frac: float = 0.4


class PlayerDetector:
    """YOLO-based player detector with camera-type awareness.

    Usage:
        detector = PlayerDetector()
        detection = detector.detect(frame, camera_type="third_person")
        if detection.has_player:
            crop = enhance_crop(frame, detection.player_bbox)
            # ... send crop to VLM for action classification
    """

    def __init__(self, config: DetectorConfig | None = None) -> None:
        if config is None:
            from gpcg.config import get_settings
            config = DetectorConfig(device=get_settings().gpcg_yolo_device)
        self.config = config
        self._model = None  # lazy load

    @property
    def model(self):
        """Lazy-load the YOLO model on first use."""
        if self._model is None:
            from ultralytics import YOLO
            log.info(f"loading YOLO model: {self.config.model_path}")
            self._model = YOLO(self.config.model_path)
            log.info(f"YOLO model loaded on device: {self.config.device}")
        return self._model

    def detect(
        self,
        frame: np.ndarray,
        camera_type: str = "third_person",
    ) -> PlayerDetection:
        """Detect the player in a frame.

        Args:
            frame: HxWxC BGR image (OpenCV format)
            camera_type: one of CameraType values (third_person, first_person, etc.)

        Returns:
            PlayerDetection with the player bbox and nearby objects
        """
        h, w = frame.shape[:2]
        ct = camera_type

        if ct == "first_person":
            return self._detect_first_person(frame, w, h)
        elif ct in ("top_down", "isometric"):
            return self._detect_top_down(frame, w, h, strategy=ct)
        elif ct == "fixed":
            return self._detect_third_person(frame, w, h, strategy="fixed")
        else:
            # third_person or unknown (fall back to third_person strategy)
            return self._detect_third_person(frame, w, h, strategy=ct)

    def _run_yolo(self, frame: np.ndarray) -> list[Detection]:
        """Run YOLO inference and return all detections above threshold."""
        results = self.model(
            frame,
            verbose=False,
            device=self.config.device,
            conf=self.config.confidence_threshold,
            iou=self.config.iou_threshold,
        )
        detections: list[Detection] = []
        for r in results:
            boxes = r.boxes
            for b in boxes:
                cls_name = self.model.names[int(b.cls)]
                conf = float(b.conf)
                xyxy = tuple(int(round(v)) for v in b.xyxy[0].tolist())
                detections.append(Detection(cls=cls_name, confidence=conf, bbox=xyxy))
        return detections

    def _detect_third_person(
        self,
        frame: np.ndarray,
        w: int,
        h: int,
        strategy: str = "third_person",
    ) -> PlayerDetection:
        """Third-person detection: find the most likely player among persons."""
        all_dets = self._run_yolo(frame)
        persons = [d for d in all_dets if d.cls == PERSON_CLASS and d.area >= self.config.min_person_area]
        vehicles = [d for d in all_dets if d.cls in VEHICLE_CLASSES]
        weapons = [d for d in all_dets if d.cls in WEAPON_CLASSES]

        if not persons:
            return PlayerDetection(
                player_bbox=None,
                player_detection=None,
                nearby_vehicles=[],
                weapons=weapons,
                all_detections=all_dets,
                frame_size=(w, h),
                strategy=strategy,
            )

        # Select the most likely player: largest + most central-bottom
        player = self._select_player(persons, w, h)
        nearby_vehicles = self._find_nearby_vehicles(player, vehicles)

        # Only report weapons that OVERLAP the player bbox (likely in hand).
        # Weapons elsewhere in the frame are not the player's.
        nearby_weapons = [
            w for w in weapons
            if _iou(player.bbox, w.bbox) > 0.0
            or _bbox_contains(player.bbox, w.bbox)
        ]

        return PlayerDetection(
            player_bbox=player.bbox,
            player_detection=player,
            nearby_vehicles=nearby_vehicles,
            weapons=nearby_weapons,  # only weapons near/in the player's hand
            all_detections=all_dets,
            frame_size=(w, h),
            strategy=strategy,
        )

    def _detect_top_down(
        self,
        frame: np.ndarray,
        w: int,
        h: int,
        strategy: str = "top_down",
    ) -> PlayerDetection:
        """Top-down/isometric: same as third_person but with tighter selection."""
        # Same logic — player is centered but small. The crop + upscale will
        # handle the small size.
        return self._detect_third_person(frame, w, h, strategy=strategy)

    def _detect_first_person(
        self,
        frame: np.ndarray,
        w: int,
        h: int,
    ) -> PlayerDetection:
        """First-person: no player character. Crop lower portion for weapons/hands.

        In first-person games, the player's arms/weapon appear in the lower
        portion of the screen. We don't try to detect a "person" — instead,
        we create a synthetic bbox covering the lower third of the frame and
        let the VLM inspect it for held items.
        """
        all_dets = self._run_yolo(frame)
        weapons = [d for d in all_dets if d.cls in WEAPON_CLASSES]

        # Synthetic bbox: lower portion of the frame
        lower_frac = self.config.first_person_lower_frac
        y1 = int(h * (1.0 - lower_frac))
        y2 = h
        x1 = 0
        x2 = w
        synthetic_bbox = (x1, y1, x2, y2)

        # Create a synthetic Detection for the "player view"
        player_det = Detection(
            cls="first_person_view",
            confidence=1.0,
            bbox=synthetic_bbox,
        )

        return PlayerDetection(
            player_bbox=synthetic_bbox,
            player_detection=player_det,
            nearby_vehicles=[],
            weapons=weapons,
            all_detections=all_dets,
            frame_size=(w, h),
            strategy="first_person",
        )

    def _select_player(
        self,
        persons: list[Detection],
        frame_w: int,
        frame_h: int,
    ) -> Detection:
        """Select the most likely player among multiple person detections.

        Heuristic: combine area (larger = closer = more likely player) with
        center-bottom preference (third-person cameras frame the player
        center-low).
        """
        if len(persons) == 1:
            return persons[0]

        # Normalize scores to [0, 1]
        max_area = max(p.area for p in persons)
        center_x = frame_w / 2
        center_y = frame_h * 0.65  # slightly below center (typical third-person)

        def score(p: Detection) -> float:
            area_score = p.area / max_area if max_area > 0 else 0
            px = (p.bbox[0] + p.bbox[2]) / 2
            py = (p.bbox[1] + p.bbox[3]) / 2
            # Distance from center-bottom, normalized by frame diagonal
            dist = ((px - center_x) ** 2 + (py - center_y) ** 2) ** 0.5
            diag = (frame_w ** 2 + frame_h ** 2) ** 0.5
            center_score = 1.0 - (dist / diag)
            w_ = self.config.center_preference_weight
            return area_score * (1.0 - w_) + center_score * w_

        return max(persons, key=score)

    def _find_nearby_vehicles(
        self,
        player: Detection,
        vehicles: list[Detection],
    ) -> list[Detection]:
        """Find vehicles overlapping or near the player bbox."""
        nearby = []
        for v in vehicles:
            iou = _iou(player.bbox, v.bbox)
            if iou >= self.config.vehicle_overlap_iou:
                nearby.append(v)
                continue
            # Check center distance
            pcx = (player.bbox[0] + player.bbox[2]) / 2
            pcy = (player.bbox[1] + player.bbox[3]) / 2
            vcx = (v.bbox[0] + v.bbox[2]) / 2
            vcy = (v.bbox[1] + v.bbox[3]) / 2
            dist = ((pcx - vcx) ** 2 + (pcy - vcy) ** 2) ** 0.5
            player_size = ((player.bbox[2] - player.bbox[0]) + (player.bbox[3] - player.bbox[1])) / 2
            if dist < player_size * self.config.vehicle_near_distance_frac:
                nearby.append(v)
        return nearby


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Intersection over Union between two bboxes."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _bbox_contains(outer: tuple[int, int, int, int], inner: tuple[int, int, int, int]) -> bool:
    """Check if the outer bbox fully contains the inner bbox."""
    ox1, oy1, ox2, oy2 = outer
    ix1, iy1, ix2, iy2 = inner
    return ox1 <= ix1 and oy1 <= iy1 and ox2 >= ix2 and oy2 >= iy2


def load_frame(path: str | Path) -> np.ndarray:
    """Load an image file as a BGR numpy array (OpenCV format)."""
    frame = cv2.imread(str(path))
    if frame is None:
        raise FileNotFoundError(f"could not load image: {path}")
    return frame
