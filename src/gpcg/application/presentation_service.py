"""Presentation service — orchestrates the Presentation Layer stage.

This is an OPTIONAL pipeline stage that runs between ``gameplay_selection``
and ``render_plan``. When the Presentation Layer is disabled (the default),
this stage is a complete no-op and the pipeline behaves exactly as before.

When enabled, it:
  1. Selects a thumbnail image (auto/imported/fixed) via ThumbnailSelector.
  2. Composes the title text onto the image → final thumbnail JPG.
  3. Pre-renders the opening clip (scene_000.mp4) via OpeningRenderer.
  4. Injects scene_000.mp4 into the scene_dir.
  5. Adjusts the selected_clips scene_timeline to include the opening.
  6. Stores artifacts: thumbnail_path, opening_duration.

Failures are NON-FATAL: if anything goes wrong, the stage logs a warning
and the pipeline continues without the Presentation Layer (graceful
degradation to the current behavior).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from gpcg.domain.presentation_config import PresentationConfig
from gpcg.logging import get_logger

log = get_logger(__name__)


@dataclass
class PresentationResult:
    """Result of the presentation stage."""
    success: bool = False
    thumbnail_path: Optional[str] = None
    opening_scene_path: Optional[str] = None
    opening_duration: float = 0.0
    # Updated selected_clips with opening prepended (if opening enabled)
    updated_clips: Optional[list[dict]] = None
    error: str = ""


class PresentationService:
    """Orchestrates the Presentation Layer (thumbnail + opening)."""

    def __init__(self) -> None:
        from gpcg.application.thumbnail_selector import ThumbnailSelector
        from gpcg.application.opening_renderer import OpeningRenderer
        self.selector = ThumbnailSelector()
        self.renderer = OpeningRenderer()

    def apply(
        self,
        session: Session,
        job_id: int,
        topic: str,
        title: str,
        script_first_line: str,
        gameplay_source_id: Optional[int],
        gameplay_source_path: str,
        selected_clips: list[dict],
        config: PresentationConfig,
        scene_dir: Path,
        video_format: str,
        narration_wav: Optional[Path] = None,
    ) -> PresentationResult:
        """Apply the Presentation Layer.

        Args:
            session: DB session.
            job_id: Job ID (for logging).
            topic: ContentPlan.topic (the video topic).
            title: The resolved video title (social_title or topic).
            script_first_line: First line of the script (for "hook" text source).
            gameplay_source_id: GameplaySource ID (for event lookup in auto mode).
            gameplay_source_path: Path to the gameplay video file.
            selected_clips: The selected_clips list from gameplay_selection.
            config: Presentation config.
            scene_dir: The temp scene directory (where scene_NNN.mp4 go).
            video_format: "9:16", "16:9", etc.
            narration_wav: Path to the narration WAV (for opening TTS, if enabled).

        Returns:
            PresentationResult with thumbnail/opening paths.
        """
        if not config.enabled:
            return PresentationResult(success=False, error="presentation disabled")

        result = PresentationResult()
        work_dir = scene_dir / "_presentation"
        work_dir.mkdir(parents=True, exist_ok=True)

        try:
            # ── 1. Select thumbnail image ───────────────────────────────────
            thumb_result = None
            if config.thumbnail_enabled:
                thumb_result = self.selector.select(
                    session=session,
                    topic=topic,
                    gameplay_source_id=gameplay_source_id,
                    gameplay_source_path=gameplay_source_path,
                    config=config,
                    output_dir=work_dir,
                )

            # ── 2. Compose thumbnail with text ──────────────────────────────
            if thumb_result and config.thumbnail_enabled:
                thumb_text = self._resolve_text(
                    config.thumbnail_text_source,
                    config.thumbnail_text_custom,
                    title,
                    script_first_line,
                )
                thumb_final = work_dir / "thumbnail_final.jpg"
                composed = self.renderer.compose_thumbnail(
                    image_path=thumb_result.image_path,
                    title=thumb_text,
                    config=config,
                    output_path=thumb_final,
                    video_format=video_format,
                )
                if composed:
                    result.thumbnail_path = str(composed)

            # ── 3. Render opening ───────────────────────────────────────────
            if config.opening_enabled and thumb_result:
                # Resolve opening image
                opening_image = self._resolve_opening_image(
                    thumb_result, config, work_dir, session,
                    gameplay_source_id, gameplay_source_path, topic,
                )

                if opening_image:
                    opening_text = self._resolve_text(
                        config.opening_text_source,
                        config.opening_text_custom,
                        title,
                        script_first_line,
                    )

                    # Resolve narration for opening
                    opening_narration = None
                    if config.opening_narration_enabled:
                        narration_text = config.opening_narration_text or title
                        if narration_wav:
                            # Use the existing narration WAV's first N seconds
                            # (simpler than generating new TTS for the title)
                            # For now, use silence — TTS for title would require
                            # a separate TTS call which adds complexity.
                            # The narration_wav param is reserved for future use.
                            pass
                        # Generate silent audio for now (TTS title is a future enhancement)

                    scene_000 = scene_dir / "scene_000.mp4"
                    rendered = self.renderer.render_opening(
                        image_path=opening_image,
                        title=opening_text,
                        config=config,
                        output_path=scene_000,
                        video_format=video_format,
                        narration_wav=opening_narration,
                    )
                    if rendered:
                        result.opening_scene_path = str(rendered)
                        result.opening_duration = config.opening_duration

                        # Adjust selected_clips: prepend opening as scene 0,
                        # shift all other scenes by +1
                        updated = []
                        # Opening clip entry
                        updated.append({
                            "asset_id": None,
                            "source_id": gameplay_source_id,
                            "source_path": str(rendered),
                            "start": 0.0,
                            "end": config.opening_duration,
                            "duration": config.opening_duration,
                            "scene_index": 0,
                            "event_id": None,
                            "selection_reason": "presentation_opening",
                            "usage_count_at_selection": 0,
                        })
                        # Shift existing clips' scene_index by +1
                        for clip in selected_clips:
                            shifted = dict(clip)
                            shifted["scene_index"] = clip.get("scene_index", 0) + 1
                            updated.append(shifted)
                        result.updated_clips = updated
                    else:
                        log.warning(f"presentation: opening render failed for job {job_id}, "
                                    f"continuing without opening")
                else:
                    log.warning(f"presentation: no opening image for job {job_id}")
            else:
                # No opening — but still update clips if we need to ensure
                # scene numbering starts at 1 (not 0) to avoid conflict with
                # a potential scene_000 from video-generate's file discovery
                # Actually, RenderPlanBuilder sorts by scene_index and numbers
                # scenes sequentially, so no shift needed when opening is off.
                pass

            result.success = bool(result.thumbnail_path or result.opening_scene_path)
            if not result.success:
                result.error = "no thumbnail or opening produced"

        except Exception as e:
            log.warning(
                f"presentation stage failed for job {job_id}: {e} — "
                f"continuing without presentation layer (non-fatal)"
            )
            result.success = False
            result.error = str(e)

        return result

    def _resolve_text(
        self,
        source: str,
        custom: str,
        title: str,
        script_first_line: str,
    ) -> str:
        """Resolve the text to display based on the configured source."""
        if source == "custom":
            return custom or title
        elif source == "hook":
            return script_first_line or title
        else:  # "title" or default
            return title

    def _resolve_opening_image(
        self,
        thumb_result,
        config: PresentationConfig,
        work_dir: Path,
        session: Session,
        gameplay_source_id: Optional[int],
        gameplay_source_path: str,
        topic: str,
    ) -> Optional[Path]:
        """Resolve the image to use for the opening."""
        if config.opening_image_mode == "same_as_thumbnail":
            return thumb_result.image_path if thumb_result else None

        if config.opening_image_mode in ("imported", "fixed"):
            if config.opening_image_path:
                p = Path(config.opening_image_path)
                if p.exists():
                    return p
            return None

        # "auto" — independent selection
        if gameplay_source_id and gameplay_source_path:
            opening_config = PresentationConfig(
                thumbnail_mode="auto",
                auto_candidate_count=config.auto_candidate_count,
                auto_min_interesting=config.auto_min_interesting,
                auto_min_confidence=config.auto_min_confidence,
            )
            result = self.selector.select(
                session=session,
                topic=topic,
                gameplay_source_id=gameplay_source_id,
                gameplay_source_path=gameplay_source_path,
                config=opening_config,
                output_dir=work_dir / "opening",
            )
            return result.image_path if result else None

        return None
