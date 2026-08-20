"""Editorial Brief Builder — translates Intent into executable collection plan.

The Brief is a TEMPORARY artifact built from Editorial Profile + Editorial
Intent. It answers "How do we find the content?" and drives the
GoalOrientedCollector.

    Editorial Profile + Editorial Intent → Editorial Brief (temp)

The Brief contains: feeds to consult, expanded search queries, active
search templates, target games, cooldowns, collection targets, scoring
weights, and volume limits.

See docs/EDITORIAL_INTELLIGENCE_V2_PROPOSAL.md §6, §7.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from gpcg.domain.editorial_types import (
    EditorialBrief,
    EditorialIntent,
    FeedSpec,
    SearchQuery,
)
from gpcg.core.models import ChannelProfile
from gpcg.domain.search_templates import (
    SEARCH_TEMPLATES,
    merge_keywords,
)
from gpcg.logging import get_logger

log = get_logger(__name__)


# Default scoring weights (neutral — no preference).
# Derived from content_type_affinity in _derive_scoring_weights.
DEFAULT_SCORING_WEIGHTS: dict[str, float] = {
    "curiosity_gap": 0.30,
    "surprise_potential": 0.25,
    "retention_potential": 0.20,
    "familiarity": 0.15,
    "insight_quality": 0.10,
}

# How affinity maps to scoring weight emphasis.
# High curiosity affinity → curiosity_gap weight up, familiarity down.
_AFFINITY_SCORING_ADJUSTMENTS = {
    "curiosity": {"curiosity_gap": +0.10, "surprise_potential": +0.05, "familiarity": -0.05},
    "news": {"retention_potential": +0.05, "familiarity": +0.05, "insight_quality": -0.05},
    "lore": {"familiarity": +0.10, "insight_quality": +0.05, "surprise_potential": -0.05},
    "fact": {"insight_quality": +0.10, "curiosity_gap": -0.05, "surprise_potential": -0.05},
}


class EditorialBriefBuilder:
    """Builds the Editorial Brief from Profile + Intent."""

    def build(
        self,
        session: Session,
        user_id: int,
        profile: ChannelProfile,
        intent: EditorialIntent,
    ) -> EditorialBrief:
        feeds = self._resolve_feeds(profile)
        active_templates = self._select_templates(intent.collection_targets)
        search_queries = self._expand_queries(
            intent.priority_games,
            active_templates,
            profile.editorial_keywords or [],
        )
        scoring_weights = self._derive_scoring_weights(profile.content_type_affinity or {})

        brief = EditorialBrief(
            feeds=feeds,
            search_queries=search_queries,
            active_templates=active_templates,
            target_games=intent.priority_games,
            cooldown_games=intent.cooldown_games,
            collection_targets=intent.collection_targets,
            scoring_weights=scoring_weights,
            max_queries_per_game=5,
            max_total_queries=30,
            user_id=user_id,
        )
        log.info(
            f"EditorialBrief for user {user_id}: "
            f"{len(feeds)} feeds, {len(search_queries)} queries, "
            f"templates={active_templates}"
        )
        return brief

    # ── Feeds ──────────────────────────────────────────────────────────────

    def _resolve_feeds(self, profile: ChannelProfile) -> list[FeedSpec]:
        """Resolve feeds: channel-custom first, global fallback if empty."""
        custom = profile.custom_feeds or []
        if custom:
            feeds = [
                FeedSpec(
                    url=f["url"],
                    source_name=f.get("source_name", "custom"),
                    item_type=f.get("item_type", "news"),
                    scope="channel",
                )
                for f in custom
                if isinstance(f, dict) and f.get("url")
            ]
            # Always include global feeds as fallback (channel feeds may not
            # cover everything). Global feeds produce shared-pool KIs.
            feeds.extend(self._global_feeds())
            return feeds
        # No custom feeds → global only (legacy behavior)
        return self._global_feeds()

    def _global_feeds(self) -> list[FeedSpec]:
        """Return the global gaming feeds (GENERAL_GAMING_FEEDS)."""
        from gpcg.application.content_collectors import GENERAL_GAMING_FEEDS
        return [
            FeedSpec(
                url=f["url"],
                source_name=f.get("source_name", "unknown"),
                item_type=f.get("item_type", "news"),
                scope="global",
            )
            for f in GENERAL_GAMING_FEEDS
        ]

    # ── Templates ──────────────────────────────────────────────────────────

    def _select_templates(self, collection_targets: dict[str, int]) -> list[str]:
        """Select templates where the collection target > 0 and template exists."""
        return [
            name for name, target in collection_targets.items()
            if target > 0 and name in SEARCH_TEMPLATES
        ]

    # ── Query expansion ────────────────────────────────────────────────────

    def _expand_queries(
        self,
        priority_games: list,
        active_templates: list[str],
        custom_keywords: list[str],
    ) -> list[SearchQuery]:
        """Expand games × templates into search queries.

        For each game, distributes queries across active templates in round-robin
        fashion (1 keyword per template per round) to ensure all templates get
        representation. Respects max_queries_per_game and max_total_queries.
        """
        queries: list[SearchQuery] = []
        for game in priority_games:
            # Build per-template keyword lists for this game
            template_keywords: list[tuple[str, str, str]] = []  # (template_name, item_type, keyword)
            for template_name in active_templates:
                template = SEARCH_TEMPLATES.get(template_name)
                if not template:
                    continue
                all_keywords = merge_keywords(template, custom_keywords)
                for kw in all_keywords:
                    template_keywords.append((template_name, template.item_type, kw))

            # Round-robin across templates: take 1 keyword from each template in turn
            # Group by template for round-robin
            by_template: dict[str, list[tuple[str, str]]] = {}
            template_order: list[str] = []
            for tname, itype, kw in template_keywords:
                if tname not in by_template:
                    by_template[tname] = []
                    template_order.append(tname)
                by_template[tname].append((itype, kw))

            game_queries = 0
            round_idx = 0
            while game_queries < self._max_per_game and len(queries) < self._max_total:
                added_this_round = False
                for tname in template_order:
                    if game_queries >= self._max_per_game or len(queries) >= self._max_total:
                        break
                    kws = by_template[tname]
                    if round_idx < len(kws):
                        itype, kw = kws[round_idx]
                        queries.append(SearchQuery(
                            text=f"{game.name} {kw}",
                            game_id=game.game_id,
                            template_name=tname,
                            item_type=itype,
                        ))
                        game_queries += 1
                        added_this_round = True
                if not added_this_round:
                    break  # all templates exhausted
                round_idx += 1
            if len(queries) >= self._max_total:
                break
        return queries[:self._max_total]

    # Properties for limits (overridable in tests via subclassing)
    _max_per_game: int = 5
    _max_total: int = 30

    # ── Scoring weights ────────────────────────────────────────────────────

    def _derive_scoring_weights(self, content_type_affinity: dict) -> dict[str, float]:
        """Derive scoring weights from content_type_affinity.

        Base weights are the neutral defaults. For each affinity with high
        weight (> 0.6), apply the corresponding adjustment.
        """
        weights = dict(DEFAULT_SCORING_WEIGHTS)
        for item_type, affinity in content_type_affinity.items():
            if affinity > 0.6:
                adjustments = _AFFINITY_SCORING_ADJUSTMENTS.get(item_type, {})
                for dim, delta in adjustments.items():
                    weights[dim] = weights.get(dim, 0.0) + delta
        # Normalize to sum ~1.0
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}
        return weights
