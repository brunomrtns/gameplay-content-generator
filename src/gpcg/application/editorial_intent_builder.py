"""Editorial Intent Builder — computes what the channel needs to produce now.

The Intent is a TEMPORARY artifact, recomputed at every collection cycle.
It translates the persisted Editorial Profile + dynamic context (gameplay
inventory, recent videos, queue state) into concrete collection targets.

    Editorial Profile (persisted) + context → Editorial Intent (temp)

The Intent answers: "What do we want to produce now?"
The Brief (next stage) answers: "How do we find it?"

See docs/EDITORIAL_INTELLIGENCE_V2_PROPOSAL.md §5.
"""

from __future__ import annotations

from collections import Counter
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.domain.editorial_types import EditorialIntent, GameTarget
from gpcg.domain.models import (
    Automation,
    ChannelProfile,
    Game,
    GameplayAsset,
    GameplaySource,
    KnowledgeItem,
    KnowledgeItemStatus,
    Video,
)
from gpcg.logging import get_logger

log = get_logger(__name__)


# Default collection targets when profile has no content_type_affinity.
# Balanced — no strong preference. Matches legacy behavior (collect everything).
DEFAULT_TARGETS: dict[str, int] = {
    "news": 5,
    "curiosity": 5,
    "lore": 3,
    "fact": 3,
}

# Maps content_type_affinity keys to target counts.
# Higher affinity → more items of that type sought per cycle.
_AFFINITY_TO_TARGET = {
    "news": (2, 10),       # (min, max) items
    "curiosity": (3, 12),
    "lore": (2, 10),
    "fact": (2, 8),
}


