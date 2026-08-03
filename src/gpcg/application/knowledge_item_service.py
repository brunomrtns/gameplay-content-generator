"""Knowledge Item service — CRUD, scoring, and queries (V2).

Provides the service layer for KnowledgeItems: creation, retrieval,
editorial scoring, rejection, and stats.

See ARCHITECTURE_V2.md §7 (Knowledge Items e Content Intelligence).
"""

from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.domain.models import (
    ContentScope,
    Game,
    KnowledgeItem,
    KnowledgeItemSource,
    KnowledgeItemStatus,
    KnowledgeItemType,
)
from gpcg.infrastructure.llm import LLMClient, LLMError
from gpcg.logging import get_logger

log = get_logger(__name__)


# ── CRUD ──────────────────────────────────────────────────────────────────────


def get_by_id(session: Session, item_id: int) -> Optional[KnowledgeItem]:
    """Get a KnowledgeItem by ID."""
    return session.get(KnowledgeItem, item_id)


def list_items(
    session: Session,
    *,
    game_id: Optional[int] = None,
    item_type: Optional[str] = None,
    status: Optional[str] = None,
    user_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
    min_score: float = 0.0,
) -> list[KnowledgeItem]:
    """List KnowledgeItems with optional filters."""
    stmt = select(KnowledgeItem).order_by(KnowledgeItem.editorial_score.desc())

    if game_id is not None:
        stmt = stmt.where(KnowledgeItem.game_id == game_id)
    if item_type:
        stmt = stmt.where(KnowledgeItem.item_type == item_type)
    if status:
        stmt = stmt.where(KnowledgeItem.status == status)
    if user_id is not None:
        # Include user-specific + global (user_id IS NULL) items
        stmt = stmt.where(
            (KnowledgeItem.user_id == user_id) | (KnowledgeItem.user_id.is_(None))
        )
    if min_score > 0:
        stmt = stmt.where(KnowledgeItem.editorial_score >= min_score)

    stmt = stmt.offset(offset).limit(limit)
    return list(session.execute(stmt).scalars().all())


def reject_item(session: Session, item_id: int) -> bool:
    """Mark a KnowledgeItem as rejected. Returns True if updated."""
    item = session.get(KnowledgeItem, item_id)
    if not item:
        return False
    item.status = KnowledgeItemStatus.rejected.value
    session.flush()
    return True


def mark_as_used(session: Session, item_id: int) -> bool:
    """Mark a KnowledgeItem as used (after a video is generated from it)."""
    item = session.get(KnowledgeItem, item_id)
    if not item:
        return False
    item.status = KnowledgeItemStatus.used.value
    session.flush()
    return True


def get_stats(session: Session, *, user_id: Optional[int] = None) -> dict:
    """Get statistics about the KnowledgeItem bank."""
    base_filter = []
    if user_id is not None:
        base_filter.append(
            (KnowledgeItem.user_id == user_id) | (KnowledgeItem.user_id.is_(None))
        )

    # Total count
    total = session.execute(
        select(func.count(KnowledgeItem.id)).where(*base_filter)
    ).scalar() or 0

    # By type
    by_type = {}
    type_counts = session.execute(
        select(KnowledgeItem.item_type, func.count(KnowledgeItem.id))
        .where(*base_filter)
        .group_by(KnowledgeItem.item_type)
    ).all()
    for t, c in type_counts:
        by_type[t] = c

    # By status
    by_status = {}
    status_counts = session.execute(
        select(KnowledgeItem.status, func.count(KnowledgeItem.id))
        .where(*base_filter)
        .group_by(KnowledgeItem.status)
    ).all()
    for s, c in status_counts:
        by_status[s] = c

    # Fresh count (available for content planning)
    fresh = by_status.get(KnowledgeItemStatus.fresh.value, 0)

    # By source
    by_source = {}
    source_counts = session.execute(
        select(KnowledgeItem.source_type, func.count(KnowledgeItem.id))
        .where(*base_filter)
        .group_by(KnowledgeItem.source_type)
    ).all()
    for s, c in source_counts:
        by_source[s] = c

    return {
        "total": total,
        "fresh": fresh,
        "by_type": by_type,
        "by_status": by_status,
        "by_source": by_source,
    }


# ── Editorial Scoring ─────────────────────────────────────────────────────────


def score_knowledge_item(item: KnowledgeItem, llm: LLMClient) -> float:
    """Score a KnowledgeItem for editorial potential (0-100).

    Uses the LLM to evaluate the item across editorial dimensions,
    reusing the methodology from CuriosityScorer (curiosity_gap,
    surprise_potential, retention_potential, familiarity, insight_quality)
    adapted for external content.

    Returns the editorial_score (0-100).
    """
    prompt = _build_scoring_prompt(item)

    try:
        response = llm.generate(prompt, temperature=0.3, max_tokens=512)
        score = _parse_score_response(response)
        item.editorial_score = score
        return score
    except (LLMError, Exception) as e:
        log.warning(f"Scoring failed for KnowledgeItem #{item.id}: {e}")
        # Default to a neutral score on failure
        item.editorial_score = 30.0
        return 30.0


