"""Thumbnail selector — chooses the best frame for the video cover.

Uses the GameplayEvent index (produced during mapping) to find visually
and semantically relevant frames. Three modes:

  - "auto":     query events, score by relevance to the video topic,
                extract candidate frames, analyze visual quality,
                pick the best.
  - "imported": use a user-provided image (per-job).
  - "fixed":    use a fixed image for all videos of this automation.

The selector does NOT compose text onto the image — that's done by the
PresentationService after selection. It only finds the base image.

V2 improvements (fixes same-frame-for-different-videos bug):
  - Semantic scoring via embeddings (cosine similarity) instead of keyword
    overlap. Falls back to keywords if embeddings unavailable.
  - Visual quality analysis of extracted frames (brightness, saturation,
    contrast). Penalizes dark/washed-out frames.
  - Event type weighting: action events (COMBAT, CHASE, VEHICLE) preferred
    over passive events (MENU, LOADING, CUTSCENE).
  - Transcript included in semantic text.
  - Diversity: random tiebreaker among top candidates to avoid the same
    frame being selected every time for the same gameplay source.
"""

from __future__ import annotations

import random
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from gpcg.domain.presentation_config import PresentationConfig
from gpcg.infrastructure.media import generate_thumbnail, probe
from gpcg.logging import get_logger

log = get_logger(__name__)


# ── Event type weights ───────────────────────────────────────────────────────
# Action/dynamic events are preferred for thumbnails. Passive/boring events
# are penalized. This is applied as a multiplier on the final score.
_EVENT_TYPE_WEIGHTS: dict[str, float] = {
    # Action — high weight
    "COMBAT": 1.0,
    "CHASE": 1.0,
    "VEHICLE": 0.95,
    "EXPLOSION": 0.95,
    "STUNT": 0.95,
    "SHOOTING": 0.9,
    "RACING": 0.9,
    # Dynamic — moderate weight
    "EXPLORATION": 0.7,
    "CUTSCENE": 0.65,
    "DIALOGUE": 0.6,
    "INTERACTION": 0.6,
    # Passive — low weight (avoid for thumbnails)
    "MENU": 0.3,
    "LOADING": 0.2,
    "UNKNOWN": 0.5,
}

# Default weight for event types not in the map
_DEFAULT_EVENT_TYPE_WEIGHT = 0.6

# Possible_ prefix events get a small penalty (ambiguous)
_POSSIBLE_PENALTY = 0.85


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
    # V2: visual quality metrics of the selected frame
    visual_brightness: float = 0.0  # 0-1 (mean luminance)
    visual_saturation: float = 0.0  # 0-1 (mean saturation)
    visual_score: float = 0.0  # combined visual quality 0-1


@dataclass
class _FrameQuality:
    """Visual quality metrics for an extracted frame."""
    brightness: float = 0.0  # 0-1 (mean luminance, higher = brighter)
    saturation: float = 0.0  # 0-1 (mean saturation, higher = more colorful)
    contrast: float = 0.0   # 0-1 (std dev of luminance, higher = more contrast)
    score: float = 0.0      # combined 0-1


