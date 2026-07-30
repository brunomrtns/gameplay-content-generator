"""Filename parser — deterministic extraction of game/capture/timestamp from
recording filenames.

Common patterns (OBS / emulators):
    Bully_2026-07-26_14-32-11.mp4
    Yuzu_2026-07-26_15-07-43.mp4
    OBS_2026-07-26_16-48-33.mp4
    Crash CTR_2026-07-26_17-12-51.mp4
    2026-07-26_18-00-00_Bully.mp4
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# Known capture sources / emulators / recorders that are NOT game names.
# These are platform/tool identifiers.
CAPTURE_SOURCES = {
    "obs", "obs studio",
    "yuzu", "ryujinx",  # Switch emulators
    "ppsspp",  # PSP
    "dolphin",  # GC/Wii
    "pcsx2",  # PS2
    "rpcs3",  # PS3
    "xemu",  # Xbox
    "mesen",  # NES
    "snes9x",  # SNES
    "bgb",  # GB
    "mgba",  # GBA
    "desmume",  # DS
    "citra",  # 3DS
    "vba",  # GBA
    "retroarch",
    "steam",
    "shadowplay",
    "nvidia",
    "amd",
    "bandicam",
    "fraps",
    "action",
    "loilo",
}


@dataclass
class ParsedFilename:
    """Result of parsing a recording filename."""

    raw: str
    candidate_game: Optional[str] = None
    capture_source: Optional[str] = None
    recorded_at: Optional[datetime] = None
    is_capture_source_only: bool = False  # True if filename only has a tool name (e.g. "Yuzu_...")
    extra_tokens: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        """Heuristic confidence in the game identification (0-1)."""
        if self.candidate_game and not self.is_capture_source_only:
            return 0.9
        if self.candidate_game and self.is_capture_source_only:
            return 0.0  # We only found a tool, not a game
        return 0.0


# Regex patterns ordered by specificity
_PATTERNS = [
    # Game_YYYY-MM-DD_HH-MM-SS.ext  (OBS default style)
    re.compile(
        r"^(?P<game>.+?)_(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{2}-\d{2}-\d{2})",
        re.IGNORECASE,
    ),
    # YYYY-MM-DD_HH-MM-SS_Game.ext
    re.compile(
        r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{2}-\d{2}-\d{2})_(?P<game>.+)",
        re.IGNORECASE,
    ),
    # Game YYYY-MM-DD HH-MM-SS.ext  (spaces)
    re.compile(
        r"^(?P<game>.+?)\s+(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}-\d{2}-\d{2})",
        re.IGNORECASE,
    ),
]


def _parse_datetime(date_str: str, time_str: str) -> Optional[datetime]:
    try:
        return datetime.strptime(f"{date_str} {time_str.replace('-', ':')}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _clean_token(token: str) -> str:
    """Normalize a candidate token: strip extension, underscores, extra spaces."""
    # Remove file extension
    token = re.sub(r"\.(mp4|mkv|mov|avi|flv|webm|ts|m4v)$", "", token, flags=re.IGNORECASE)
    # Replace underscores/spaces collapse
    token = token.replace("_", " ").strip()
    token = re.sub(r"\s+", " ", token)
    return token


def parse_filename(filename: str) -> ParsedFilename:
    """Parse a recording filename deterministically.

    Returns ParsedFilename with candidate_game, capture_source, recorded_at.
    Never raises — returns empty ParsedFilename on failure.
    """
    raw = filename
    result = ParsedFilename(raw=raw)

    for pattern in _PATTERNS:
        m = pattern.match(filename)
        if not m:
            continue
        game_token = _clean_token(m.group("game"))
        date_str = m.group("date")
        time_str = m.group("time")
        result.recorded_at = _parse_datetime(date_str, time_str)

        # Is the "game" token actually a capture source?
        if game_token.lower() in CAPTURE_SOURCES:
            result.capture_source = game_token
            result.is_capture_source_only = True
            # No game candidate from this token
        else:
            result.candidate_game = game_token
            # Check if there's also a capture source embedded (rare)
        return result

    # Fallback: no pattern matched. Try to extract any date.
    date_m = re.search(r"(\d{4}-\d{2}-\d{2})[ _](\d{2}-\d{2}-\d{2})", filename)
    if date_m:
        result.recorded_at = _parse_datetime(date_m.group(1), date_m.group(2))

    # Fallback: use the filename stem as candidate game (low confidence)
    stem = _clean_token(filename)
    if stem:
        # If stem is just a capture source, don't treat as game
        if stem.lower() in CAPTURE_SOURCES:
            result.capture_source = stem
            result.is_capture_source_only = True
        else:
            # Keep as a weak candidate — caller decides if it's good enough
            result.candidate_game = stem
            result.extra_tokens.append("weak_candidate_from_stem")

    return result


def is_capture_source(token: str) -> bool:
    """Check if a token is a known capture source / emulator / recorder."""
    return token.lower().strip() in CAPTURE_SOURCES