def score_all_fresh(session: Session, llm: LLMClient, *, limit: int = 50) -> int:
    """Score all fresh KnowledgeItems that have score=0. Returns count scored."""
    items = session.execute(
        select(KnowledgeItem)
        .where(
            KnowledgeItem.status == KnowledgeItemStatus.fresh.value,
            KnowledgeItem.editorial_score == 0.0,
        )
        .limit(limit)
    ).scalars().all()

    count = 0
    for item in items:
        score_knowledge_item(item, llm)
        count += 1

    if count > 0:
        session.flush()
        log.info(f"Scored {count} KnowledgeItems")
    return count


def _build_scoring_prompt(item: KnowledgeItem) -> str:
    """Build the LLM prompt for editorial scoring."""
    return (
        "Você é um editor de conteúdo gaming. Avalie o potencial editorial "
        "desta ideia de conteúdo em uma escala de 0-100.\n\n"
        f"Título: {item.title}\n"
        f"Conteúdo: {item.content[:500]}\n"
        f"Tipo: {item.item_type}\n"
        f"Fonte: {item.source_type}\n\n"
        "Considere:\n"
        "- Curiosidade: Esta ideia cria um gap de informação que o espectador quer preencher?\n"
        "- Surpresa: O conteúdo tem potencial de surpreender o espectador?\n"
        "- Retenção: O conteúdo consegue segurar a atenção até o final?\n"
        "- Familiaridade: O tema é reconhecível para o público gaming?\n"
        "- Insight: O conteúdo oferece uma perspectiva nova ou reveladora?\n\n"
        "Responda APENAS com um número de 0 a 100, sem texto adicional."
    )


def _parse_score_response(response: str) -> float:
    """Parse the LLM response to extract a score (0-100)."""
    if not response:
        return 30.0
    # Try to extract a number from the response
    import re
    numbers = re.findall(r"\d+(?:\.\d+)?", response.strip())
    if numbers:
        score = float(numbers[0])
        return max(0.0, min(100.0, score))
    return 30.0


# ── Content Ideas Query (unified Facts + KnowledgeItems) ──────────────────────


def get_content_ideas(
    session: Session,
    game_id: int,
    *,
    user_id: Optional[int] = None,
    scope: str = ContentScope.game.value,
    limit: int = 20,
) -> list[dict]:
    """Get unified content ideas from KnowledgeItems for a game.

    V2: This queries KnowledgeItems filtered by scope. Facts are handled
    separately by ContentPlanningService (this is the KnowledgeItem half).

    Scopes:
    - game: only items with game_id == this game
    - franchise: items from games with the same franchise
    - developer: items from games with the same developer

    Returns list of dicts: {id, title, content, item_type, source_type,
    editorial_score, game_id, source_url}
    """
    game = session.get(Game, game_id)
    if not game:
        return []

    stmt = (
        select(KnowledgeItem)
        .where(KnowledgeItem.status == KnowledgeItemStatus.fresh.value)
        .order_by(KnowledgeItem.editorial_score.desc())
    )

    if scope == ContentScope.game.value or not game:
        stmt = stmt.where(KnowledgeItem.game_id == game_id)
    elif scope == ContentScope.franchise.value and game.franchise:
        # Items denormalized with the same franchise
        stmt = stmt.where(KnowledgeItem.franchise == game.franchise)
    elif scope == ContentScope.developer.value and game.developer:
        # Items denormalized with the same developer
        stmt = stmt.where(KnowledgeItem.developer == game.developer)
    else:
        # Fallback to game scope if franchise/developer not available
        stmt = stmt.where(KnowledgeItem.game_id == game_id)

    # Filter by user (include global items)
    if user_id is not None:
        stmt = stmt.where(
            (KnowledgeItem.user_id == user_id) | (KnowledgeItem.user_id.is_(None))
        )

    # Filter by min editorial score
    settings = get_settings()
    min_score = settings.gpcg_content_min_editorial_score
    if min_score > 0:
        stmt = stmt.where(KnowledgeItem.editorial_score >= min_score)

    stmt = stmt.limit(limit)
    items = session.execute(stmt).scalars().all()

    return [
        {
            "id": item.id,
            "title": item.title,
            "content": item.content,
            "item_type": item.item_type,
            "source_type": item.source_type,
            "editorial_score": item.editorial_score,
            "game_id": item.game_id,
            "source_url": item.source_url,
        }
        for item in items
    ]
