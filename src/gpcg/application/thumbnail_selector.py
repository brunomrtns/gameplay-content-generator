"""Thumbnail selector — chooses the best frame for the video cover.

Uses the GameplayEvent index (produced during mapping) to find visually
and semantically relevant frames. Three modes:

  - "auto":     query events, score by relevance to the video topic,
                extract candidate frames, pick the best.
  - "imported": use a user-provided image (per-job).
  - "fixed":    use a fixed image for all videos of this automation.

The selector does NOT compose text onto the image — that's done by the
PresentationService after selection. It only finds the base image.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from gpcg.domain.presentation_config import PresentationConfig
from gpcg.infrastructure.media import generate_thumbnail, probe
from gpcg.logging import get_logger

log = get_logger(__name__)


@dataclass
class ThumbnailResult:
    """Result of thumbnail selection."""
    image_path: Path
    # Provenance: which event informed this selection (None for imported/fixed)
    source_event_id: Optional[int] = None
    source_timestamp: float = 0.0  # timestamp in the gameplay video
    selection_reason: str = ""  # "semantic_event", "interesting_fallback", "imported", "fixed"
    # The gameplay source video path (for opening re-extraction if needed)
    gameplay_source_path: str = ""


class ThumbnailSelector:
    """Selects the best image for the thumbnail/opening."""

    def select(
        self,
        session: Session,
        topic: str,
        gameplay_source_id: Optional[int],
        gameplay_source_path: str,
        config: PresentationConfig,
        output_dir: Path,
    ) -> Optional[ThumbnailResult]:
        """Select a thumbnail image.

        Args:
            session: DB session for querying GameplayEvents.
            topic: The video topic/title (for semantic matching in auto mode).
            gameplay_source_id: The GameplaySource ID (for event lookup).
            gameplay_source_path: Path to the gameplay video file.
            config: Presentation config.
            output_dir: Where to save the extracted frame.

        Returns:
            ThumbnailResult, or None if selection failed (caller falls back
            to the default mid-video thumbnail).
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        if config.thumbnail_mode == "imported" or config.thumbnail_mode == "fixed":
            return self._select_imported(config, output_dir)

        # Auto mode
        if not gameplay_source_id or not gameplay_source_path:
            log.warning("thumbnail auto mode: no gameplay source, falling back")
            return None

        return self._select_auto(
            session, topic, gameplay_source_id, gameplay_source_path, config, output_dir
        )

    def _select_imported(
        self, config: PresentationConfig, output_dir: Path
    ) -> Optional[ThumbnailResult]:
        """Use a user-provided image."""
        image_path = config.thumbnail_image_path
        if not image_path:
            log.warning(f"thumbnail mode={config.thumbnail_mode} but no image_path set")
            return None
        p = Path(image_path)
        if not p.exists():
            log.warning(f"thumbnail image not found: {p}")
            return None
        return ThumbnailResult(
            image_path=p,
            selection_reason=config.thumbnail_mode,
        )

    def _select_auto(
        self,
        session: Session,
        topic: str,
        source_id: int,
        source_path: str,
        config: PresentationConfig,
        output_dir: Path,
    ) -> Optional[ThumbnailResult]:
        """Auto-select the best frame using the GameplayEvent index."""
        from gpcg.domains.games.models import GameplayEvent

        events = session.query(GameplayEvent).filter(
            GameplayEvent.source_id == source_id,
            GameplayEvent.interesting_score >= config.auto_min_interesting,
            GameplayEvent.visual_confidence >= config.auto_min_confidence,
        ).all()

        if not events:
            log.info(f"thumbnail auto: no events pass filters for source {source_id}, "
                     f"using mid-video fallback")
            return self._mid_video_fallback(source_path, output_dir, source_id)

        # Score each event
        scored = []
        topic_keywords = self._extract_keywords(topic)
        for event in events:
            score = self._score_event(event, topic_keywords, source_id)
            scored.append((score, event))
        scored.sort(key=lambda x: x[0], reverse=True)

        # Extract top N candidates
        n = min(config.auto_candidate_count, len(scored))
        candidates = []
        for i in range(n):
            score, event = scored[i]
            # Use the midpoint of the event as the frame timestamp
            ts = (event.start_time + event.end_time) / 2.0
            out_path = output_dir / f"thumb_candidate_{i:02d}.jpg"
            try:
                generate_thumbnail(source_path, out_path, at=ts)
                candidates.append((score, event, out_path))
            except Exception as e:
                log.warning(f"failed to extract candidate at t={ts:.1f}s: {e}")

        if not candidates:
            log.warning("thumbnail auto: all candidate extractions failed, mid-video fallback")
            return self._mid_video_fallback(source_path, output_dir, source_id)

        # Pick the best (highest score among successfully extracted)
        best_score, best_event, best_path = candidates[0]
        log.info(
            f"thumbnail auto: selected frame at t={(best_event.start_time + best_event.end_time) / 2:.1f}s "
            f"(event={best_event.event_type}, score={best_score:.3f}, "
            f"interesting={best_event.interesting_score:.2f}, "
            f"confidence={best_event.visual_confidence:.2f})"
        )
        return ThumbnailResult(
            image_path=best_path,
            source_event_id=best_event.id,
            source_timestamp=(best_event.start_time + best_event.end_time) / 2.0,
            selection_reason="semantic_event" if best_score > 0.3 else "interesting_fallback",
            gameplay_source_path=source_path,
        )

    def _mid_video_fallback(
        self, source_path: str, output_dir: Path, source_id: int
    ) -> Optional[ThumbnailResult]:
        """Fallback: extract a frame from the middle of the gameplay video."""
        try:
            info = probe(source_path)
            mid = (info.duration or 2.0) / 2.0
        except Exception:
            mid = 1.0
        out_path = output_dir / "thumb_fallback.jpg"
        try:
            generate_thumbnail(source_path, out_path, at=mid)
        except Exception as e:
            log.error(f"mid-video fallback thumbnail failed: {e}")
            return None
        return ThumbnailResult(
            image_path=out_path,
            source_timestamp=mid,
            selection_reason="mid_video_fallback",
            gameplay_source_path=source_path,
        )

    def _score_event(self, event, topic_keywords: set[str], source_id: int) -> float:
        """Score an event for thumbnail relevance.

        score = semantic_relevance * 0.40
              + interesting_score * 0.30
              + visual_confidence * 0.20
              + position_bonus * 0.10
        """
        # Semantic relevance: keyword overlap between topic and event description/tags
        event_text = " ".join([
            event.description or "",
            " ".join(event.tags or []),
            " ".join(event.characters or []),
            " ".join(event.actions or []),
            event.location or "",
        ]).lower()
        event_words = set(re.findall(r"\w+", event_text))

        if topic_keywords and event_words:
            overlap = len(topic_keywords & event_words) / max(len(topic_keywords), 1)
        else:
            overlap = 0.0

        # Position bonus: prefer events in the middle third of the video
        # (avoid opening menus and ending screens)
        # We don't have the total duration here, so we use a mild bonus
        # based on event start_time (prefer > 10s to skip intros)
        position_bonus = 0.5 if event.start_time > 10.0 else 0.0

        score = (
            overlap * 0.40
            + event.interesting_score * 0.30
            + event.visual_confidence * 0.20
            + position_bonus * 0.10
        )
        return score

    def _extract_keywords(self, text: str) -> set[str]:
        """Extract meaningful keywords from a topic/title string.

        Filters out common Portuguese/English stop words and short tokens.
        """
        if not text:
            return set()
        stop_words = {
            # Portuguese
            "o", "a", "os", "as", "de", "do", "da", "dos", "das", "e", "ou",
            "em", "no", "na", "nos", "nas", "por", "para", "com", "sem",
            "que", "se", "como", "mais", "menos", "muito", "pouco",
            "um", "uma", "uns", "umas", "ao", "aos", "pelo", "pela",
            # English
            "the", "a", "an", "in", "on", "at", "of", "to", "for",
            "and", "or", "with", "without", "that", "this", "is", "are",
            "was", "were", "be", "been", "being", "have", "has", "had",
        }
        words = set(re.findall(r"\w+", text.lower()))
        return {w for w in words if len(w) > 2 and w not in stop_words}
