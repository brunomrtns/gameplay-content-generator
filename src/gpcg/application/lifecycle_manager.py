"""Lifecycle Manager — freshness decay and stage transitions for KnowledgeItems.

The lifecycle is TYPED by item_type:
  - news decays fast (half-life: 2 days, archived after 14 days)
  - curiosity decays slowly (half-life: 90 days, archived after 365 days)
  - lore is evergreen (no decay, never archived)
  - fact decays medium (half-life: 180 days, archived after 365 days)

freshness_score = 0.5 ^ (age_days / half_life_days)
  - news with 2 days → 0.5
  - curiosity with 90 days → 0.5
  - lore → always 1.0

lifecycle_stage is ORTHOGONAL to status:
  - status: fresh | used | rejected (editorial state)
  - lifecycle_stage: fresh | aging | archived (temporal state)

A KI can be status=fresh + lifecycle_stage=aging (still available, but less fresh).

See docs/EDITORIAL_INTELLIGENCE_V2_PROPOSAL.md §10.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from gpcg.core.models import KnowledgeItem, KnowledgeItemStatus
from gpcg.logging import get_logger

log = get_logger(__name__)


# Decay configuration per item_type.
# half_life_days: days for freshness to drop to 0.5. None = evergreen.
# archive_after_days: days before moving to archived. None = never archive.
LIFECYCLE_DECAY: dict[str, dict] = {
    "news": {
        "half_life_days": 2,
        "archive_after_days": 14,
    },
    "curiosity": {
        "half_life_days": 90,
        "archive_after_days": 365,
    },
    "lore": {
        "half_life_days": None,      # evergreen
        "archive_after_days": None,  # never archive
    },
    "fact": {
        "half_life_days": 180,
        "archive_after_days": 365,
    },
}

# Default for unknown item_types
DEFAULT_DECAY = {
    "half_life_days": 30,
    "archive_after_days": 90,
}

# Freshness threshold for "aging" stage (below this = aging)
AGING_THRESHOLD = 0.3


def compute_freshness(ki: KnowledgeItem, now: Optional[datetime] = None) -> float:
    """Compute the freshness score for a KnowledgeItem (0.0–1.0).

    Uses the published_at date if available, otherwise collected_at.
    Lore and other evergreen types always return 1.0.
    """
    decay = LIFECYCLE_DECAY.get(ki.item_type, DEFAULT_DECAY)
    half_life = decay["half_life_days"]

    if half_life is None:
        return 1.0  # evergreen

    # Use published_at if available, otherwise collected_at
    ref_date = ki.published_at or ki.collected_at
    if ref_date is None:
        return 1.0  # no date → assume fresh

    if now is None:
        now = datetime.now(timezone.utc)

    # Make ref_date timezone-aware for comparison
    if ref_date.tzinfo is None:
        ref_date = ref_date.replace(tzinfo=timezone.utc)

    age_days = (now - ref_date).total_seconds() / 86400.0
    if age_days <= 0:
        return 1.0

    return 0.5 ** (age_days / half_life)


def determine_stage(ki: KnowledgeItem, now: Optional[datetime] = None) -> str:
    """Determine the lifecycle_stage for a KnowledgeItem.

    Returns: "fresh" | "aging" | "archived"
    """
    decay = LIFECYCLE_DECAY.get(ki.item_type, DEFAULT_DECAY)
    archive_after = decay["archive_after_days"]

    if archive_after is not None:
        ref_date = ki.published_at or ki.collected_at
        if ref_date is not None:
            if now is None:
                now = datetime.now(timezone.utc)
            if ref_date.tzinfo is None:
                ref_date = ref_date.replace(tzinfo=timezone.utc)
            age_days = (now - ref_date).total_seconds() / 86400.0
            if age_days > archive_after:
                return "archived"

    freshness = compute_freshness(ki, now)
    if freshness < AGING_THRESHOLD:
        return "aging"
    return "fresh"


class LifecycleManager:
    """Updates freshness_score and lifecycle_stage for KnowledgeItems.

    Designed to run as a periodic job (e.g., nightly) to keep the freshness
    scores current without recomputing on every read.
    """

    def update_all_fresh(self, session: Session, now: Optional[datetime] = None) -> int:
        """Update freshness_score and lifecycle_stage for all fresh KIs.

        Only updates KIs with status=fresh (used/rejected are terminal).
        Returns the number of KIs updated.
        """
        kis = session.execute(
            select(KnowledgeItem).where(
                KnowledgeItem.status == KnowledgeItemStatus.fresh.value
            )
        ).scalars().all()

        count = 0
        for ki in kis:
            new_freshness = compute_freshness(ki, now)
            new_stage = determine_stage(ki, now)

            # Only update if changed (avoid unnecessary writes)
            if ki.freshness_score != new_freshness or ki.lifecycle_stage != new_stage:
                ki.freshness_score = new_freshness
                ki.lifecycle_stage = new_stage
                count += 1

        if count > 0:
            session.flush()
            log.info(f"LifecycleManager: updated {count} KIs freshness/stage")
        return count

    def archive_old_items(self, session: Session, now: Optional[datetime] = None) -> int:
        """Move KIs past their archive_after_days to archived stage.

        Archived KIs are not deleted (that's cleanup_old_news's job) but they
        are excluded from the reconciler (lifecycle_stage=archived).
        Returns the number of KIs archived.
        """
        kis = session.execute(
            select(KnowledgeItem).where(
                KnowledgeItem.status == KnowledgeItemStatus.fresh.value,
                KnowledgeItem.lifecycle_stage != "archived",
            )
        ).scalars().all()

        count = 0
        for ki in kis:
            stage = determine_stage(ki, now)
            if stage == "archived":
                ki.lifecycle_stage = "archived"
                count += 1

        if count > 0:
            session.flush()
            log.info(f"LifecycleManager: archived {count} old KIs")
        return count
