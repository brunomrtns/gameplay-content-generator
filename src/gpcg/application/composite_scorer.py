"""Composite Scorer — 3-layer multiplicative scoring for KnowledgeItems.

The composite score replaces the single editorial_score when
gpcg_composite_scoring_enabled is True. It is RELATIVE to the channel,
not absolute.

    Final = Editorial Quality × Production Fit × Editorial Timing

Layer 1 — Editorial Quality (intrinsic, global, LLM-scored):
    The existing editorial_score (0-100), normalized to 0.0–1.0.
    Computed once per KI by score_knowledge_item(). Does NOT change per channel.

Layer 2 — Production Fit (relational, per-channel, cheap):
    How well this KI can be PRODUCED as a video for THIS channel.
    Components:
      - gameplay_availability (0.40): does the channel have gameplay for this game?
      - content_type_affinity (0.25): does the channel want this item_type?
      - channel_affinity (0.20): semantic similarity to channel identity (embeddings)
      - source_authority (0.15): tier list of sources
    All computed without LLM — DB queries + embedding dot product.

Layer 3 — Editorial Timing (temporal, per-channel, cheap):
    Is NOW the right time for this idea?
    Components:
      - freshness: decay based on item_type (news decays fast, lore is evergreen)
      - diversity_penalty: 1.0 if game not recently covered, 0.3 if in cooldown
    All computed without LLM — date math + history query.

See docs/EDITORIAL_INTELLIGENCE_V2_PROPOSAL.md §11.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gpcg.domain.editorial_types import CompositeScore, EditorialBrief
from gpcg.domain.models import (
    ChannelProfile,
    GameplayAsset,
    GameplaySource,
    KnowledgeItem,
    Video,
)
from gpcg.logging import get_logger

log = get_logger(__name__)


# Source authority tier list (higher = more authoritative)
SOURCE_AUTHORITY_TIERS: dict[str, float] = {
    "IGN": 0.9,
    "GameSpot": 0.85,
    "Polygon": 0.85,
    "Eurogamer": 0.8,
    "Rock Paper Shotgun": 0.8,
    "Kotaku": 0.7,
    "r/games": 0.6,
    "r/gaming": 0.55,
    "r/truegaming": 0.6,
    "r/patientgamers": 0.6,
    "Google News": 0.5,
    "manual": 0.7,
}
DEFAULT_SOURCE_AUTHORITY = 0.5

# Diversity penalty for games in cooldown
COOLDOWN_PENALTY = 0.3

# Weights for Production Fit components
FIT_WEIGHTS = {
    "gameplay_availability": 0.40,
    "content_type_affinity": 0.25,
    "channel_affinity": 0.20,
    "source_authority": 0.15,
}


class CompositeScorer:
    """Computes the 3-layer composite score for a KI relative to a channel."""

    def score(
        self,
        ki: KnowledgeItem,
        brief: EditorialBrief,
        session: Session,
        user_id: int,
        channel_embedding: Optional[list[float]] = None,
        ki_embedding: Optional[list[float]] = None,
    ) -> CompositeScore:
        """Compute the composite score.

        Args:
            ki: the KnowledgeItem to score
            brief: the Editorial Brief for this channel/cycle
            session: DB session
            user_id: the channel owner
            channel_embedding: embedding of the channel profile (optional)
            ki_embedding: embedding of the KI (optional)

        Returns:
            CompositeScore with all 3 layers + breakdown
        """
        # Layer 1: Editorial Quality (already computed by score_knowledge_item)
        quality = ki.editorial_score

        # Layer 2: Production Fit
        fit, fit_breakdown = self._compute_fit(
            ki, brief, session, user_id, channel_embedding, ki_embedding
        )

        # Layer 3: Editorial Timing
        timing, timing_breakdown = self._compute_timing(ki, brief)

        return CompositeScore.compute(
            quality, fit, timing,
            fit_breakdown=fit_breakdown,
            timing_breakdown=timing_breakdown,
        )

    # ── Layer 2: Production Fit ────────────────────────────────────────────

    def _compute_fit(
        self,
        ki: KnowledgeItem,
        brief: EditorialBrief,
        session: Session,
        user_id: int,
        channel_embedding: Optional[list[float]],
        ki_embedding: Optional[list[float]],
    ) -> tuple[float, dict]:
        """Compute Production Fit (Layer 2).

        Returns (fit_score, breakdown_dict).
        """
        breakdown = {}

        # 1. Gameplay availability
        gameplay_avail = self._gameplay_availability(ki, session, user_id)
        breakdown["gameplay_availability"] = gameplay_avail

        # 2. Content type affinity
        # Use brief.scoring_weights as a proxy, or directly from profile
        # The brief has collection_targets which reflect affinity
        affinity = self._content_type_affinity(ki, brief)
        breakdown["content_type_affinity"] = affinity

        # 3. Channel affinity (embedding similarity)
        channel_affinity = self._channel_affinity(channel_embedding, ki_embedding)
        breakdown["channel_affinity"] = channel_affinity

        # 4. Source authority
        authority = self._source_authority(ki)
        breakdown["source_authority"] = authority

        # Weighted sum
        fit = (
            gameplay_avail * FIT_WEIGHTS["gameplay_availability"]
            + affinity * FIT_WEIGHTS["content_type_affinity"]
            + channel_affinity * FIT_WEIGHTS["channel_affinity"]
            + authority * FIT_WEIGHTS["source_authority"]
        )

        return fit, breakdown

    def _gameplay_availability(self, ki: KnowledgeItem, session: Session, user_id: int) -> float:
        """Check if the channel has gameplay for the KI's game.

        Returns: 1.0 if gameplay ready, 0.5 if sources exist but no assets,
        0.0 if no gameplay at all (or ki has no game_id).
        """
        if ki.game_id is None:
            # General-topic KI — gameplay availability is neutral (0.5)
            # The EditorialPlanner will pick a background game
            return 0.5

        # Check for gameplay assets for this game + user
        clip_count = session.execute(
            select(func.count(GameplayAsset.id))
            .join(GameplaySource, GameplayAsset.source_id == GameplaySource.id)
            .where(
                GameplaySource.user_id == user_id,
                GameplaySource.game_id == ki.game_id,
            )
        ).scalar_one()

        if clip_count and clip_count > 0:
            return 1.0

        # Check if sources exist (mapped but no clips yet)
        source_count = session.execute(
            select(func.count(GameplaySource.id)).where(
                GameplaySource.user_id == user_id,
                GameplaySource.game_id == ki.game_id,
            )
        ).scalar_one()

        if source_count and source_count > 0:
            return 0.5

        return 0.0

    def _content_type_affinity(self, ki: KnowledgeItem, brief: EditorialBrief) -> float:
        """Get the channel's affinity for this KI's item_type.

        Derives from collection_targets: if the channel is actively seeking
        this item_type, affinity is high. Falls back to 0.5 (neutral).
        """
        # Map item_type to template name (they match for most types)
        # collection_targets keys are template names (curiosity, news, lore, fact)
        # ki.item_type values are also these names
        targets = brief.collection_targets or {}
        max_target = max(targets.values(), default=1) if targets else 1
        target = targets.get(ki.item_type, 0)
        if max_target == 0:
            return 0.5
        # Normalize: 0 target → 0.0, target == max_target → 1.0
        return min(1.0, target / max_target) if max_target > 0 else 0.5

    def _channel_affinity(
        self,
        channel_embedding: Optional[list[float]],
        ki_embedding: Optional[list[float]],
    ) -> float:
        """Compute semantic similarity between channel and KI.

        Returns 0.5 (neutral) if either embedding is missing.
        """
        if not channel_embedding or not ki_embedding:
            return 0.5  # neutral when embeddings unavailable

        from gpcg.application.embedding_service import cosine_similarity
        sim = cosine_similarity(channel_embedding, ki_embedding)
        # Cosine similarity is -1 to 1, but embeddings are typically 0 to 1.
        # Clamp to 0–1.
        return max(0.0, min(1.0, sim))

    def _source_authority(self, ki: KnowledgeItem) -> float:
        """Get the authority score for the KI's source."""
        if ki.source_name:
            return SOURCE_AUTHORITY_TIERS.get(ki.source_name, DEFAULT_SOURCE_AUTHORITY)
        return DEFAULT_SOURCE_AUTHORITY

    # ── Layer 3: Editorial Timing ──────────────────────────────────────────

    def _compute_timing(
        self,
        ki: KnowledgeItem,
        brief: EditorialBrief,
    ) -> tuple[float, dict]:
        """Compute Editorial Timing (Layer 3).

        Returns (timing_score, breakdown_dict).
        """
        breakdown = {}

        # 1. Freshness (from lifecycle_manager)
        from gpcg.application.lifecycle_manager import compute_freshness
        freshness = compute_freshness(ki)
        breakdown["freshness"] = freshness

        # 2. Diversity penalty
        diversity = self._diversity_penalty(ki, brief)
        breakdown["diversity_penalty"] = diversity

        timing = freshness * diversity
        return timing, breakdown

    def _diversity_penalty(self, ki: KnowledgeItem, brief: EditorialBrief) -> float:
        """Check if the KI's game is in cooldown.

        Returns 1.0 if not in cooldown, COOLDOWN_PENALTY (0.3) if in cooldown.
        """
        if ki.game_id is None:
            return 1.0  # general-topic KIs are not affected by game cooldown

        cooldown_games = brief.cooldown_games or {}
        if ki.game_id in cooldown_games:
            return COOLDOWN_PENALTY
        return 1.0
