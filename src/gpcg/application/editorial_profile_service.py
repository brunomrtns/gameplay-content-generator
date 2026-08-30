"""Editorial Profile Service — CRUD + presets for the V2 channel profile.

The Editorial Profile is the persisted identity of a channel. It contains
both user-configured fields (niche, tone, content_type_affinity, ...) and
learned fields (learned_preferences, production_history_summary) that are
populated by the feedback loop.

This service provides:
- preset application (Curiosidades, Notícias, Lore, Nostalgia, Educacional)
- structured-field updates (with validation)
- learned-field updates (called by the feedback loop, not by the user)
- serialization for the API

See docs/EDITORIAL_INTELLIGENCE_V2_PROPOSAL.md §4.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from gpcg.core.models import ChannelProfile
from gpcg.logging import get_logger

log = get_logger(__name__)


# ── Editorial Presets ────────────────────────────────────────────────────────
# Each preset populates the structured fields of the Editorial Profile.
# Free-text fields (niche, tone_of_voice, etc.) are also set to sensible
# defaults but can be freely customized by the user afterwards.

EDITORIAL_PRESETS: dict[str, dict[str, Any]] = {
    "curiosidades": {
        "niche": "Curiosidades e segredos de jogos",
        "target_audience": "Jogadores que amam descobrir detalhes ocultos",
        "tone_of_voice": "intrigante, descobridor",
        "narrative_style": "storytelling de descoberta",
        "content_goals": "Revelar curiosidades e segredos pouco conhecidos",
        "content_type_affinity": {"news": 0.1, "curiosity": 0.9, "lore": 0.6, "fact": 0.4},
        "editorial_keywords": [
            "hidden", "secrets", "beta", "unused", "cancelled",
            "developer", "interview", "easter egg", "glitch", "mystery",
        ],
        "custom_feeds": [
            {"url": "https://rsshub.app/reddit/subreddit/truegaming", "source_name": "r/truegaming", "item_type": "curiosity"},
            {"url": "https://rsshub.app/reddit/subreddit/patientgamers", "source_name": "r/patientgamers", "item_type": "curiosity"},
        ],
    },
    "noticias": {
        "niche": "Notícias e atualizações do mundo dos games",
        "target_audience": "Jogadores que querem estar por dentro",
        "tone_of_voice": "informativo, direto",
        "narrative_style": "reportagem direta",
        "content_goals": "Informar sobre lançamentos, patches e novidades",
        "content_type_affinity": {"news": 0.9, "curiosity": 0.3, "lore": 0.1, "fact": 0.2},
        "editorial_keywords": [
            "update", "patch", "release", "announcement", "trailer",
            "delay", "review", "dlc",
        ],
        "custom_feeds": [
            {"url": "https://feeds.feedburner.com/ign/games-all", "source_name": "IGN", "item_type": "news"},
            {"url": "https://www.gamespot.com/feeds/mashup/", "source_name": "GameSpot", "item_type": "news"},
            {"url": "https://rsshub.app/reddit/subreddit/games", "source_name": "r/games", "item_type": "news"},
        ],
    },
    "lore": {
        "niche": "Lore e história dos jogos",
        "target_audience": "Fãs de narrativa e worldbuilding",
        "tone_of_voice": "narrativo, imersivo",
        "narrative_style": "documentário",
        "content_goals": "Explorar a história e lore por trás dos jogos",
        "content_type_affinity": {"news": 0.1, "curiosity": 0.7, "lore": 0.9, "fact": 0.5},
        "editorial_keywords": [
            "story", "lore", "history", "behind the scenes",
            "development", "documentary", "timeline", "explained",
        ],
        "custom_feeds": [
            {"url": "https://rsshub.app/reddit/subreddit/truegaming", "source_name": "r/truegaming", "item_type": "lore"},
            {"url": "https://rsshub.app/reddit/subreddit/patientgamers", "source_name": "r/patientgamers", "item_type": "lore"},
        ],
    },
    "nostalgia": {
        "niche": "Nostalgia e retrospectiva de jogos clássicos",
        "target_audience": "Jogadores veteranos e fãs de retro",
        "tone_of_voice": "nostálgico, reflexivo",
        "narrative_style": "retrospectiva",
        "content_goals": "Relembrar e celebrar jogos clássicos",
        "content_type_affinity": {"news": 0.2, "curiosity": 0.7, "lore": 0.8, "fact": 0.4},
        "editorial_keywords": [
            "anniversary", "retrospective", "evolution", "history",
            "classic", "retro", "nostalgia", "then vs now",
        ],
        "custom_feeds": [
            {"url": "https://rsshub.app/reddit/subreddit/retrogaming", "source_name": "r/retrogaming", "item_type": "curiosity"},
            {"url": "https://rsshub.app/reddit/subreddit/crtgaming", "source_name": "r/crtgaming", "item_type": "curiosity"},
        ],
    },
    "educacional": {
        "niche": "Análise e educação sobre game design",
        "target_audience": "Jogadores curiosos sobre como jogos funcionam",
        "tone_of_voice": "educativo, analítico",
        "narrative_style": "análise detalhada",
        "content_goals": "Educar sobre mecânicas, design e desenvolvimento",
        "content_type_affinity": {"news": 0.3, "curiosity": 0.6, "lore": 0.5, "fact": 0.8},
        "editorial_keywords": [
            "explained", "analysis", "guide", "tutorial",
            "how to", "mechanics", "design", "breakdown",
        ],
        "custom_feeds": [
            {"url": "https://rsshub.app/reddit/subreddit/truegaming", "source_name": "r/truegaming", "item_type": "fact"},
            {"url": "https://rsshub.app/reddit/subreddit/gamedesign", "source_name": "r/gamedesign", "item_type": "fact"},
        ],
    },
}


def list_presets() -> list[dict[str, Any]]:
    """Return all available presets with their metadata (for UI)."""
    return [
        {"name": name, "niche": p["niche"], "content_goals": p["content_goals"]}
        for name, p in EDITORIAL_PRESETS.items()
    ]


def get_or_create_profile(session: Session, user_id: int) -> ChannelProfile:
    """Get the user's channel profile, creating a default one if missing."""
    profile = session.query(ChannelProfile).filter(
        ChannelProfile.user_id == user_id
    ).first()
    if not profile:
        profile = ChannelProfile(user_id=user_id)
        session.add(profile)
        session.flush()
    return profile