class EditorialIntentBuilder:
    """Builds the Editorial Intent for a channel at each collection cycle."""

    def build(self, session: Session, user_id: int, profile: ChannelProfile) -> EditorialIntent:
        gameplay_inventory = self._get_gameplay_inventory(session, user_id)
        recent_videos = self._get_recent_videos(session, user_id, limit=10)
        queue_state = self._get_queue_state(session, user_id)

        priority_games = self._compute_priority_games(
            gameplay_inventory, recent_videos, profile
        )
        cooldown_games = self._compute_cooldowns(
            recent_videos, profile.diversity_strictness if profile.diversity_strictness is not None else 0.5
        )
        collection_targets = self._compute_targets(profile, queue_state)
        fill_strategy = self._determine_fill_strategy(queue_state, profile)
        format_rotation = self._determine_format_rotation(recent_videos)

        intent = EditorialIntent(
            collection_targets=collection_targets,
            priority_games=priority_games,
            cooldown_games=cooldown_games,
            fill_strategy=fill_strategy,
            time_context="normal",
            format_rotation=format_rotation,
        )
        log.info(
            f"EditorialIntent for user {user_id}: "
            f"targets={collection_targets}, "
            f"priority_games={[g.name for g in priority_games]}, "
            f"cooldowns={cooldown_games}, "
            f"fill={fill_strategy}, rotation={format_rotation}"
        )
        return intent

    # ── Gameplay inventory ─────────────────────────────────────────────────

    def _get_gameplay_inventory(self, session: Session, user_id: int) -> list[dict]:
        """Return gameplay inventory: [{game_id, name, clips_ready, ...}].

        Counts gameplay assets (clips) per game for sources that are ready.
        """
        # Count ready gameplay assets per game for this user
        rows = session.execute(
            select(
                GameplaySource.game_id,
                Game.canonical_name,
                func.count(GameplayAsset.id).label("clips"),
            )
            .join(Game, GameplaySource.game_id == Game.id)
            .join(GameplayAsset, GameplayAsset.source_id == GameplaySource.id)
            .where(
                GameplaySource.user_id == user_id,
                GameplaySource.game_id.isnot(None),
            )
            .group_by(GameplaySource.game_id, Game.canonical_name)
        ).all()

        inventory = []
        for row in rows:
            if row.game_id and row.canonical_name and row.clips and row.clips > 0:
                inventory.append({
                    "game_id": row.game_id,
                    "name": row.canonical_name,
                    "clips_ready": int(row.clips),
                })
        return inventory

    # ── Recent videos ──────────────────────────────────────────────────────

    def _get_recent_videos(self, session: Session, user_id: int, limit: int = 10) -> list[Video]:
        """Return the most recent videos for diversity/cooldown analysis."""
        return (
            session.query(Video)
            .filter(Video.user_id == user_id)
            .order_by(Video.created_at.desc())
            .limit(limit)
            .all()
        )

    # ── Queue state ────────────────────────────────────────────────────────

    def _get_queue_state(self, session: Session, user_id: int) -> dict:
        """Return current idea queue state: {length, max_size, types_in_queue}."""
        auto = session.query(Automation).filter(Automation.user_id == user_id).first()
        if not auto:
            return {"length": 0, "max_size": 10, "types_in_queue": set()}
        cfg = dict(auto.config or {})
        queue = cfg.get("idea_queue", [])
        max_size = cfg.get("max_queue_size", 10)
        # Count types already in queue
        types_in_queue: set[str] = set()
        for entry in queue:
            ki_id = entry.get("ki_id")
            if ki_id:
                ki = session.get(KnowledgeItem, ki_id)
                if ki:
                    types_in_queue.add(ki.item_type)
        return {
            "length": len(queue),
            "max_size": max_size,
            "types_in_queue": types_in_queue,
        }

    # ── Priority games ─────────────────────────────────────────────────────

    def _compute_priority_games(
        self,
        gameplay_inventory: list[dict],
        recent_videos: list[Video],
        profile: ChannelProfile,
    ) -> list[GameTarget]:
        """Compute which games to prioritize for collection.

        Priority = (clips_available / max_clips) * (1 - recent_coverage_penalty)

        If gameplay_driven_collection is False, all games get equal priority.
        """
        if not gameplay_inventory:
            return []

        # If gameplay-driven collection is disabled, all games equal priority
        if profile.gameplay_driven_collection is False:
            return [
                GameTarget(
                    game_id=g["game_id"],
                    name=g["name"],
                    priority=0.5,
                    reason="gameplay disponível (coleta não-dirigida)",
                    clips_ready=g["clips_ready"],
                )
                for g in gameplay_inventory
            ]

        # Count recent coverage per game
        coverage = Counter()
        for v in recent_videos:
            if v.game_id:
                coverage[v.game_id] += 1

        max_clips = max((g["clips_ready"] for g in gameplay_inventory), default=1)
        max_coverage = max(coverage.values(), default=0)

        targets = []
        for g in gameplay_inventory:
            clips_ratio = g["clips_ready"] / max_clips if max_clips > 0 else 0
            cov = coverage.get(g["game_id"], 0)
            coverage_penalty = cov / max(max_coverage, 3) if max_coverage > 0 else 0
            # Priority floor of 0.15 ensures lightly-clipped games still receive
            # some collection attention, preventing a single heavily-clipped game
            # from monopolizing collection. See CONVERGENCE_RISK_ANALYSIS.md Risk 5.
            priority = max(0.15, clips_ratio * (1 - coverage_penalty))

            reason_parts = [f"{g['clips_ready']} clips"]
            if cov > 0:
                reason_parts.append(f"coberto {cov}x recentemente")
            else:
                reason_parts.append("sem cobertura recente")

            targets.append(GameTarget(
                game_id=g["game_id"],
                name=g["name"],
                priority=round(priority, 3),
                reason=", ".join(reason_parts),
                clips_ready=g["clips_ready"],
            ))

        # Sort by priority descending
        targets.sort(key=lambda t: t.priority, reverse=True)
        return targets

    # ── Cooldowns ──────────────────────────────────────────────────────────

    def _compute_cooldowns(
        self,
        recent_videos: list[Video],
        strictness: float,
    ) -> dict[int, int]:
        """Compute game cooldowns based on recent coverage.

        strictness 0.0 → no cooldown
        strictness 1.0 → 30-day cooldown after 1 coverage
        """
        if strictness <= 0:
            return {}

        # Threshold: how many coverages before cooldown kicks in
        # strictness 0.0 → 3, 1.0 → 1
        threshold = max(1, int(round(3 - strictness * 2)))
        # Cooldown days: strictness 0.0 → 7, 1.0 → 30
        cooldown_days = int(round(7 + strictness * 23))

        coverage = Counter()
        for v in recent_videos:
            if v.game_id:
                coverage[v.game_id] += 1

        return {
            game_id: cooldown_days
            for game_id, count in coverage.items()
            if count >= threshold
        }

    # ── Collection targets ─────────────────────────────────────────────────

    def _compute_targets(self, profile: ChannelProfile, queue_state: dict) -> dict[str, int]:
        """Compute how many KIs to collect per item_type this cycle.

        Based on content_type_affinity (higher affinity → more items) and
        queue state (if queue already has items of a type, reduce target).
        """
        affinity = profile.content_type_affinity or {}
        types_in_queue = queue_state.get("types_in_queue", set())

        if not affinity:
            # No affinity set → use balanced defaults
            targets = dict(DEFAULT_TARGETS)
        else:
            targets = {}
            for item_type, (min_t, max_t) in _AFFINITY_TO_TARGET.items():
                aff = affinity.get(item_type, 0.5)
                # Scale: affinity 0.0 → min, 1.0 → max
                count = int(min_t + (max_t - min_t) * aff)
                targets[item_type] = count

        # Reduce targets for types already well-represented in queue
        for item_type in list(targets.keys()):
            if item_type in types_in_queue and queue_state["length"] >= queue_state["max_size"] // 2:
                targets[item_type] = max(0, targets[item_type] - 2)

        return targets

    # ── Fill strategy ──────────────────────────────────────────────────────

    def _determine_fill_strategy(self, queue_state: dict, profile: ChannelProfile) -> str:
        """Determine the fill strategy when queue is low."""
        if queue_state["length"] == 0:
            # Empty queue — prefer evergreen to build a buffer
            affinity = profile.content_type_affinity or {}
            if affinity.get("news", 0) > 0.7:
                return "news_priority"
            return "evergreen_fallback"
        return "balanced"

    # ── Format rotation ────────────────────────────────────────────────────

    def _determine_format_rotation(self, recent_videos: list[Video]) -> str:
        """Determine format rotation hint based on recent video types.

        If >70% of recent videos are generate_short, prefer curiosity_short
        (and vice versa) to maintain variety.
        """
        if not recent_videos:
            return "balanced"

        # Video.artifacts contains job_type or we check via ContentPlan
        # Simpler: check if video has game_id (generate_short) or not (curiosity_short)
        game_related = sum(1 for v in recent_videos if v.game_id)
        total = len(recent_videos)
        if total == 0:
            return "balanced"

        ratio_game = game_related / total
        if ratio_game > 0.7:
            return "prefer_curiosity_short"
        if ratio_game < 0.3:
            return "prefer_generate_short"
        return "balanced"