class ThumbnailSelector:
    """Selects the best image for the thumbnail/opening."""

    def __init__(self) -> None:
        # V2: track recently selected timestamps per source to avoid repeats
        # Keyed by (source_id, rounded_timestamp) → selection count
        self._recent_selections: dict[tuple[int, float], int] = {}

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
        """Auto-select the best frame using the GameplayEvent index.

        V2 scoring formula:
          final_score = semantic_relevance * 0.35
                      + visual_quality * 0.25
                      + interesting_score * 0.15
                      + event_type_weight * 0.15
                      + position_bonus * 0.10

        Semantic relevance uses embeddings (cosine similarity) when available,
        falling back to keyword overlap.

        Visual quality analyzes the extracted frame for brightness, saturation,
        and contrast — penalizing dark/washed-out frames.

        Diversity: among the top candidates within 10% of the best score,
        a random one is picked to avoid the same frame always winning.
        """
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

        # V2: Try semantic scoring via embeddings first
        semantic_scores = self._semantic_scores_via_embeddings(
            session, events, topic
        )

        # Fallback to keyword matching if no embeddings available
        if semantic_scores is None:
            topic_keywords = self._extract_keywords(topic)
            semantic_scores = {}
            for event in events:
                semantic_scores[event.id] = self._keyword_score(event, topic_keywords)
            log.info("thumbnail auto: using keyword matching (no embeddings available)")
        else:
            log.info("thumbnail auto: using embedding-based semantic scoring")

        # Score each event (preliminary, without visual quality)
        scored = []
        for event in events:
            sem = semantic_scores.get(event.id, 0.0)
            etype_weight = self._event_type_weight(event.event_type)
            position_bonus = 0.5 if event.start_time > 10.0 else 0.0
            prelim_score = (
                sem * 0.35
                + event.interesting_score * 0.15
                + etype_weight * 0.15
                + position_bonus * 0.10
                # Visual quality (0.25) is added after frame extraction
            )
            scored.append((prelim_score, event))
        scored.sort(key=lambda x: x[0], reverse=True)

        # Extract top N candidates and analyze visual quality
        n = min(config.auto_candidate_count, len(scored))
        candidates = []
        for i in range(n):
            prelim_score, event = scored[i]
            ts = (event.start_time + event.end_time) / 2.0
            out_path = output_dir / f"thumb_candidate_{i:02d}.jpg"
            try:
                generate_thumbnail(source_path, out_path, at=ts)
            except Exception as e:
                log.warning(f"failed to extract candidate at t={ts:.1f}s: {e}")
                continue

            # V2: Analyze visual quality of the extracted frame
            quality = self._analyze_frame_quality(out_path)

            # V2: Diversity penalty — reduce score if this timestamp was
            # recently selected for the same source
            ts_key = (source_id, round(ts, 1))
            repeat_count = self._recent_selections.get(ts_key, 0)
            repeat_penalty = max(0.0, 1.0 - (repeat_count * 0.3))

            final_score = (
                prelim_score
                + quality.score * 0.25
            ) * repeat_penalty

            candidates.append((final_score, event, out_path, quality))

        if not candidates:
            log.warning("thumbnail auto: all candidate extractions failed, mid-video fallback")
            return self._mid_video_fallback(source_path, output_dir, source_id)

        # V2: Diversity — among candidates within 10% of the best score,
        # pick randomly to avoid always selecting the same frame
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_score = candidates[0][0]
        threshold = best_score * 0.9
        top_tier = [c for c in candidates if c[0] >= threshold]

        if len(top_tier) > 1:
            rng = random.Random(hash(topic) & 0xFFFFFFFF)
            chosen = rng.choice(top_tier)
            log.info(
                f"thumbnail auto: {len(top_tier)} candidates within 10% of best "
                f"({best_score:.3f}), random pick for diversity"
            )
        else:
            chosen = candidates[0]

        best_score, best_event, best_path, best_quality = chosen

        # Track this selection for diversity
        ts = (best_event.start_time + best_event.end_time) / 2.0
        ts_key = (source_id, round(ts, 1))
        self._recent_selections[ts_key] = self._recent_selections.get(ts_key, 0) + 1

        log.info(
            f"thumbnail auto: selected frame at t={ts:.1f}s "
            f"(event={best_event.event_type}, score={best_score:.3f}, "
            f"interesting={best_event.interesting_score:.2f}, "
            f"confidence={best_event.visual_confidence:.2f}, "
            f"brightness={best_quality.brightness:.2f}, "
            f"saturation={best_quality.saturation:.2f})"
        )
        return ThumbnailResult(
            image_path=best_path,
            source_event_id=best_event.id,
            source_timestamp=ts,
            selection_reason="semantic_event" if best_score > 0.3 else "interesting_fallback",
            gameplay_source_path=source_path,
            visual_brightness=best_quality.brightness,
            visual_saturation=best_quality.saturation,
            visual_score=best_quality.score,
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

    # ── V2: Semantic scoring via embeddings ──────────────────────────────────

    def _semantic_scores_via_embeddings(
        self,
        session: Session,
        events: list,
        topic: str,
    ) -> Optional[dict[int, float]]:
        """Score events using embedding cosine similarity.

        Returns a dict {event_id: similarity_score} or None if embeddings
        are not available (fallback to keyword matching).
        """
        from gpcg.domains.games.models import GameplayEventEmbedding
        from gpcg.application.embedding_service import (
            get_gameplay_event_embedding,
            cosine_similarity,
        )

        # Check if any events have embeddings
        event_ids = [e.id for e in events]
        if not event_ids:
            return None

        # Load existing embeddings
        event_embeddings: dict[int, list[float]] = {}
        for eid in event_ids:
            try:
                emb = get_gameplay_event_embedding(session, eid)
                if emb:
                    event_embeddings[eid] = emb
            except Exception:
                pass

        if not event_embeddings:
            return None

        # Generate embedding for the topic
        try:
            from gpcg.infrastructure.llm import LLMClient
            llm = LLMClient.from_env()
            topic_emb = llm.embed(topic)
        except Exception as e:
            log.warning(f"thumbnail auto: failed to embed topic: {e}, using keyword fallback")
            return None

        # Compute cosine similarity for events that have embeddings
        scores: dict[int, float] = {}
        for event in events:
            emb = event_embeddings.get(event.id)
            if emb and topic_emb:
                sim = cosine_similarity(topic_emb, emb)
                # Normalize to 0-1 (cosine similarity can be -1 to 1)
                scores[event.id] = max(0.0, (sim + 1.0) / 2.0)
            else:
                # No embedding for this event — assign 0 (will rely on other factors)
                scores[event.id] = 0.0

        coverage = len(event_embeddings) / len(events)
        log.info(
            f"thumbnail auto: embedding coverage {len(event_embeddings)}/{len(events)} "
            f"({coverage:.0%})"
        )

        # If coverage is very low (< 20%), the semantic scores are unreliable
        if coverage < 0.2:
            log.info("thumbnail auto: embedding coverage too low, using keyword fallback")
            return None

        return scores

    # ── V2: Visual quality analysis ──────────────────────────────────────────

    def _analyze_frame_quality(self, image_path: Path) -> _FrameQuality:
        """Analyze a JPG frame for visual quality (brightness, saturation, contrast).

        Uses FFmpeg's signalstats filter to extract metrics. If analysis fails,
        returns a neutral score (doesn't penalize or reward).
        """
        try:
            cmd = [
                "ffmpeg", "-y",
                "-i", str(image_path),
                "-vf", "signalstats,format=lum",
                "-f", "null",
                "-",
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10,
            )
            # Parse signalstats output from stderr
            # Looking for lines like:
            #   signalstats: YAVG=0.234 YDIF=0.012 ...
            stderr = result.stderr
            yavg = self._parse_signalstat(stderr, "YAVG")
            ydif = self._parse_signalstat(stderr, "YDIF")
            # Also try to get saturation from format conversion
            sat = self._parse_signalstat(stderr, "UAVG")
            # Normalize: YAVG is 0-1 (luminance), YDIF is temporal (not useful for single frame)
            # Use YAVG for brightness, and std dev via a second approach

            # If signalstats didn't give us good data, try PIL
            if yavg is None:
                return self._analyze_frame_pil(image_path)

            brightness = max(0.0, min(1.0, yavg / 255.0))

            # For saturation, if we have U/V averages, compute a rough estimate
            if sat is not None:
                saturation = max(0.0, min(1.0, abs(sat - 128) / 128.0))
            else:
                saturation = 0.5  # neutral

            # Contrast: use YDIF as a proxy (spatial detail)
            contrast = max(0.0, min(1.0, ydif / 50.0)) if ydif else 0.5

            # Penalize very dark frames (brightness < 0.15)
            brightness_penalty = 1.0
            if brightness < 0.15:
                brightness_penalty = 0.3
            elif brightness < 0.25:
                brightness_penalty = 0.6
            elif brightness < 0.35:
                brightness_penalty = 0.8

            # Combined visual score
            score = (
                brightness * 0.40 * brightness_penalty
                + saturation * 0.35
                + contrast * 0.25
            )

            return _FrameQuality(
                brightness=brightness,
                saturation=saturation,
                contrast=contrast,
                score=score,
            )

        except Exception as e:
            log.debug(f"frame quality analysis failed: {e}")
            return _FrameQuality(brightness=0.5, saturation=0.5, contrast=0.5, score=0.5)

    def _analyze_frame_pil(self, image_path: Path) -> _FrameQuality:
        """Fallback: analyze frame using PIL (if FFmpeg signalstats fails)."""
        try:
            from PIL import Image
            import statistics

            img = Image.open(image_path).convert("RGB")
            # Sample pixels (don't process the full image)
            img_small = img.resize((50, 50))
            pixels = list(img_small.get_flattened_data())

            # Brightness: mean luminance (0-255 → 0-1)
            luminances = [(0.299 * r + 0.587 * g + 0.114 * b) / 255.0 for r, g, b in pixels]
            brightness = statistics.mean(luminances)

            # Saturation: mean distance from gray (0-1)
            saturations = []
            for r, g, b in pixels:
                gray = (r + g + b) / 3
                dist = max(abs(r - gray), abs(g - gray), abs(b - gray)) / 128.0
                saturations.append(dist)
            saturation = statistics.mean(saturations)

            # Contrast: std dev of luminance (0-1)
            contrast = statistics.stdev(luminances) if len(luminances) > 1 else 0.0

            # Penalize very dark frames
            brightness_penalty = 1.0
            if brightness < 0.15:
                brightness_penalty = 0.3
            elif brightness < 0.25:
                brightness_penalty = 0.6
            elif brightness < 0.35:
                brightness_penalty = 0.8

            score = (
                brightness * 0.40 * brightness_penalty
                + saturation * 0.35
                + min(1.0, contrast * 2.0) * 0.25
            )

            return _FrameQuality(
                brightness=brightness,
                saturation=saturation,
                contrast=min(1.0, contrast * 2.0),
                score=score,
            )
        except Exception as e:
            log.debug(f"PIL frame analysis failed: {e}")
            return _FrameQuality(brightness=0.5, saturation=0.5, contrast=0.5, score=0.5)

    def _parse_signalstat(self, stderr: str, stat: str) -> Optional[float]:
        """Parse a signalstats value from FFmpeg stderr output."""
        # Look for pattern like "YAVG=123.45" or "YAVG= 123"
        match = re.search(rf"{stat}=\s*([\d.]+)", stderr)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
        return None

    # ── V2: Event type weighting ─────────────────────────────────────────────

    def _event_type_weight(self, event_type: str) -> float:
        """Get the weight for an event type.

        Action events (COMBAT, CHASE, VEHICLE) get high weights.
        Passive events (MENU, LOADING) get low weights.
        POSSIBLE_ prefix events get a small penalty (ambiguous).
        """
        weight = _EVENT_TYPE_WEIGHTS.get(event_type, _DEFAULT_EVENT_TYPE_WEIGHT)
        if event_type.startswith("POSSIBLE_"):
            weight *= _POSSIBLE_PENALTY
        return weight

    # ── V2: Keyword scoring (fallback) ───────────────────────────────────────

    def _keyword_score(self, event, topic_keywords: set[str]) -> float:
        """Score an event by keyword overlap (fallback when no embeddings)."""
        # V2: include transcript in the text
        parts = []
        for attr in ("description", "location", "transcript"):
            val = getattr(event, attr, None)
            if isinstance(val, str):
                parts.append(val)
        for attr in ("tags", "characters", "actions"):
            val = getattr(event, attr, None)
            if isinstance(val, (list, tuple)):
                parts.append(" ".join(str(v) for v in val))
        event_text = " ".join(parts).lower()
        event_words = set(re.findall(r"\w+", event_text))

        if topic_keywords and event_words:
            overlap = len(topic_keywords & event_words) / max(len(topic_keywords), 1)
        else:
            overlap = 0.0

        return overlap

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
