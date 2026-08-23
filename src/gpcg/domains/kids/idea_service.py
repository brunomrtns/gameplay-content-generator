"""KidsIdea service — business logic for KidsIdeas.

This module provides the core operations on KidsIdea entities:
- creation (manual + from discovery)
- content hash computation (deduplication)
- duplicate detection (against existing ideas + topics)
- lifecycle transitions (evaluate, queue, reject, convert)
- conversion to KidsTopic (with traceability link)

It does NOT handle:
- Discovery (AI ideation, topic library) — that's in ``discovery.py``
- Queue management — that's in the API routes (``kids_idea_routes.py``)
- Automation — that's in the automation strategy (Phase 4)

The service is domain-specific to Kids. It does not share code with
``knowledge_item_service.py`` (Games) because the semantics are different:
KnowledgeItem is external collected content; KidsIdea is an editorial
opportunity.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from gpcg.core.models import ChannelProfile, ContentDomain
from gpcg.domains.kids.models import (
    KidsIdea,
    KidsIdeaSource,
    KidsIdeaStatus,
    KidsTopic,
)
from gpcg.logging import get_logger

log = get_logger(__name__)


# ── Content hash (deduplication) ─────────────────────────────────────────────


def normalize_for_hash(text: str) -> str:
    """Normalize text for hashing: lowercase, strip accents, collapse whitespace.

    This makes "Por que os polvos têm 3 corações?" and
    "porque os polvos tem 3 coracoes" produce the same hash.
    """
    # Lowercase
    text = text.lower().strip()
    # Remove accents (NFD decomposition + strip combining chars)
    text = re.sub(r"[\u0300-\u036f]", "", unicodedata.normalize("NFKD", text))
    # Replace digits with word equivalents for common cases
    # (not exhaustive — just the most common in kids content)
    digit_map = {"0": "zero", "1": "um", "2": "dois", "3": "tres",
                 "4": "quatro", "5": "cinco", "6": "seis", "7": "sete",
                 "8": "oito", "9": "nove"}
    for digit, word in digit_map.items():
        text = text.replace(digit, word)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    # Remove punctuation
    text = re.sub(r"[^\w\s]", "", text)
    return text.strip()


def compute_content_hash(title: str) -> str:
    """Compute SHA256 of normalized title for deduplication."""
    normalized = normalize_for_hash(title)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def title_similarity(title_a: str, title_b: str) -> float:
    """Compute word-level Jaccard similarity between two titles.

    Returns 0.0–1.0. Uses the same normalization as content_hash
    (lowercase, no accents, no punctuation) plus word tokenization.

    This catches paraphrases like:
    - "Por que o polvo tem três corações?" vs
      "Você sabia que o polvo possui três corações?"
    → shared words: polvo, tres, coracoes → similarity ~0.33

    Not as precise as embeddings, but lightweight and sufficient for MVP.
    Embeddings can be added later via EmbeddingService without changing
    this function's interface.
    """
    words_a = set(normalize_for_hash(title_a).split())
    words_b = set(normalize_for_hash(title_b).split())
    # Remove very common stop words that don't carry semantic meaning
    stop_words = {
        "o", "a", "os", "as", "um", "uma", "de", "do", "da", "dos", "das",
        "e", "ou", "que", "por", "para", "com", "sem", "em", "no", "na",
        "nos", "nas", "se", "sao", "eh", "voce", "sabia", "voce sabia",
        "voce", "sabia", "porque", "como", "quando", "onde", "qual", "quais",
        "tem", "temum", "temuma", "muito", "muita", "isso", "isto", "aquilo",
    }
    words_a -= stop_words
    words_b -= stop_words
    if not words_a and not words_b:
        return 1.0
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def is_similar_to_existing(
    session: Session,
    user_id: int,
    title: str,
    *,
    threshold: float = 0.4,
    exclude_idea_id: Optional[int] = None,
) -> Optional[KidsIdea]:
    """Check if a title is similar to any existing KidsIdea.

    Returns the similar idea if found, None otherwise.
    Uses Jaccard word similarity (threshold default 0.4).
    """
    ideas = session.execute(
        select(KidsIdea).where(
            KidsIdea.user_id == user_id,
            KidsIdea.status != KidsIdeaStatus.rejected.value,
        )
    ).scalars().all()
    for idea in ideas:
        if exclude_idea_id and idea.id == exclude_idea_id:
            continue
        sim = title_similarity(title, idea.title)
        if sim >= threshold:
            return idea
    return None


# ── Lifecycle ────────────────────────────────────────────────────────────────


def is_terminal(status: str) -> bool:
    """Check if a status is terminal (no further transitions)."""
    return status in (
        KidsIdeaStatus.converted.value,
        KidsIdeaStatus.rejected.value,
        KidsIdeaStatus.expired.value,
    )


def can_transition(from_status: str, to_status: str) -> bool:
    """Check if a lifecycle transition is valid.

    Valid transitions:
        discovered → evaluated, rejected
        evaluated → queued, rejected, expired
        queued → converted, rejected, evaluated (back to pool)
        converted → (terminal)
        rejected → (terminal)
        expired → (terminal)
    """
    valid: dict[str, set[str]] = {
        KidsIdeaStatus.discovered.value: {
            KidsIdeaStatus.evaluated.value,
            KidsIdeaStatus.rejected.value,
        },
        KidsIdeaStatus.evaluated.value: {
            KidsIdeaStatus.queued.value,
            KidsIdeaStatus.rejected.value,
            KidsIdeaStatus.expired.value,
        },
        KidsIdeaStatus.queued.value: {
            KidsIdeaStatus.converted.value,
            KidsIdeaStatus.rejected.value,
            KidsIdeaStatus.evaluated.value,  # back to pool if removed from queue
        },
    }
    if is_terminal(from_status):
        return False
    return to_status in valid.get(from_status, set())


# ── CRUD ─────────────────────────────────────────────────────────────────────


def create_idea(
    session: Session,
    user_id: int,
    title: str,
    description: str = "",
    category: str = "general",
    suggested_age_range: str = "3-6",
    source: str = KidsIdeaSource.manual.value,
    source_metadata: Optional[dict] = None,
    skip_dedup: bool = False,
) -> KidsIdea | None:
    """Create a new KidsIdea.

    Computes content_hash and checks for duplicates before creating.
    Returns None if a duplicate exists (same content_hash).

    Args:
        skip_dedup: if True, skip duplicate check (for testing/bulk import).
    """
    content_hash = compute_content_hash(title)

    if not skip_dedup:
        # Layer 1: exact hash match
        existing = session.execute(
            select(KidsIdea).where(KidsIdea.content_hash == content_hash)
        ).scalar_one_or_none()
        if existing:
            log.info(
                f"kids_idea.duplicate_exact: title='{title[:50]}', "
                f"hash={content_hash[:8]}, existing=#{existing.id}"
            )
            return None

        # Layer 2: fuzzy similarity (catches paraphrases)
        similar = is_similar_to_existing(session, user_id, title, threshold=0.4)
        if similar:
            log.info(
                f"kids_idea.duplicate_similar: title='{title[:50]}', "
                f"similar=#{similar.id} '{similar.title[:50]}'"
            )
            return None

    idea = KidsIdea(
        user_id=user_id,
        title=title.strip(),
        description=description.strip() if description else "",
        category=category,
        suggested_age_range=suggested_age_range,
        source=source,
        source_metadata=source_metadata or {},
        content_hash=content_hash,
        status=KidsIdeaStatus.discovered.value,
    )
    session.add(idea)
    session.flush()
    log.info(
        f"kids_idea.created: #{idea.id} title='{title[:50]}' "
        f"source={source} category={category}"
    )
    return idea


def get_by_id(session: Session, idea_id: int) -> Optional[KidsIdea]:
    """Get a KidsIdea by ID."""
    return session.get(KidsIdea, idea_id)


def list_ideas(
    session: Session,
    user_id: int,
    *,
    status: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[KidsIdea]:
    """List KidsIdeas for a user with optional filters."""
    query = session.query(KidsIdea).filter(KidsIdea.user_id == user_id)
    if status:
        query = query.filter(KidsIdea.status == status)
    if category:
        query = query.filter(KidsIdea.category == category)
    # Exclude terminal by default? No — let caller filter by status.
    return (
        query
        .order_by(KidsIdea.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def reject_idea(
    session: Session,
    idea_id: int,
    reason: str = "",
) -> bool:
    """Reject a KidsIdea. Returns True if rejected, False if not found or terminal."""
    idea = session.get(KidsIdea, idea_id)
    if not idea or is_terminal(idea.status):
        return False
    idea.status = KidsIdeaStatus.rejected.value
    idea.rejection_reason = reason
    session.flush()
    log.info(f"kids_idea.rejected: #{idea_id} reason='{reason[:100]}'")
    return True


def update_status(
    session: Session,
    idea_id: int,
    new_status: str,
) -> bool:
    """Update the status of a KidsIdea with lifecycle validation.

    Returns True if updated, False if transition is invalid or idea not found.
    """
    idea = session.get(KidsIdea, idea_id)
    if not idea:
        return False
    if not can_transition(idea.status, new_status):
        log.warning(
            f"kids_idea.invalid_transition: #{idea_id} "
            f"{idea.status} → {new_status}"
        )
        return False
    idea.status = new_status
    session.flush()
    return True


# ── Duplicate detection ──────────────────────────────────────────────────────


def is_duplicate_topic(session: Session, user_id: int, title: str) -> bool:
    """Check if a KidsTopic with a similar title already exists.

    Uses both content_hash (exact) and Jaccard word similarity (fuzzy).
    This prevents producing a video about the same topic twice.
    """
    content_hash = compute_content_hash(title)
    existing = session.execute(
        select(KidsTopic).where(
            KidsTopic.user_id == user_id,
        )
    ).scalars().all()
    for topic in existing:
        # Layer 1: exact hash match
        topic_hash = compute_content_hash(topic.title)
        if topic_hash == content_hash:
            return True
        # Layer 2: fuzzy similarity
        if title_similarity(title, topic.title) >= 0.4:
            return True
    return False


# ── Conversion: KidsIdea → KidsTopic ─────────────────────────────────────────


def convert_to_topic(
    session: Session,
    idea_id: int,
    *,
    editorial_intent: str = "curiosity",
    educational_goal: str = "general",
    description_override: Optional[str] = None,
) -> KidsTopic | None:
    """Convert a KidsIdea into a KidsTopic.

    Creates a KidsTopic with the idea's title, category, age_range, and
    description. Links the topic back to the idea via ``idea_id`` and
    marks the idea as ``converted``.

    Returns the new KidsTopic, or None if:
    - idea not found
    - idea is already converted
    - a duplicate topic already exists

    The user should upload story assets to the new topic before generating
    a video. This function does NOT create assets or trigger generation.
    """
    idea = session.get(KidsIdea, idea_id)
    if not idea:
        log.warning(f"kids_idea.convert: #{idea_id} not found")
        return None
    if idea.status == KidsIdeaStatus.converted.value and idea.topic_id:
        # Already converted — return existing topic
        return session.get(KidsTopic, idea.topic_id)

    # Check for duplicate topic
    if is_duplicate_topic(session, idea.user_id, idea.title):
        log.warning(
            f"kids_idea.convert: #{idea_id} duplicate topic exists "
            f"for title='{idea.title[:50]}'"
        )
        return None

    # Generate slug
    from gpcg.api.kids_routes import _slugify
    slug = _slugify(idea.title)

    # Create the topic
    topic = KidsTopic(
        user_id=idea.user_id,
        title=idea.title,
        slug=slug,
        category=idea.category,
        age_range=idea.suggested_age_range,
        description=description_override or idea.description,
        idea_id=idea.id,
        editorial_intent=editorial_intent,
        educational_goal=educational_goal,
        metadata_json={"source_idea": idea.id, "source": idea.source},
    )
    session.add(topic)
    session.flush()

    # Link idea → topic and mark converted
    idea.topic_id = topic.id
    idea.status = KidsIdeaStatus.converted.value
    session.flush()

    log.info(
        f"kids_idea.converted: #{idea.id} → topic #{topic.id} "
        f"title='{topic.title[:50]}'"
    )
    return topic


# ── Stats ────────────────────────────────────────────────────────────────────


def get_stats(session: Session, user_id: int) -> dict:
    """Get statistics about KidsIdeas for a user."""
    from sqlalchemy import func

    total = session.execute(
        select(func.count(KidsIdea.id)).where(KidsIdea.user_id == user_id)
    ).scalar_one()

    by_status: dict[str, int] = {}
    for status_val in [
        KidsIdeaStatus.discovered.value,
        KidsIdeaStatus.evaluated.value,
        KidsIdeaStatus.queued.value,
        KidsIdeaStatus.converted.value,
        KidsIdeaStatus.rejected.value,
        KidsIdeaStatus.expired.value,
    ]:
        count = session.execute(
            select(func.count(KidsIdea.id)).where(
                KidsIdea.user_id == user_id,
                KidsIdea.status == status_val,
            )
        ).scalar_one()
        by_status[status_val] = count

    return {
        "total": total,
        "by_status": by_status,
    }


# ── Expiration ───────────────────────────────────────────────────────────────


def expire_old_ideas(
    session: Session,
    user_id: int,
    max_age_days: int = 180,
) -> int:
    """Expire evaluated ideas older than max_age_days.

    Only expires ideas in 'evaluated' status (not queued or discovered).
    Returns the number of ideas expired.
    """
    now = datetime.now(timezone.utc)
    ideas = session.execute(
        select(KidsIdea).where(
            KidsIdea.user_id == user_id,
            KidsIdea.status == KidsIdeaStatus.evaluated.value,
        )
    ).scalars().all()

    expired = 0
    for idea in ideas:
        ref_date = idea.updated_at or idea.created_at
        if ref_date and ref_date.tzinfo is None:
            ref_date = ref_date.replace(tzinfo=timezone.utc)
        if ref_date:
            age_days = (now - ref_date).total_seconds() / 86400.0
            if age_days > max_age_days:
                idea.status = KidsIdeaStatus.expired.value
                expired += 1

    if expired:
        session.flush()
        log.info(f"kids_idea.expired: {expired} ideas for user {user_id}")
    return expired


# ── Queue reconciliation ─────────────────────────────────────────────────────


def reconcile_kids_queue(session: Session, user_id: int) -> int:
    """Auto-fill the Kids idea queue with top-scored evaluated ideas.

    Only runs when:
    - queue_mode == "automatic"
    - auto_fill_queue == True
    - queue length < max_queue_size

    Picks the highest-scoring evaluated ideas that are not already in the
    queue, not rejected, not expired, not converted, and not already
    produced (no duplicate topic).

    Returns the number of new entries added (0 if nothing changed).
    """
    from sqlalchemy.orm.attributes import flag_modified
    from gpcg.core.models import Automation

    auto = session.query(Automation).filter(Automation.user_id == user_id).first()
    if not auto:
        return 0

    cfg = dict(auto.config or {})
    queue_mode = cfg.get("kids_queue_mode", "automatic")
    if queue_mode != "automatic" or not cfg.get("kids_auto_fill_queue", False):
        return 0

    queue = cfg.get("kids_idea_queue", [])
    if not isinstance(queue, list):
        queue = []
    queue = [int(i) for i in queue if isinstance(i, (int, str)) and str(i).isdigit() or isinstance(i, int)]

    max_size = cfg.get("kids_max_queue_size", 10)
    if len(queue) >= max_size:
        return 0

    # Clean queue: remove ideas that are no longer valid
    cleaned_queue: list[int] = []
    for idea_id in queue:
        idea = session.get(KidsIdea, idea_id)
        if idea and idea.user_id == user_id and not is_terminal(idea.status):
            cleaned_queue.append(idea_id)
        elif idea and idea.status == KidsIdeaStatus.queued.value:
            # Keep queued ideas in the queue
            cleaned_queue.append(idea_id)

    # Find top-scored evaluated ideas not already in queue
    available = session.execute(
        select(KidsIdea).where(
            KidsIdea.user_id == user_id,
            KidsIdea.status == KidsIdeaStatus.evaluated.value,
            ~KidsIdea.id.in_(cleaned_queue) if cleaned_queue else True,
        ).order_by(KidsIdea.final_score.desc())
    ).scalars().all()

    # Filter out ideas that already have a duplicate topic
    valid_available: list[KidsIdea] = []
    for idea in available:
        if not is_duplicate_topic(session, user_id, idea.title):
            valid_available.append(idea)

    # Fill up to max_size
    slots = max_size - len(cleaned_queue)
    to_add = valid_available[:slots]

    for idea in to_add:
        cleaned_queue.append(idea.id)
        idea.status = KidsIdeaStatus.queued.value

    if len(cleaned_queue) != len(queue) or to_add:
        cfg["kids_idea_queue"] = cleaned_queue
        auto.config = cfg
        flag_modified(auto, "config")
        session.flush()
        added = len(to_add)
        if added:
            log.info(
                f"kids_idea.reconcile: auto-filled queue for user {user_id} "
                f"with {added} ideas (now {len(cleaned_queue)}/{max_size})"
            )
        return added

    return 0


def clean_kids_queue(session: Session, user_id: int) -> int:
    """Remove invalid ideas from the Kids idea queue.

    Removes ideas that are:
    - rejected
    - expired
    - converted (already became a topic)
    - not found
    - not owned by the user

    Returns the number of entries removed.
    """
    from sqlalchemy.orm.attributes import flag_modified
    from gpcg.core.models import Automation

    auto = session.query(Automation).filter(Automation.user_id == user_id).first()
    if not auto:
        return 0

    cfg = dict(auto.config or {})
    queue = cfg.get("kids_idea_queue", [])
    if not isinstance(queue, list):
        return 0

    original_len = len(queue)
    cleaned: list[int] = []
    for item in queue:
        if isinstance(item, int):
            idea_id = item
        elif isinstance(item, str) and item.isdigit():
            idea_id = int(item)
        else:
            continue

        idea = session.get(KidsIdea, idea_id)
        if not idea or idea.user_id != user_id:
            continue  # remove invalid
        if idea.status in (KidsIdeaStatus.rejected.value, KidsIdeaStatus.expired.value):
            continue  # remove rejected/expired
        if idea.status == KidsIdeaStatus.converted.value:
            continue  # remove converted
        cleaned.append(idea_id)

    if len(cleaned) != original_len:
        cfg["kids_idea_queue"] = cleaned
        auto.config = cfg
        flag_modified(auto, "config")
        session.flush()
        removed = original_len - len(cleaned)
        log.info(f"kids_idea.clean_queue: removed {removed} invalid entries for user {user_id}")
        return removed

    return 0
