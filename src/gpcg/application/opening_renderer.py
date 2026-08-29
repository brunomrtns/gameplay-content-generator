"""Opening renderer — pre-renders the visual opening (intro) with FFmpeg.

Produces ``scene_000.mp4`` — a short clip (2-3s) showing a strong image
with the video title in large text.

This clip is injected into the ``scene_dir`` before ``RenderPlanBuilder``
assembles the ``request_data``. The video-generate subprocess treats it
as any other scene (Ken Burns, transitions, etc.) — no video-generate
modification needed.

Also provides ``compose_thumbnail`` — composes the title text onto the
base image to produce the final thumbnail JPG.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from gpcg.domain.presentation_config import PresentationConfig
from gpcg.domain.video_profiles import get_resolution
from gpcg.logging import get_logger

log = get_logger(__name__)

# Font file for drawtext (system font, available in Docker + local)
_FONT_FILE = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Font size multipliers relative to video height
_SIZE_MAP = {
    "medium": 0.06,
    "large": 0.09,
    "xlarge": 0.13,
}

# Position y-offset ratios (from top)
_POSITION_MAP = {
    "top": 0.15,
    "middle": 0.40,
    "bottom": 0.70,
}


class OpeningRenderer:
    """Pre-renders the opening clip and composes the thumbnail with text."""

    def render_opening(
        self,
        image_path: Path,
        title: str,
        config: PresentationConfig,
        output_path: Path,
        video_format: str = "9:16",
    ) -> Optional[Path]:
        """Render the opening clip (scene_000.mp4).

        Args:
            image_path: Base image for the opening.
            title: The title text to overlay (already resolved).
            config: Presentation config.
            output_path: Where to save scene_000.mp4.
            video_format: "9:16", "16:9", "1:1", "4:5".

        Returns:
            Path to the rendered clip, or None on failure.
        """
        w, h = get_resolution(video_format)
        duration = config.opening_duration

        # Build the video filter: scale image → crop → drawtext
        vf_parts = [
            f"scale={w}:{h}:force_original_aspect_ratio=increase",
            f"crop={w}:{h}",
        ]

        if config.opening_text_enabled and title:
            drawtext = self._build_drawtext(
                title, config.opening_text_position,
                config.opening_text_color, config.opening_text_outline,
                config.opening_text_size, w, h,
            )
            vf_parts.append(drawtext)

        vf = ",".join(vf_parts)

        # Build FFmpeg command
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", str(image_path),
        ]

        # Silent audio track (the opening is visual only — narration
        # comes from the main narration_wav in the render stage)
        cmd.extend(["-f", "lavfi", "-i", f"anullsrc=channel_layout=mono:sample_rate=22050"])

        cmd.extend([
            "-t", f"{duration:.3f}",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "medium", "-crf", "21",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-video_track_timescale", "30000",  # match video-generate's timebase
            "-c:a", "aac", "-b:a", "128k", "-shortest",
        ])

        cmd.append(str(output_path))

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                log.error(f"opening render failed: {result.stderr[-500:]}")
                return None
        except subprocess.TimeoutExpired:
            log.error("opening render timed out")
            return None

        log.info(f"opening rendered: {output_path.name} ({duration:.1f}s, {w}x{h})")
        return output_path

    def compose_thumbnail(
        self,
        image_path: Path,
        title: str,
        config: PresentationConfig,
        output_path: Path,
        video_format: str = "9:16",
    ) -> Optional[Path]:
        """Compose the title text onto the base image → final thumbnail JPG.

        Args:
            image_path: Base image (selected frame or imported).
            title: Title text to overlay.
            config: Presentation config.
            output_path: Where to save the thumbnail JPG.
            video_format: For resolution reference.

        Returns:
            Path to the composed thumbnail, or None on failure.
        """
        w, h = get_resolution(video_format)

        vf_parts = [
            f"scale={w}:{h}:force_original_aspect_ratio=increase",
            f"crop={w}:{h}",
        ]

        if config.thumbnail_text_enabled and title:
            drawtext = self._build_drawtext(
                title, config.thumbnail_text_position,
                config.thumbnail_text_color, config.thumbnail_text_outline,
                config.thumbnail_text_size, w, h,
            )
            vf_parts.append(drawtext)

        vf = ",".join(vf_parts)

        cmd = [
            "ffmpeg", "-y",
            "-i", str(image_path),
            "-vf", vf,
            "-frames:v", "1",
            "-q:v", "2",
            str(output_path),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                log.error(f"thumbnail compose failed: {result.stderr[-500:]}")
                return None
        except subprocess.TimeoutExpired:
            log.error("thumbnail compose timed out")
            return None

        log.info(f"thumbnail composed: {output_path.name} ({w}x{h})")
        return output_path

    def _build_drawtext(
        self,
        text: str,
        position: str,
        color: str,
        outline: str,
        size: str,
        width: int,
        height: int,
    ) -> str:
        """Build an FFmpeg drawtext filter string.

        Escapes special characters for FFmpeg drawtext syntax.
        Uses textfile to avoid complex escaping of the text content.
        """
        # Write text to a temp file to avoid escaping issues
        # (drawtext textfile= is safer than text= for special chars)
        textfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, prefix="gpcg_dt_"
        )
        textfile.write(text)
        textfile.close()

        font_size = int(height * _SIZE_MAP.get(size, 0.09))
        y_ratio = _POSITION_MAP.get(position, 0.40)

        # Calculate y position (centered vertically at the ratio point)
        # Use (h-text_h)*ratio for vertical centering at the ratio
        y_expr = f"(h-text_h)*{y_ratio:.2f}"

        # Horizontal centering
        x_expr = "(w-text_w)/2"

        # Build drawtext filter
        # Note: box=1 with boxcolor for readability, borderw for outline
        filter_str = (
            f"drawtext="
            f"fontfile='{_FONT_FILE}':"
            f"textfile='{textfile.name}':"
            f"fontcolor={color}:"
            f"fontsize={font_size}:"
            f"x={x_expr}:"
            f"y={y_expr}:"
            f"borderw=4:"
            f"bordercolor={outline}:"
            f"box=1:"
            f"boxcolor={outline}@0.3:"
            f"boxborderw=20"
        )

        return filter_str