def apply_preset(session: Session, user_id: int, preset_name: str) -> ChannelProfile:
    """Apply a preset to the user's channel profile.

    Overwrites structured fields + free-text fields with preset defaults.
    Learned fields (learned_preferences, production_history_summary) are
    preserved — presets are about identity, not about history.
    """
    preset = EDITORIAL_PRESETS.get(preset_name)
    if not preset:
        raise ValueError(f"Unknown preset: {preset_name}. Available: {list(EDITORIAL_PRESETS.keys())}")

    profile = get_or_create_profile(session, user_id)
    for key, value in preset.items():
        setattr(profile, key, value)
    session.flush()
    log.info(f"Applied preset '{preset_name}' to channel profile of user {user_id}")
    return profile


def update_structured_fields(
    session: Session,
    user_id: int,
    *,
    content_type_affinity: Optional[dict] = None,
    editorial_keywords: Optional[list] = None,
    custom_feeds: Optional[list] = None,
    gameplay_driven_collection: Optional[bool] = None,
    diversity_strictness: Optional[float] = None,
) -> ChannelProfile:
    """Update structured fields of the Editorial Profile.

    Only provided fields are updated (partial update). Validates ranges.
    """
    profile = get_or_create_profile(session, user_id)

    if content_type_affinity is not None:
        # Validate: keys must be known item types, values 0.0–1.0
        valid_types = {"news", "curiosity", "lore", "fact"}
        validated = {}
        for k, v in content_type_affinity.items():
            if k in valid_types:
                validated[k] = max(0.0, min(1.0, float(v)))
        profile.content_type_affinity = validated

    if editorial_keywords is not None:
        profile.editorial_keywords = [str(k).strip() for k in editorial_keywords if str(k).strip()]

    if custom_feeds is not None:
        # Validate: each feed must have url + source_name
        validated_feeds = []
        for f in custom_feeds:
            if isinstance(f, dict) and f.get("url") and f.get("source_name"):
                validated_feeds.append({
                    "url": str(f["url"]),
                    "source_name": str(f["source_name"]),
                    "item_type": str(f.get("item_type", "news")),
                })
        profile.custom_feeds = validated_feeds

    if gameplay_driven_collection is not None:
        profile.gameplay_driven_collection = bool(gameplay_driven_collection)

    if diversity_strictness is not None:
        profile.diversity_strictness = max(0.0, min(1.0, float(diversity_strictness)))

    session.flush()
    return profile


