"""Video profile definitions for GPCG.

Defines 4 aspect ratio profiles (9:16, 16:9, 1:1, 4:5) and a builder for
custom profiles with subtitle overrides. These are registered into
video-generate's VideoProfileRegistry at runtime (in the subprocess script)
before calling process_video_request — we do NOT modify video-generate.

Profile naming convention: gpcg_<aspect> (e.g. gpcg_9_16, gpcg_1_1).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Default font path (DejaVu Sans Bold is available on most Linux systems)
DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Font family name → file path mapping (common fonts)
FONT_MAP = {
    "DejaVuSans-Bold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "DejaVuSans": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "DejaVuSansMono-Bold": "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "LiberationSans-Bold": "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "LiberationSans-Regular": "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
}


@dataclass
class SubtitleConfig:
    """Subtitle customization settings (overlays on top of profile defaults)."""

    font: str = ""  # font family name
    font_size: int = 0  # 0 = auto from profile
    color: str = ""  # e.g. "white", "yellow"
    outline_color: str = ""  # e.g. "black"
    position: str = ""  # "top", "middle", "bottom"
    case_transform: str = ""  # "upper", "lower", "none"
    # Advanced subtitle fields (MPT-inspired)
    box_enabled: Optional[bool] = None  # None = use profile default
    box_color: str = ""  # e.g. "black@0.7"
    box_padding: int = 0  # 0 = use profile default
    stroke_color: str = ""  # alias for outline_color (MPT naming)
    stroke_width: int = 0  # alias for outline_width (MPT naming)
    rounded_box: Optional[bool] = None  # rounded background pill effect

    def to_dict(self) -> dict:
        return {
            "font": self.font,
            "font_size": self.font_size,
            "color": self.color,
            "outline_color": self.outline_color,
            "position": self.position,
            "case_transform": self.case_transform,
            "box_enabled": self.box_enabled,
            "box_color": self.box_color,
            "box_padding": self.box_padding,
            "stroke_color": self.stroke_color,
            "stroke_width": self.stroke_width,
            "rounded_box": self.rounded_box,
        }


@dataclass
class TransitionConfig:
    """Transition customization settings (passed as top-level request_data overrides)."""

    transition_type: str = ""  # FFmpeg xfade name, "" = use profile default
    transition_duration: float = 0.0  # seconds, 0 = use profile default

    def to_dict(self) -> dict:
        d = {}
        if self.transition_type:
            d["transition_type"] = self.transition_type
        if self.transition_duration > 0:
            d["transition_duration"] = self.transition_duration
        return d


# ── Profile definitions ───────────────────────────────────────────────────
# Each profile is a dict that the subprocess script uses to build a
# VideoProfile object and register it. We use dicts (not VideoProfile
# instances) because this module runs in GPCG's process, not video-generate's.

PROFILES = {
    "9:16": {
        "name": "gpcg_9_16",
        "display_name": "Vertical (9:16) — YouTube Shorts/TikTok/Reels",
        "category": "reel",
        "width": 1080,
        "height": 1920,
        "aspect_ratio": "9:16",
        "orientation": "vertical",
        "base_scale_factor": 1.5,
        "fps": 30,
        "crf": 21,
        "preset": "medium",
        "transition_type": "smoothleft",
        "transition_duration": 0.5,
        "subtitle": {
            "font_size": 48,
            "font_file": DEFAULT_FONT,
            "outline_width": 3,
            "shadow_offset": 2,
            "max_chars_per_line": 32,
            "max_lines": 2,
            "position_y_ratio": 0.15,  # near bottom
            "text_align": "C",
            "box_enabled": False,
            "box_color": "black@0.7",
            "box_padding": 10,
            "font_color": "white",
            "outline_color": "black",
            "shadow_color": "black@0.5",
            "case_transform": "upper",
        },
        "safe_area": {
            "margin_top_ratio": 0.05,
            "margin_bottom_ratio": 0.20,
            "margin_left_ratio": 0.05,
            "margin_right_ratio": 0.05,
            "subtitle_safe_zone_top": 0.60,
            "subtitle_safe_zone_bottom": 0.95,
        },
        "provider_hints": {
            "gemini_composition": "vertical composition, 9:16 aspect ratio, portrait orientation",
            "pollinations_width": 720,
            "pollinations_height": 1280,
            "stock_orientation": "portrait",
            "veo_aspect_ratio": "9:16",
            "runway_ratio": "720:1280",
        },
    },
    "16:9": {
        "name": "gpcg_16_9",
        "display_name": "Horizontal (16:9) — YouTube tradicional",
        "category": "course",
        "width": 1920,
        "height": 1080,
        "aspect_ratio": "16:9",
        "orientation": "horizontal",
        "base_scale_factor": 1.5,
        "fps": 30,
        "crf": 20,
        "preset": "medium",
        "transition_type": "smoothleft",
        "transition_duration": 0.5,
        "subtitle": {
            "font_size": 52,
            "font_file": DEFAULT_FONT,
            "outline_width": 3,
            "shadow_offset": 2,
            "max_chars_per_line": 48,
            "max_lines": 3,
            "position_y_ratio": 0.08,  # near bottom
            "text_align": "C",
            "box_enabled": True,
            "box_color": "black@0.7",
            "box_padding": 16,
            "font_color": "white",
            "outline_color": "black",
            "shadow_color": "black@0.5",
            "case_transform": "none",
        },
        "safe_area": {
            "margin_top_ratio": 0.05,
            "margin_bottom_ratio": 0.15,
            "margin_left_ratio": 0.05,
            "margin_right_ratio": 0.05,
            "subtitle_safe_zone_top": 0.70,
            "subtitle_safe_zone_bottom": 0.95,
        },
        "provider_hints": {
            "gemini_composition": "horizontal composition, 16:9 aspect ratio, landscape orientation",
            "pollinations_width": 1280,
            "pollinations_height": 720,
            "stock_orientation": "landscape",
            "veo_aspect_ratio": "16:9",
            "runway_ratio": "1280:720",
        },
    },
    "1:1": {
        "name": "gpcg_1_1",
        "display_name": "Quadrado (1:1) — Instagram feed",
        "category": "reel",
        "width": 1080,
        "height": 1080,
        "aspect_ratio": "1:1",
        "orientation": "vertical",  # height >= width, passes validation
        "base_scale_factor": 1.5,
        "fps": 30,
        "crf": 21,
        "preset": "medium",
        "transition_type": "smoothleft",
        "transition_duration": 0.5,
        "subtitle": {
            "font_size": 44,
            "font_file": DEFAULT_FONT,
            "outline_width": 3,
            "shadow_offset": 2,
            "max_chars_per_line": 36,
            "max_lines": 2,
            "position_y_ratio": 0.15,
            "text_align": "C",
            "box_enabled": False,
            "box_color": "black@0.7",
            "box_padding": 10,
            "font_color": "white",
            "outline_color": "black",
            "shadow_color": "black@0.5",
            "case_transform": "upper",
        },
        "safe_area": {
            "margin_top_ratio": 0.05,
            "margin_bottom_ratio": 0.20,
            "margin_left_ratio": 0.05,
            "margin_right_ratio": 0.05,
            "subtitle_safe_zone_top": 0.60,
            "subtitle_safe_zone_bottom": 0.95,
        },
        "provider_hints": {
            "gemini_composition": "square composition, 1:1 aspect ratio",
            "pollinations_width": 1024,
            "pollinations_height": 1024,
            "stock_orientation": "square",
            "veo_aspect_ratio": "1:1",
            "runway_ratio": "1024:1024",
        },
    },
    "4:5": {
        "name": "gpcg_4_5",
        "display_name": "Retrato (4:5) — Instagram Reels feed",
        "category": "reel",
        "width": 1080,
        "height": 1350,
        "aspect_ratio": "4:5",
        "orientation": "vertical",
        "base_scale_factor": 1.5,
        "fps": 30,
        "crf": 21,
        "preset": "medium",
        "transition_type": "smoothleft",
        "transition_duration": 0.5,
        "subtitle": {
            "font_size": 46,
            "font_file": DEFAULT_FONT,
            "outline_width": 3,
            "shadow_offset": 2,
            "max_chars_per_line": 34,
            "max_lines": 2,
            "position_y_ratio": 0.15,
            "text_align": "C",
            "box_enabled": False,
            "box_color": "black@0.7",
            "box_padding": 10,
            "font_color": "white",
            "outline_color": "black",
            "shadow_color": "black@0.5",
            "case_transform": "upper",
        },
        "safe_area": {
            "margin_top_ratio": 0.05,
            "margin_bottom_ratio": 0.20,
            "margin_left_ratio": 0.05,
            "margin_right_ratio": 0.05,
            "subtitle_safe_zone_top": 0.60,
            "subtitle_safe_zone_bottom": 0.95,
        },
        "provider_hints": {
            "gemini_composition": "portrait composition, 4:5 aspect ratio",
            "pollinations_width": 864,
            "pollinations_height": 1080,
            "stock_orientation": "portrait",
            "veo_aspect_ratio": "4:5",
            "runway_ratio": "864:1080",
        },
    },
}


def get_profile_dict(format: str, subtitle: Optional[SubtitleConfig] = None) -> dict:
    """Get a profile dict for the given format, with optional subtitle overrides.

    Returns a copy of the profile dict with subtitle settings overridden
    by the SubtitleConfig (where non-empty/non-zero values are provided).
    """
    fmt = format if format in PROFILES else "9:16"
    profile = {k: (v.copy() if isinstance(v, dict) else v) for k, v in PROFILES[fmt].items()}

    if subtitle:
        sub = profile["subtitle"].copy()
        if subtitle.font:
            sub["font_file"] = FONT_MAP.get(subtitle.font, DEFAULT_FONT)
        if subtitle.font_size > 0:
            sub["font_size"] = subtitle.font_size
        if subtitle.color:
            sub["font_color"] = subtitle.color
        if subtitle.outline_color:
            sub["outline_color"] = subtitle.outline_color
        if subtitle.case_transform:
            sub["case_transform"] = subtitle.case_transform
        if subtitle.position:
            # position_y_ratio: 0.0 = bottom, 0.5 = middle, 0.15 = near bottom (default)
            # "top" → 0.85 (near top), "middle" → 0.45, "bottom" → 0.15
            pos_map = {"top": 0.85, "middle": 0.45, "bottom": 0.15}
            sub["position_y_ratio"] = pos_map.get(subtitle.position, sub["position_y_ratio"])
        # Advanced subtitle overrides (MPT-inspired)
        if subtitle.box_enabled is not None:
            sub["box_enabled"] = subtitle.box_enabled
        if subtitle.box_color:
            sub["box_color"] = subtitle.box_color
        if subtitle.box_padding > 0:
            sub["box_padding"] = subtitle.box_padding
        if subtitle.stroke_color:
            sub["stroke_color"] = subtitle.stroke_color
        if subtitle.stroke_width > 0:
            sub["stroke_width"] = subtitle.stroke_width
        if subtitle.rounded_box is not None:
            sub["rounded_box"] = subtitle.rounded_box
        profile["subtitle"] = sub

    return profile


def get_resolution(format: str) -> tuple[int, int]:
    """Get (width, height) for a format."""
    fmt = format if format in PROFILES else "9:16"
    p = PROFILES[fmt]
    return p["width"], p["height"]


def get_profile_name(format: str) -> str:
    """Get the video-generate profile name for a format."""
    fmt = format if format in PROFILES else "9:16"
    return PROFILES[fmt]["name"]


def build_profile_registration_code(profile_dict: dict) -> str:
    """Build Python code that registers a custom profile in video-generate's registry.

    This code is injected into the subprocess script before calling
    process_video_request. It creates a VideoProfile object from the dict
    and registers it in the VideoProfileRegistry.
    """
    import json
    pd = json.dumps(profile_dict, ensure_ascii=False)
    return textwrap_dedent(f"""\
        import json as _json
        from src.profiles.video_profile import VideoProfile, SubtitleProfile, SafeAreaProfile, ProviderHints
        from src.profiles.profile_registry import VideoProfileRegistry

        _pd = _json.loads({pd!r})
        _sub = _pd["subtitle"]
        _safe = _pd["safe_area"]
        _prov = _pd["provider_hints"]
        _profile = VideoProfile(
            name=_pd["name"],
            display_name=_pd["display_name"],
            category=_pd["category"],
            width=_pd["width"],
            height=_pd["height"],
            aspect_ratio=_pd["aspect_ratio"],
            orientation=_pd["orientation"],
            base_scale_factor=_pd["base_scale_factor"],
            fps=_pd["fps"],
            crf=_pd["crf"],
            preset=_pd["preset"],
            transition_type=_pd.get("transition_type", "smoothleft"),
            transition_duration=_pd.get("transition_duration", 0.5),
            subtitle=SubtitleProfile(**_sub),
            safe_area=SafeAreaProfile(**_safe),
            provider_hints=ProviderHints(**_prov),
        )
        # Override the orientation validation for square profiles
        # (video-generate only allows "horizontal" or "vertical")
        VideoProfileRegistry.register(_profile)
    """)


def textwrap_dedent(s: str) -> str:
    import textwrap
    return textwrap.dedent(s)
