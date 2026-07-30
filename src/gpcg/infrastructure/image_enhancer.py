"""Image enhancement for the cascaded gameplay analysis pipeline.

Provides preprocessing operations applied BETWEEN detection stages to improve
the signal-to-noise ratio of the next stage — the same principle as the
plate-recognition pipeline (detect car → crop → detect plate → crop →
binarize → OCR).

Operations (applied in order when requested):
  1. Crop      — extract a region of interest (bbox) from the frame
  2. Upscale   — resize the crop to a minimum size using Lanczos interpolation
  3. Sharpen   — unsharp mask to recover edges lost during upscaling
  4. Contrast  — CLAHE (Contrast Limited Adaptive Histogram Equalization) to
                 normalize lighting without washing out highlights
  5. Denoise   — fast non-local-means denoise (optional, off by default —
                 it can erase small details like weapons)

All operations use OpenCV (headless). No external services.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class EnhancementConfig:
    """Configuration for the image enhancement pipeline.

    Defaults are tuned for gameplay frames (1080p source, third-person games
    where the player is small and needs upscaling).
    """

    # Upscale: target the longest side of the crop to at least this many pixels.
    # 640 is a good minimum for VLM consumption — below that, gemma3:12b
    # struggles to identify small objects like weapons.
    min_long_side: int = 640

    # Upscale interpolation — Lanczos is best for upscaling (sharp edges).
    interpolation: int = cv2.INTER_LANCZOS4

    # Sharpen: unsharp mask strength. 0 = off. Typical: 0.3–0.6.
    # Too high creates halos around edges.
    sharpen_strength: float = 0.4

    # Contrast: CLAHE clip limit. 0 = off. Typical: 2.0–3.0.
    # Higher = more aggressive local contrast enhancement.
    clahe_clip_limit: float = 2.0
    clahe_tile_grid_size: tuple[int, int] = (8, 8)

    # Denoise: fastNlMeansDenoisingColored strength. 0 = off.
    # WARNING: can erase small details (weapons, items). Off by default.
    denoise_strength: int = 0

    # Padding around bbox before crop (fraction of bbox size).
    # Gives the VLM context around the player (ground, nearby objects).
    # 0.3 = 30% padding on each side.
    crop_padding: float = 0.3

    # Minimum bbox size (in pixels) to bother upscaling. Below this, the
    # detection is probably noise.
    min_bbox_size: int = 20


def crop_with_padding(
    frame: np.ndarray,
    bbox: tuple[int, int, int, int],
    padding_frac: float = 0.3,
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Crop a region from the frame with padding around the bbox.

    Args:
        frame: HxWxC BGR image (OpenCV format)
        bbox: (x1, y1, x2, y2) in pixels
        padding_frac: fraction of bbox size to add as padding on each side

    Returns:
        (crop, adjusted_bbox) — the cropped image and the bbox in original
        frame coordinates (with padding, clamped to frame bounds)
    """
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = bbox
    bw = x2 - x1
    bh = y2 - y1
    pad_x = int(bw * padding_frac)
    pad_y = int(bh * padding_frac)
    # Clamp to frame bounds
    px1 = max(0, x1 - pad_x)
    py1 = max(0, y1 - pad_y)
    px2 = min(w, x2 + pad_x)
    py2 = min(h, y2 + pad_y)
    crop = frame[py1:py2, px1:px2]
    return crop, (px1, py1, px2, py2)


def upscale_to_min(
    image: np.ndarray,
    min_long_side: int = 640,
    interpolation: int = cv2.INTER_LANCZOS4,
) -> np.ndarray:
    """Upscale the image so its longest side is at least min_long_side pixels.

    If the image is already larger, it's returned unchanged.
    """
    h, w = image.shape[:2]
    long_side = max(h, w)
    if long_side >= min_long_side:
        return image
    scale = min_long_side / long_side
    new_w = int(w * scale)
    new_h = int(h * scale)
    return cv2.resize(image, (new_w, new_h), interpolation=interpolation)


def sharpen(
    image: np.ndarray,
    strength: float = 0.4,
) -> np.ndarray:
    """Apply unsharp mask sharpening.

    sharpened = image + strength * (image - blurred)
    """
    if strength <= 0:
        return image
    blurred = cv2.GaussianBlur(image, (0, 0), sigmaX=3)
    sharpened = cv2.addWeighted(image, 1.0 + strength, blurred, -strength, 0)
    return sharpened


def enhance_contrast(
    image: np.ndarray,
    clip_limit: float = 2.0,
    tile_grid_size: tuple[int, int] = (8, 8),
) -> np.ndarray:
    """Apply CLAHE (Contrast Limited Adaptive Histogram Equalization) on the L
    channel of the LAB color space. Normalizes lighting without color shifts.
    """
    if clip_limit <= 0:
        return image
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=tile_grid_size)
    l = clahe.apply(l)
    lab = cv2.merge((l, a, b))
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def denoise(
    image: np.ndarray,
    strength: int = 10,
) -> np.ndarray:
    """Apply fast non-local-means denoising (colored).

    WARNING: can erase small details like weapons. Use sparingly.
    """
    if strength <= 0:
        return image
    return cv2.fastNlMeansDenoisingColored(image, None, strength, strength, 7, 21)


def enhance_crop(
    frame: np.ndarray,
    bbox: tuple[int, int, int, int],
    config: EnhancementConfig | None = None,
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Full enhancement pipeline for a detected region.

    This is the main entry point: given a frame and a bbox (e.g., from YOLO
    player detection), produce an enhanced crop suitable for VLM analysis.

    Pipeline: crop (with padding) → upscale → sharpen → contrast → [denoise]

    Args:
        frame: HxWxC BGR image (OpenCV format)
        bbox: (x1, y1, x2, y2) in pixels
        config: enhancement configuration (defaults if None)

    Returns:
        (enhanced_crop, adjusted_bbox) — the enhanced crop and the bbox in
        original frame coordinates (with padding)
    """
    cfg = config or EnhancementConfig()
    crop, adjusted_bbox = crop_with_padding(frame, bbox, cfg.crop_padding)
    crop = upscale_to_min(crop, cfg.min_long_side, cfg.interpolation)
    crop = sharpen(crop, cfg.sharpen_strength)
    crop = enhance_contrast(crop, cfg.clahe_clip_limit, cfg.clahe_tile_grid_size)
    if cfg.denoise_strength > 0:
        crop = denoise(crop, cfg.denoise_strength)
    return crop, adjusted_bbox


def save_image(image: np.ndarray, path: str) -> None:
    """Save an image (BGR) to a file. Convenience wrapper."""
    cv2.imwrite(path, image)
