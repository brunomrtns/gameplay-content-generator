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

    # Max chars for a title to fit comfortably in the opening/thumbnail.
    # Longer titles are shortened via LLM before rendering.
    _MAX_TITLE_CHARS = 40

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

        # Shorten long titles via LLM so they fit the video
        display_title = self._fit_title(title)

        # Build the video filter: scale image → crop → drawtext
        vf_parts = [
            f"scale={w}:{h}:force_original_aspect_ratio=increase",
            f"crop={w}:{h}",
        ]

        if config.opening_text_enabled and display_title:
            drawtext = self._build_drawtext(
                display_title, config.opening_text_position,
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

        # Shorten long titles via LLM so they fit the thumbnail
        display_title = self._fit_title(title)

        vf_parts = [
            f"scale={w}:{h}:force_original_aspect_ratio=increase",
            f"crop={w}:{h}",
        ]

        if config.thumbnail_text_enabled and display_title:
            drawtext = self._build_drawtext(
                display_title, config.thumbnail_text_position,
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

    def _fit_title(self, title: str) -> str:
        """Shorten a title if it's too long to fit in the opening/thumbnail.

        Uses LLM to generate a punchy, short version that preserves the
        essence. Falls back to hard truncation if LLM is unavailable.
        """
        title = title.strip()
        if len(title) <= self._MAX_TITLE_CHARS:
            return title

        # Try LLM shortening
        try:
            from gpcg.infrastructure.llm import get_llm
            llm = get_llm()
            system = (
                "Você é um editor de YouTube. Sua tarefa é encurtar títulos "
                "para caberem na capa/apresentação de vídeos verticais (9:16). "
                "Mantenho o gancho e a essência. Máximo 40 caracteres. "
                "Responda APENAS com o título encurtado, sem aspas, sem explicação."
            )
            prompt = (
                f'Título original: "{title}"\n\n'
                f'Encurte para no máximo {self._MAX_TITLE_CHARS} caracteres, '
                f'mantendo o impacto e o gancho. Responda só com o título.'
            )
            shortened = llm.chat(
                system, prompt,
                model="gemma3:4b",
                temperature=0.3,
                max_tokens=60,
            ).strip().strip('"').strip("'").strip("*").strip()

            # Validate
            if shortened and len(shortened) <= self._MAX_TITLE_CHARS and len(shortened) > 5:
                log.info(f'presentation: title shortened by LLM: "{title}" → "{shortened}"')
                return shortened
            # LLM returned something still too long — hard truncate
            if shortened and len(shortened) < len(title):
                # Truncate at word boundary
                return self._hard_truncate(shortened, self._MAX_TITLE_CHARS)
        except Exception as e:
            log.debug(f'presentation: LLM title shorten failed ({e}), using hard truncate')

        return self._hard_truncate(title, self._MAX_TITLE_CHARS)

    @staticmethod
    def _hard_truncate(text: str, max_chars: int) -> str:
        """Truncate text at word boundary, adding … if truncated."""
        if len(text) <= max_chars:
            return text
        # Try to break at a word boundary near the limit
        cut = text[:max_chars - 1]
        last_space = cut.rfind(" ")
        if last_space > max_chars // 2:
            cut = cut[:last_space]
        return cut.rstrip(" .!?,;:") + "…"

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
        Wraps long titles into multiple lines to fit the video width.
        Adaptively reduces font size if the title is too long.
        """
        max_font_size = int(height * _SIZE_MAP.get(size, 0.09))

        # Adaptively pick a font size that fits the text in ≤3 lines.
        # DejaVuSans-Bold avg char width ≈ 0.5 × font_size for mixed text.
        # Usable width = 85% of video width (7.5% margin each side).
        max_lines = 3
        usable_width = width * 0.85
        font_size = max_font_size
        wrapped = text
        for _ in range(10):
            avg_char_width = font_size * 0.5
            max_chars = max(8, int(usable_width / avg_char_width))
            wrapped = self._wrap_text(text, max_chars)
            num_lines = wrapped.count("\n") + 1
            if num_lines <= max_lines:
                break
            # Too many lines — shrink font
            font_size = int(font_size * 0.85)
        else:
            # Fallback: use the last wrapped text even if >3 lines
            avg_char_width = font_size * 0.5
            max_chars = max(8, int(usable_width / avg_char_width))
            wrapped = self._wrap_text(text, max_chars)

        # Write wrapped text to a temp file to avoid escaping issues
        # (drawtext textfile= is safer than text= for special chars)
        textfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, prefix="gpcg_dt_"
        )
        textfile.write(wrapped)
        textfile.close()

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

    @staticmethod
    def _wrap_text(text: str, max_chars: int) -> str:
        """Wrap text into lines of at most max_chars, breaking on spaces.

        Preserves explicit newlines in the input. Long words are hard-broken
        at max_chars if they don't fit.
        """
        lines: list[str] = []
        for paragraph in text.split("\n"):
            words = paragraph.split()
            if not words:
                lines.append("")
                continue
            current = ""
            for word in words:
                # If a single word is longer than max_chars, hard-break it
                while len(word) > max_chars:
                    if current:
                        lines.append(current)
                        current = ""
                    lines.append(word[:max_chars])
                    word = word[max_chars:]
                if not current:
                    current = word
                elif len(current) + 1 + len(word) <= max_chars:
                    current += " " + word
                else:
                    lines.append(current)
                    current = word
            if current:
                lines.append(current)
        return "\n".join(lines)
