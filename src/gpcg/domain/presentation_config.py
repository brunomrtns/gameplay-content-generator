"""Presentation Layer configuration — thumbnail + visual opening.

This is an OPTIONAL layer. When ``enabled=False`` (the default), the GPCG
pipeline behaves exactly as it does today. When enabled, the pipeline adds
a visual presentation layer:

  1. THUMBNAIL / CAPA — a cover image for the video, optionally with the
     title overlaid. Can be auto-selected from gameplay events, imported
     by the user, or a fixed image per automation.

  2. OPENING / INTRO — a short visual intro (2-3s) at the start of the
     video showing a strong image + the title in large text.

The config is stored as a JSON blob inside ``Automation.config["presentation"]``
and snapshotted into ``job.artifacts["config_snapshot"]["presentation"]``.
No DB schema changes are required — everything lives in the existing JSON
columns.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class PresentationConfig:
    """Configuration for the Presentation Layer (thumbnail + opening).

    All defaults preserve the current GPCG behavior (layer disabled).
    """

    # ── Master toggle ──────────────────────────────────────────────────────
    # False = Presentation Layer completely disabled (current behavior).
    enabled: bool = False

    # ── Thumbnail / Capa ───────────────────────────────────────────────────
    thumbnail_enabled: bool = True
    # "auto" = select frame from gameplay events
    # "imported" = user-provided image (per-job)
    # "fixed" = fixed image for all videos of this automation
    thumbnail_mode: str = "auto"
    # storage_key for imported/fixed modes (resolved by storage layer)
    thumbnail_image_path: str = ""
    # Text overlay on the thumbnail
    thumbnail_text_enabled: bool = True
    # "title" = derive from video title, "custom" = use thumbnail_text_custom
    thumbnail_text_source: str = "title"
    thumbnail_text_custom: str = ""
    thumbnail_text_position: str = "bottom"  # "top" | "middle" | "bottom"
    thumbnail_text_color: str = "white"
    thumbnail_text_outline: str = "black"
    thumbnail_text_size: str = "large"  # "medium" | "large" | "xlarge"

    # ── Opening / Introdução Visual ────────────────────────────────────────
    opening_enabled: bool = True
    opening_duration: float = 2.5  # seconds
    # "same_as_thumbnail" = reuse the thumbnail image
    # "auto" | "imported" | "fixed" = independent selection
    opening_image_mode: str = "same_as_thumbnail"
    opening_image_path: str = ""
    opening_text_enabled: bool = True
    # "title" = video title, "hook" = first line of script, "custom" = custom
    opening_text_source: str = "title"
    opening_text_custom: str = ""
    opening_text_position: str = "middle"
    opening_text_color: str = "white"
    opening_text_outline: str = "black"
    opening_text_size: str = "xlarge"

    # ── Auto-selection parameters ──────────────────────────────────────────
    # How many candidate frames to extract and score
    auto_candidate_count: int = 5
    # Minimum thresholds for event filtering (0-1)
    auto_min_interesting: float = 0.4
    auto_min_confidence: float = 0.5

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "PresentationConfig":
        """Deserialize from a dict. Missing keys get defaults.

        An empty/None dict returns a disabled config (backward compat).
        """
        if not d:
            return cls()
        # Only pick known fields — ignore unknown keys for forward compat
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)
