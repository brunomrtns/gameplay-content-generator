"""Editorial domain types — dataclasses for the V2 editorial intelligence.

These are the in-memory artifacts that flow through the V2 pipeline:

    Editorial Profile (persisted)  →  Editorial Intent (per-cycle)
                                    →  Editorial Brief (per-cycle)
                                    →  CollectionResult
                                    →  CompositeScore

All are plain dataclasses (no business logic). Logic lives in the builders
and services. This keeps the three concepts (Profile / Intent / Brief)
cleanly separated and independently testable.

See docs/EDITORIAL_INTELLIGENCE_V2_PROPOSAL.md §2, §5, §6.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ── Game Target ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GameTarget:
    """A game prioritized for collection in this cycle.

    Attributes:
        game_id: the Game.id
        name: canonical name (used to build search queries)
        priority: 0.0–1.0 (higher = more queries allocated)
        reason: human-readable justification for auditability
        clips_ready: number of ready gameplay clips (drives allocation)
    """

    game_id: int
    name: str
    priority: float
    reason: str
    clips_ready: int = 0


# ── Search Query ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SearchQuery:
    """A single expanded search query.

    Built by combining a game name with a search-template keyword.
    Carries metadata so the collector can attribute the KI correctly.
    """

    text: str
    game_id: Optional[int]
    template_name: str
    item_type: str


# ── Feed Spec ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FeedSpec:
    """A feed to be consulted during collection.

    Attributes:
        url: feed URL
        source_name: human-readable source name (IGN, r/games, ...)
        item_type: default KnowledgeItemType for items from this feed
        scope: "global" (shared pool) or "channel" (per-channel custom)
    """

    url: str
    source_name: str
    item_type: str
    scope: str = "global"  # "global" | "channel"


# ── Editorial Intent ─────────────────────────────────────────────────────────


@dataclass
class EditorialIntent:
    """What the channel needs to produce right now.

    Computed per collection cycle from the Editorial Profile + dynamic
    context (gameplay inventory, recent videos, queue state). Not persisted.
    """

    # How many KIs we want to collect per item_type this cycle
    collection_targets: dict[str, int] = field(default_factory=dict)
    # Games to prioritize (with reason for audit)
    priority_games: list[GameTarget] = field(default_factory=list)
    # Games to avoid — {game_id: cooldown_days}
    cooldown_games: dict[int, int] = field(default_factory=dict)
    # Fill strategy when queue is low
    fill_strategy: str = "balanced"  # "balanced" | "evergreen_fallback" | "news_priority"
    # Temporal context
    time_context: str = "normal"  # "normal" | "breaking_news_window" | "evergreen_fill"
    # Format rotation hint
    format_rotation: str = "balanced"  # "balanced" | "prefer_curiosity_short" | "prefer_generate_short"


# ── Editorial Brief ──────────────────────────────────────────────────────────


@dataclass
class EditorialBrief:
    """How the collection will be executed this cycle.

    Built from Editorial Profile + Editorial Intent. Not persisted.
    Drives the GoalOrientedCollector.
    """

    # Feeds to consult (channel-custom + global fallback)
    feeds: list[FeedSpec] = field(default_factory=list)
    # Expanded search queries (game + editorial keywords)
    search_queries: list[SearchQuery] = field(default_factory=list)
    # Which search templates are active this cycle
    active_templates: list[str] = field(default_factory=list)
    # Inherited from Intent
    target_games: list[GameTarget] = field(default_factory=list)
    cooldown_games: dict[int, int] = field(default_factory=dict)
    collection_targets: dict[str, int] = field(default_factory=dict)
    # Scoring weights for this channel (derived from content_type_affinity)
    scoring_weights: dict[str, float] = field(default_factory=dict)
    # Volume limits
    max_queries_per_game: int = 5
    max_total_queries: int = 30
    # User id this brief was built for (for audit)
    user_id: Optional[int] = None


# ── Collection Result ────────────────────────────────────────────────────────


@dataclass
class CollectionResult:
    """Outcome of a goal-oriented collection cycle.

    Attributes:
        collected: {item_type: [KnowledgeItem, ...]} — what was collected
        remaining: {item_type: int} — unmet targets (deficit)
        total: total KIs collected
        queries_executed: number of search queries actually run
        feeds_consulted: number of feeds actually fetched
    """

    collected: dict[str, list] = field(default_factory=dict)
    remaining: dict[str, int] = field(default_factory=dict)
    total: int = 0
    queries_executed: int = 0
    feeds_consulted: int = 0


# ── Composite Score ──────────────────────────────────────────────────────────


@dataclass
class CompositeScore:
    """The 3-layer composite score for a KI relative to a channel.

    final_score = editorial_quality * production_fit * editorial_timing
    All components are 0.0–1.0.
    """

    editorial_quality: float  # Layer 1 (intrinsic, LLM-scored)
    production_fit: float  # Layer 2 (relational, per-channel)
    editorial_timing: float  # Layer 3 (temporal, per-channel)
    final_score: float  # product of the three

    # Optional breakdown for auditability / UI
    fit_breakdown: dict = field(default_factory=dict)
    timing_breakdown: dict = field(default_factory=dict)

    @classmethod
    def compute(
        cls,
        editorial_score_0_100: float,
        fit: float,
        timing: float,
        *,
        fit_breakdown: dict | None = None,
        timing_breakdown: dict | None = None,
    ) -> "CompositeScore":
        quality = max(0.0, min(1.0, editorial_score_0_100 / 100.0))
        fit = max(0.0, min(1.0, fit))
        timing = max(0.0, min(1.0, timing))
        return cls(
            editorial_quality=quality,
            production_fit=fit,
            editorial_timing=timing,
            final_score=quality * fit * timing,
            fit_breakdown=fit_breakdown or {},
            timing_breakdown=timing_breakdown or {},
        )
