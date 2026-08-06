"""Search Templates — editorial search strategies as first-class components.

A Search Template is NOT just a list of keywords. It represents an editorial
intention: "find curiosities", "find lore", "find news". Each template bundles:

- a name (the editorial strategy identity)
- the corresponding KnowledgeItemType
- a tuple of keywords that expand a game name into editorial queries
- a description (for UI/auditability)
- a decay_category that drives the lifecycle ("fast" for news, "evergreen" for lore)

Templates are immutable and registered in a module-level dict. To add a new
template, register it in SEARCH_TEMPLATES. To customize at channel level,
users add keywords via ChannelProfile.editorial_keywords — these are MERGED
with the template keywords at query expansion time, never replace them.

See docs/EDITORIAL_INTELLIGENCE_V2_PROPOSAL.md §7.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchTemplate:
    """An editorial search strategy.

    Attributes:
        name: stable identifier (e.g. "curiosity", "news", "lore")
        item_type: the KnowledgeItemType value this template produces
        keywords: editorial keywords combined with game names to build queries
        description: human-readable description (UI / audit)
        decay_category: lifecycle hint — "fast" (news), "medium" (facts),
            "evergreen" (curiosities, lore). Drives LifecycleManager.
    """

    name: str
    item_type: str
    keywords: tuple[str, ...]
    description: str
    decay_category: str


# Decay categories — referenced by lifecycle_manager.py
DECAY_FAST = "fast"
DECAY_MEDIUM = "medium"
DECAY_EVERGREEN = "evergreen"


SEARCH_TEMPLATES: dict[str, SearchTemplate] = {
    "curiosity": SearchTemplate(
        name="curiosity",
        item_type="curiosity",
        keywords=(
            "hidden",
            "secrets",
            "beta",
            "unused",
            "cancelled",
            "developer",
            "interview",
            "easter egg",
            "glitch",
            "mystery",
            "cut content",
            "unused content",
        ),
        description="Curiosidades e segredos ocultos dos jogos",
        decay_category=DECAY_EVERGREEN,
    ),
    "news": SearchTemplate(
        name="news",
        item_type="news",
        keywords=(
            "update",
            "patch",
            "release",
            "announcement",
            "trailer",
            "delay",
            "review",
            "dlc",
            "expansion",
            "remaster",
        ),
        description="Notícias e atualizações atuais",
        decay_category=DECAY_FAST,
    ),
    "lore": SearchTemplate(
        name="lore",
        item_type="lore",
        keywords=(
            "story",
            "lore",
            "history",
            "behind the scenes",
            "development",
            "documentary",
            "timeline",
            "explained",
            "making of",
            "design",
        ),
        description="História e narrativa dos jogos",
        decay_category=DECAY_EVERGREEN,
    ),
    "nostalgia": SearchTemplate(
        name="nostalgia",
        item_type="curiosity",
        keywords=(
            "anniversary",
            "retrospective",
            "evolution",
            "history",
            "classic",
            "retro",
            "nostalgia",
            "then vs now",
        ),
        description="Nostalgia e retrospectiva de jogos clássicos",
        decay_category=DECAY_EVERGREEN,
    ),
    "fact": SearchTemplate(
        name="fact",
        item_type="fact",
        keywords=(
            "trivia",
            "fact",
            "did you know",
            "detail",
            "analysis",
            "mechanics",
            "breakdown",
        ),
        description="Fatos e análises detalhadas",
        decay_category=DECAY_MEDIUM,
    ),
}


def get_template(name: str) -> SearchTemplate | None:
    """Return the template with the given name, or None."""
    return SEARCH_TEMPLATES.get(name)


def list_template_names() -> list[str]:
    """Return all registered template names."""
    return list(SEARCH_TEMPLATES.keys())


def merge_keywords(template: SearchTemplate, custom_keywords: list[str]) -> list[str]:
    """Merge template keywords with channel-custom keywords.

    Custom keywords are appended (preserving order) if not already present.
    The template keywords always come first — they are the curated baseline.
    """
    merged = list(template.keywords)
    seen = set(template.keywords)
    for kw in custom_keywords:
        kl = kw.lower().strip()
        if kl and kl not in seen:
            merged.append(kw)
            seen.add(kl)
    return merged
