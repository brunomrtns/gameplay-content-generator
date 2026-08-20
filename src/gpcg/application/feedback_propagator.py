"""Feedback Propagator — propagates editorial signals via embeddings.

When a user rejects a KI, adds a manual KI, or produces a video, the system
learns. This module:

1. Records the signal in the `editorial_signals` table
2. Propagates the signal to similar KIs via embedding cosine similarity
3. Updates the channel's learned_preferences and production_history_summary

Signals:
  - rejection_penalty: user rejected a KI → penalty on similar KIs
  - manual_add_boost: user manually added a KI → boost on similar KIs
  - production_history: a video was produced → update production_history_summary

Propagation uses embeddings: KIs with cosine similarity > threshold to the
source KI receive a proportional adjustment to their editorial_score.

See docs/EDITORIAL_INTELLIGENCE_V2_PROPOSAL.md §13.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from gpcg.application.embedding_service import (
    cosine_similarity,
    get_knowledge_item_embedding,
)
from gpcg.application.editorial_profile_service import (
    update_learned_preferences,
    update_production_history,
)
from gpcg.config import get_settings
from gpcg.core.models import (
    EditorialSignal,
    KnowledgeItem,
    KnowledgeItemStatus,
    Video,
)
from gpcg.logging import get_logger

log = get_logger(__name__)


# Signal types
SIGNAL_REJECTION_PENALTY = "rejection_penalty"
SIGNAL_MANUAL_ADD_BOOST = "manual_add_boost"
SIGNAL_PRODUCTION_HISTORY = "production_history"

# Maximum cumulative feedback adjustment per KI (±N points).
# Prevents death spirals: rejecting 5 similar KIs has the same effect as 1.
MAX_CUMULATIVE_ADJUSTMENT = 20.0

# Decay factor applied to feedback_adjustment each cycle.
# 0.95 means 5% of the adjustment fades per lifecycle update.
# After ~14 cycles (~14 days if daily), half the adjustment is gone.
FEEDBACK_DECAY_FACTOR = 0.95


class FeedbackPropagator:
    """Propagates editorial feedback signals to similar KIs via embeddings."""

    def propagate_rejection(
        self,
        session: Session,
        user_id: int,
        ki_id: int,
        reason: Optional[str] = None,
    ) -> int:
        """Propagate a rejection penalty to similar KIs.

        Args:
            session: DB session
            user_id: the channel owner
            ki_id: the rejected KnowledgeItem ID
            reason: optional rejection reason

        Returns:
            Number of KIs that received a penalty adjustment.
        """
        settings = get_settings()
        if not settings.gpcg_feedback_loop_enabled:
            log.debug("Feedback loop disabled, skipping rejection propagation")
            return 0

        ki = session.get(KnowledgeItem, ki_id)
        if not ki:
            return 0

        # Record the signal
        signal = EditorialSignal(
            ki_id=ki_id,
            user_id=user_id,
            signal_type=SIGNAL_REJECTION_PENALTY,
            signal_value=-settings.gpcg_feedback_boost_factor,
            source_ki_id=ki_id,
        )
        session.add(signal)
        session.flush()

        # Update learned preferences
        if ki.title:
            update_learned_preferences(
                session, user_id,
                avoided_topics=[ki.title[:100]],
            )

        # Propagate to similar KIs
        count = self._propagate_to_similar(
            session, user_id, ki_id,
            adjustment=-settings.gpcg_feedback_boost_factor,
            threshold=settings.gpcg_feedback_similarity_threshold,
        )

        log.info(f"FeedbackPropagator: rejection of KI {ki_id} propagated to {count} similar KIs")
        return count

    def propagate_manual_add(
        self,
        session: Session,
        user_id: int,
        ki_id: int,
    ) -> int:
        """Propagate a manual-add boost to similar KIs.

        When a user manually adds a KI, it signals interest in that topic.
        Similar KIs receive a small boost.

        Returns:
            Number of KIs that received a boost adjustment.
        """
        settings = get_settings()
        if not settings.gpcg_feedback_loop_enabled:
            return 0

        ki = session.get(KnowledgeItem, ki_id)
        if not ki:
            return 0

        # Record the signal
        signal = EditorialSignal(
            ki_id=ki_id,
            user_id=user_id,
            signal_type=SIGNAL_MANUAL_ADD_BOOST,
            signal_value=settings.gpcg_feedback_boost_factor,
            source_ki_id=ki_id,
        )
        session.add(signal)
        session.flush()

        # Update learned preferences
        if ki.game_id:
            update_learned_preferences(
                session, user_id,
                preferred_games=[ki.game_id],
            )

        # Propagate to similar KIs
        count = self._propagate_to_similar(
            session, user_id, ki_id,
            adjustment=settings.gpcg_feedback_boost_factor,
            threshold=settings.gpcg_feedback_similarity_threshold,
        )

        log.info(f"FeedbackPropagator: manual add of KI {ki_id} propagated to {count} similar KIs")
        return count

    def record_production(
        self,
        session: Session,
        user_id: int,
        video_id: int,
    ) -> None:
        """Record a production event and update production history.

        Called after a video is successfully produced. Updates the channel's
        production_history_summary with the new video count and top games.

        Does NOT propagate to KIs (production is a positive signal but
        doesn't directly affect other KIs' scores — that's for YouTube
        Analytics in a future phase).
        """
        settings = get_settings()
        if not settings.gpcg_feedback_loop_enabled:
            return

        video = session.get(Video, video_id)
        if not video:
            return

        # Record the signal
        signal = EditorialSignal(
            user_id=user_id,
            signal_type=SIGNAL_PRODUCTION_HISTORY,
            signal_value=1.0,
            source_video_id=video_id,
        )
        session.add(signal)

        # Update production history summary
        from sqlalchemy import func
        total_count = session.execute(
            select(func.count(Video.id)).where(Video.user_id == user_id)
        ).scalar_one()

        # Get top games by video count
        top_games_rows = session.execute(
            select(Video.game_id, func.count(Video.id).label("cnt"))
            .where(Video.user_id == user_id, Video.game_id.isnot(None))
            .group_by(Video.game_id)
            .order_by(func.count(Video.id).desc())
            .limit(5)
        ).all()

        top_games = [
            {"game_id": row.game_id, "video_count": row.cnt}
            for row in top_games_rows
        ]

        update_production_history(
            session, user_id,
            total_videos=total_count,
            top_games=top_games,
        )

        log.info(f"FeedbackPropagator: production recorded for video {video_id}, user {user_id}")

    def decay_feedback_adjustments(self, session: Session) -> int:
        """Decay all feedback_adjustment values toward zero.

        Called periodically (e.g., in the worker after lifecycle updates).
        This ensures that feedback adjustments are temporary — they fade over
        time, allowing the system to re-explore topics that were previously
        penalized.

        Decay factor: FEEDBACK_DECAY_FACTOR (0.95 = 5% fade per cycle).
        After ~14 cycles, half the adjustment is gone.

        Returns the number of KIs updated.
        """
        kis = session.execute(
            select(KnowledgeItem).where(
                KnowledgeItem.feedback_adjustment != 0.0,
            )
        ).scalars().all()

        count = 0
        for ki in kis:
            new_adj = ki.feedback_adjustment * FEEDBACK_DECAY_FACTOR
            # Snap to zero if very small (avoid floating-point dust)
            if abs(new_adj) < 0.1:
                new_adj = 0.0
            if new_adj != ki.feedback_adjustment:
                ki.feedback_adjustment = new_adj
                count += 1

        if count > 0:
            session.flush()
            log.info(f"FeedbackPropagator: decayed feedback adjustments for {count} KIs")
        return count

    def cleanup_old_signals(self, session: Session, days: int = 90) -> int:
        """Delete editorial signals older than `days`.

        Prevents the editorial_signals table from growing unbounded.
        Called periodically from the worker.

        Returns the number of signals deleted.
        """
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        old_signals = session.execute(
            select(EditorialSignal).where(
                EditorialSignal.created_at < cutoff
            )
        ).scalars().all()

        count = 0
        for signal in old_signals:
            session.delete(signal)
            count += 1

        if count > 0:
            session.flush()
            log.info(f"FeedbackPropagator: cleaned up {count} old editorial signals")
        return count

    # ── Internal: propagation via embeddings ───────────────────────────────

    def _propagate_to_similar(
        self,
        session: Session,
        user_id: int,
        source_ki_id: int,
        adjustment: float,
        threshold: float,
    ) -> int:
        """Propagate an adjustment to KIs similar to the source.

        Uses embedding cosine similarity. KIs with similarity > threshold
        receive a proportional adjustment (scaled by similarity).

        SCOPE: Only propagates to KIs owned by this user (user_id = user_id).
        Public/shared KIs (user_id = NULL) are NEVER mutated by individual
        user feedback — editorial_score is a global quality layer, and
        per-channel feedback must not contaminate it.

        CAP: The cumulative adjustment per KI is capped at ±MAX_CUMULATIVE_ADJUSTMENT
        to prevent death spirals (e.g., rejecting 5 similar KIs shouldn't
        permanently exclude a topic).

        Args:
            adjustment: negative for penalty, positive for boost

        Returns:
            Number of KIs adjusted.
        """
        source_embedding = get_knowledge_item_embedding(session, source_ki_id)
        if not source_embedding:
            log.debug(f"No embedding for KI {source_ki_id}, skipping propagation")
            return 0

        # Only propagate to KIs owned by this user (not public/shared)
        kis = session.execute(
            select(KnowledgeItem).where(
                KnowledgeItem.status == KnowledgeItemStatus.fresh.value,
                KnowledgeItem.user_id == user_id,
                KnowledgeItem.id != source_ki_id,
            )
        ).scalars().all()

        count = 0
        for ki in kis:
            ki_embedding = get_knowledge_item_embedding(session, ki.id)
            if not ki_embedding:
                continue

            sim = cosine_similarity(source_embedding, ki_embedding)
            if sim > threshold:
                # Scale adjustment by similarity (more similar = more adjustment)
                scaled = adjustment * (sim - 0.5) * 2  # normalize 0.5–1.0 → 0.0–1.0

                # Cap cumulative adjustment per KI to prevent death spirals.
                # feedback_adjustment tracks the total adjustment applied.
                cumulative = ki.feedback_adjustment or 0.0
                new_cumulative = cumulative + scaled

                # If we'd exceed the cap, clamp the adjustment
                if new_cumulative > MAX_CUMULATIVE_ADJUSTMENT:
                    scaled = MAX_CUMULATIVE_ADJUSTMENT - cumulative
                elif new_cumulative < -MAX_CUMULATIVE_ADJUSTMENT:
                    scaled = -MAX_CUMULATIVE_ADJUSTMENT - cumulative

                if abs(scaled) < 0.01:
                    continue  # already at cap, skip

                ki.editorial_score = max(0.0, min(100.0, ki.editorial_score + scaled))
                ki.feedback_adjustment = cumulative + scaled
                count += 1

        if count > 0:
            session.flush()

        return count