def update_learned_preferences(
    session: Session,
    user_id: int,
    *,
    preferred_games: Optional[list[int]] = None,
    avoided_topics: Optional[list[str]] = None,
    preferred_styles: Optional[list[str]] = None,
) -> ChannelProfile:
    """Update learned preferences (called by feedback loop, not by user).

    Merges with existing preferences rather than overwriting. Each list is
    capped (FIFO eviction) to prevent unbounded growth and signal dilution
    over months/years of operation.

    Caps:
      - preferred_games: 20 entries (most recent first)
      - avoided_topics: 50 entries
      - preferred_styles: 10 entries

    See docs/CONVERGENCE_RISK_ANALYSIS.md Risk 2.
    """
    profile = get_or_create_profile(session, user_id)
    learned = dict(profile.learned_preferences or {})

    if preferred_games is not None:
        existing = learned.get("preferred_games", [])
        # Merge: new entries go to front (most recent), dedup, cap
        merged = list(preferred_games) + [g for g in existing if g not in preferred_games]
        learned["preferred_games"] = merged[:LEARNED_PREF_CAPS["preferred_games"]]

    if avoided_topics is not None:
        existing = learned.get("avoided_topics", [])
        merged = list(avoided_topics) + [t for t in existing if t not in avoided_topics]
        learned["avoided_topics"] = merged[:LEARNED_PREF_CAPS["avoided_topics"]]

    if preferred_styles is not None:
        existing = learned.get("preferred_styles", [])
        merged = list(preferred_styles) + [s for s in existing if s not in preferred_styles]
        learned["preferred_styles"] = merged[:LEARNED_PREF_CAPS["preferred_styles"]]

    profile.learned_preferences = learned
    session.flush()
    return profile


# Caps for learned preference lists (FIFO eviction when exceeded).
# Prevents unbounded growth and signal dilution over long-term operation.
LEARNED_PREF_CAPS = {
    "preferred_games": 20,
    "avoided_topics": 50,
    "preferred_styles": 10,
}


def decay_learned_preferences(session: Session, user_id: int) -> ChannelProfile:
    """Apply periodic decay to learned preferences.

    Called periodically (e.g., weekly) to ensure old preferences fade,
    allowing the system to re-explore topics that were previously avoided
    or to forget games that are no longer preferred.

    Strategy: trim each list by 1 entry if it has more than 5 items.
    This is a gentle forgetting mechanism — over time, old entries
    naturally fall off the back of the list.

    See docs/CONVERGENCE_RISK_ANALYSIS.md Risk 2.
    """
    profile = get_or_create_profile(session, user_id)
    learned = dict(profile.learned_preferences or {})

    changed = False
    for key in ("preferred_games", "avoided_topics", "preferred_styles"):
        lst = learned.get(key, [])
        if len(lst) > 5:
            # Remove the oldest entry (last in the list)
            learned[key] = lst[:-1]
            changed = True

    if changed:
        profile.learned_preferences = learned
        session.flush()
        log.info(f"Decayed learned preferences for user {user_id}")
    return profile


def update_production_history(
    session: Session,
    user_id: int,
    *,
    total_videos: Optional[int] = None,
    top_games: Optional[list[dict]] = None,
    avg_performance: Optional[float] = None,
) -> ChannelProfile:
    """Update production history summary (called after each video is produced)."""
    profile = get_or_create_profile(session, user_id)
    history = dict(profile.production_history_summary or {})

    if total_videos is not None:
        history["total_videos"] = total_videos
    if top_games is not None:
        history["top_games"] = top_games
    if avg_performance is not None:
        history["avg_performance"] = avg_performance

    profile.production_history_summary = history
    session.flush()
    return profile


def serialize_profile(profile: ChannelProfile) -> dict[str, Any]:
    """Serialize the profile for API responses."""
    return {
        "id": profile.id,
        "user_id": profile.user_id,
        "domain": profile.domain,
        "channel_description": profile.channel_description,
        "niche": profile.niche,
        "target_audience": profile.target_audience,
        "tone_of_voice": profile.tone_of_voice,
        "narrative_style": profile.narrative_style,
        "content_goals": profile.content_goals,
        "special_rules": profile.special_rules,
        # Multilingual
        "target_language": profile.target_language,
        "prompt_version": profile.prompt_version,
        # V2 structured
        "content_type_affinity": profile.content_type_affinity or {},
        "editorial_keywords": profile.editorial_keywords or [],
        "custom_feeds": profile.custom_feeds or [],
        "gameplay_driven_collection": profile.gameplay_driven_collection if profile.gameplay_driven_collection is not None else True,
        "diversity_strictness": profile.diversity_strictness if profile.diversity_strictness is not None else 0.5,
        "collection_focus": profile.collection_focus,
        # V2 learned
        "learned_preferences": profile.learned_preferences or {},
        "production_history_summary": profile.production_history_summary or {},
        # Metadata (Kids-specific: kids_age_range, categories, etc.)
        "metadata": profile.metadata_json or {},
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }
