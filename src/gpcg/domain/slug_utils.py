"""Slug utilities — canonical slug generation for Game registry (V2).

Slug rules:
- Lowercase
- Remove accents (NFD normalization, drop combining marks)
- Replace non-alphanumeric with hyphens
- Collapse consecutive hyphens
- Strip leading/trailing hyphens
- Never empty (falls back to "game")

Examples:
  "Resident Evil 4" → "resident-evil-4"
  "Bully: Scholarship Edition" → "bully-scholarship-edition"
  "Super Mario 64" → "super-mario-64"
"""

from __future__ import annotations

import re
import unicodedata


def slugify(text: str) -> str:
    """Convert a game name into a canonical slug.

    >>> slugify("Resident Evil 4")
    'resident-evil-4'
    >>> slugify("Bully: Scholarship Edition")
    'bully-scholarship-edition'
    >>> slugify("Super Mario 64")
    'super-mario-64'
    >>> slugify("")
    'game'
    """
    if not text:
        return "game"

    # Normalize: NFD decomposes accented chars, then drop combining marks
    normalized = unicodedata.normalize("NFD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")

    # Lowercase
    slug = ascii_text.lower()

    # Replace any non-alphanumeric with hyphens
    slug = re.sub(r"[^a-z0-9]+", "-", slug)

    # Collapse consecutive hyphens
    slug = re.sub(r"-+", "-", slug)

    # Strip leading/trailing hyphens
    slug = slug.strip("-")

    # Never empty
    return slug if slug else "game"


def normalize_name(name: str) -> str:
    """Normalize a game name for dedup matching.

    - Lowercase
    - Remove accents (NFD)
    - Strip whitespace
    - Remove common platform suffixes (PS2, PS3, PS4, PS5, PC, Xbox, etc.)

    Does NOT remove subtitles ("Scholarship Edition", "Remake", "Director's Cut")
    — those are distinct aliases, not platform suffixes.
    """
    if not name:
        return ""

    # NFD normalize + remove combining marks
    normalized = unicodedata.normalize("NFD", name)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")

    # Lowercase + strip
    result = ascii_text.lower().strip()

    # Remove common platform suffixes (only as suffix, not in the middle)
    platform_suffixes = [
        "ps2", "ps3", "ps4", "ps5", "psn", "psp", "psvita",
        "pc", "steam", "gog",
        "xbox", "xbox 360", "xbox one", "xbox series x", "xbox series s",
        "switch", "wii", "wii u", "3ds", "nds", "n64", "gamecube",
        "snes", "nes", "genesis", "dreamcast", "saturn",
        "android", "ios", "mobile",
        "mac", "linux",
    ]
    for suffix in sorted(platform_suffixes, key=len, reverse=True):
        if result.endswith(" " + suffix):
            result = result[: -(len(suffix) + 1)].strip()
            break
        if result.endswith("(" + suffix + ")"):
            result = result[: -(len(suffix) + 2)].strip()
            break

    return result.strip()
