"""Kids media analyzer — semantic understanding of Kids video assets.

Reuses the SAME pipeline as GameplayAnalyzer (VLM + ASR + merge +
interesting score). The only difference is the output destination:
GameplayAnalyzer produces events for GameplaySource (Games domain),
KidsMediaAnalyzer produces events for StoryAsset (Kids domain).

The VLM prompts in GameplayAnalyzer are generic enough ("describe what
you see in this video segment") that they work for any video content —
gameplay, educational animation, stock footage, etc. The event types
(COMBAT, CHASE, etc.) are gameplay-specific, but the description, tags,
transcript, and confidence scores are domain-agnostic and useful for
semantic selection in Kids too.

For Kids, the event_type field is remapped to a Kids-friendly taxonomy
(VISUAL_ACTION, NARRATION, ANIMATION, etc.) but the underlying analysis
is identical.

Usage (in the worker during a kids_asset_process job):

    analyzer = KidsMediaAnalyzer()
    timeline = analyzer.analyze(local_video_path, asset_id=asset_id)
    events = kids_media_events_from_timeline(timeline, asset_id)
    # POST /api/kids/assets/{asset_id}/mapping-result with events
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from gpcg.application.gameplay_analyzer import GameplayAnalyzer
from gpcg.domain.gameplay_events import EventTimeline, GameplayEventRecord
from gpcg.logging import get_logger

log = get_logger(__name__)


# ── Event type remapping: Games → Kids ──────────────────────────────────────

# GameplayAnalyzer produces gameplay-specific event types (COMBAT, CHASE,
# DIALOGUE, etc.). For Kids content, we remap these to a Kids-friendly
# taxonomy. Unknown/unmatched types are kept as-is — the description and
# tags are more important than the type for Kids selection.
_EVENT_TYPE_REMAP: dict[str, str] = {
    "COMBAT": "VISUAL_ACTION",
    "CHASE": "VISUAL_ACTION",
    "VEHICLE": "VISUAL_ACTION",
    "DIALOGUE": "NARRATION",
    "CUTSCENE": "ANIMATION",
    "EXPLORATION": "VISUAL_ACTION",
    "INTERACTION": "VISUAL_ACTION",
    "TRAVEL": "VISUAL_ACTION",
    "MENU": "TEXT_OVERLAY",
    "LOADING": "TRANSITION",
    "IDLE": "STATIC_IMAGE",
    "UNKNOWN": "UNKNOWN",
}


def _remap_event_type(gameplay_type: str) -> str:
    """Remap a gameplay event type to a Kids-friendly type.

    Preserves POSSIBLE_ prefix if present.
    """
    if gameplay_type.startswith("POSSIBLE_"):
        base = gameplay_type[len("POSSIBLE_"):]
        remapped = _EVENT_TYPE_REMAP.get(base, gameplay_type)
        return f"POSSIBLE_{remapped}"
    return _EVENT_TYPE_REMAP.get(gameplay_type, gameplay_type)


class KidsMediaAnalyzer:
    """Analyzes Kids video assets using the same VLM + ASR pipeline as Games.

    This is a thin wrapper around GameplayAnalyzer. The analysis pipeline
    (coarse → refine → ASR → merge → interesting score) is identical.
    The only difference is how the results are stored (KidsMediaEvent
    instead of GameplayEvent) and the event_type taxonomy remapping.
    """

    def __init__(
        self,
        analyzer: Optional[GameplayAnalyzer] = None,
        camera_type: str = "unknown",
    ) -> None:
        # Kids content is typically not gameplay — no player detection,
        # no cascaded pipeline. Use "unknown" camera_type (full-frame VLM).
        self.analyzer = analyzer or GameplayAnalyzer(camera_type=camera_type)

    def analyze(
        self,
        video_path: str | Path,
        asset_id: int = 0,
        *,
        enable_asr: Optional[bool] = None,
        enable_interesting_score: Optional[bool] = None,
        progress_callback: Optional[callable] = None,
    ) -> EventTimeline:
        """Run the full analysis pipeline on a Kids video asset.

        Returns an EventTimeline (same data structure as GameplayAnalyzer).
        The caller (worker) converts the events to KidsMediaEvent records
        and syncs them to the VPS.
        """
        return self.analyzer.analyze(
            video_path,
            source_id=asset_id,
            enable_asr=enable_asr,
            enable_interesting_score=enable_interesting_score,
            camera_type="unknown",  # Kids: always full-frame, no cascaded
            progress_callback=progress_callback,
        )


def kids_media_events_from_timeline(
    timeline: EventTimeline,
    asset_id: int,
) -> list[dict]:
    """Convert an EventTimeline to KidsMediaEvent dicts for VPS sync.

    This is the Kids equivalent of the gameplay mapping-result endpoint
    payload. Each event dict has the same structure as KidsMediaEvent
    columns, with event_type remapped to the Kids taxonomy.
    """
    events: list[dict] = []
    for record in timeline.events:
        events.append({
            "asset_id": asset_id,
            "start_time": record.start_time,
            "end_time": record.end_time,
            "event_type": _remap_event_type(record.event_type),
            "description": record.description,
            "characters": record.characters or [],
            "location": record.location or "",
            "actions": record.actions or [],
            "tags": record.tags or [],
            "transcript": record.transcript or "",
            "visual_confidence": record.visual_confidence,
            "interesting_score": record.interesting_score,
            "analysis_version": timeline.analysis_version,
            "metadata_json": {
                "vision_model": timeline.vision_model,
                "asr_model": timeline.asr_model,
            },
        })
    return events
