"""Domain reset service — destructive channel domain switch.

When a user switches their channel's content domain (e.g., Games → Kids),
this service performs a complete reset of the channel's internal production
state. The operation is:

- **Explicit**: only triggered via the dedicated API endpoint with confirmation.
- **Destructive**: deletes all domain-specific data (media, jobs, content,
  knowledge) for the channel.
- **YouTube-independent**: does NOT touch the YouTube connection or
  already-published videos. YouTube is orthogonal to domain.
- **Worker-aware**: creates cleanup jobs so workers delete physical files
  from their local storage.
- **Transactional**: DB changes are committed atomically; physical file
  deletions are best-effort with logging.

The reset flow:
  1. Cancel all queued/running jobs for the user.
  2. Delete all non-published videos (files + DB records).
  3. Delete all content plans, scripts, facts, documents for the user.
  4. Delete all knowledge items + embeddings for the user.
  5. Delete all Games-specific data (games, gameplay sources, assets, events,
     clip usage, aliases) for the user.
  6. Create cleanup_gameplay jobs for each gameplay source so workers
     delete physical files.
  7. Reset ChannelProfile domain-specific fields.
  8. Set the new domain on ChannelProfile.
  9. Pause automation.

Published videos (status=published with youtube_video_id) are preserved
because they exist externally on YouTube and are not GPCG-internal state.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from gpcg.core.models import (
    ChannelProfile,
    ContentDomain,
    Fact,
    ContentPlan,
    Script,
    Video,
    VideoStatus,
    Job,
    JobStatus,
    JobType,
    JobPriority,
    KnowledgeItem,
    KnowledgeItemEmbedding,
    KnowledgeItemUsage,
    ChannelProfileEmbedding,
    EditorialSignal,
    Document,
)
from gpcg.logging import get_logger

log = get_logger(__name__)

# Valid domain values (must match ContentDomain enum)
VALID_DOMAINS = {d.value for d in ContentDomain}


def reset_channel_domain(
    session: Session,
    user_id: int,
    new_domain: str,
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    """Reset a channel's domain, destroying all domain-specific state.

    Args:
        session: SQLAlchemy session (transaction is managed by caller).
        user_id: The user whose channel is being reset.
        new_domain: The target domain (must be a valid ContentDomain value).
        confirm: Must be True to execute. Safety guard.

    Returns:
        Summary dict with counts of deleted items and cleanup jobs created.

    Raises:
        ValueError: If new_domain is invalid or confirm is False.
    """
    if not confirm:
        raise ValueError("Domain reset requires explicit confirmation (confirm=True)")
    if new_domain not in VALID_DOMAINS:
        raise ValueError(
            f"Invalid domain '{new_domain}'. Valid domains: {sorted(VALID_DOMAINS)}"
        )

    summary: dict[str, Any] = {
        "user_id": user_id,
        "old_domain": None,
        "new_domain": new_domain,
        "jobs_cancelled": 0,
        "videos_deleted": 0,
        "videos_preserved_published": 0,
        "content_plans_deleted": 0,
        "scripts_deleted": 0,
        "facts_deleted": 0,
        "documents_deleted": 0,
        "knowledge_items_deleted": 0,
        "cleanup_jobs_created": 0,
        "games_deleted": 0,
        "gameplay_sources_deleted": 0,
    }

    # ── 0. Load channel profile and record old domain ──────────────────────
    profile = session.query(ChannelProfile).filter(
        ChannelProfile.user_id == user_id
    ).first()
    if profile:
        summary["old_domain"] = profile.domain

    # ── 1. Cancel all queued/running jobs for the user ──────────────────────
    active_jobs = session.query(Job).filter(
        Job.user_id == user_id,
        Job.status.in_([JobStatus.queued.value, JobStatus.running.value]),
    ).all()
    for job in active_jobs:
        job.status = JobStatus.cancelled.value
        job.error = "Cancelled by domain reset"
        summary["jobs_cancelled"] += 1
    session.flush()

    # ── 2. Delete non-published videos (files + DB) ────────────────────────
    # Published videos (status=published AND youtube_video_id set) are
    # preserved — they exist externally on YouTube.
    videos = session.query(Video).filter(Video.user_id == user_id).all()
    from gpcg.config import get_settings
    from pathlib import Path

    settings = get_settings()
    for v in videos:
        is_published = (
            v.status == VideoStatus.published.value and bool(v.youtube_video_id)
        )
        if is_published:
            summary["videos_preserved_published"] += 1
            continue
        # Delete physical files (best-effort)
        for path_attr in ("file_path", "thumbnail_path"):
            raw = getattr(v, path_attr, None) or ""
            if not raw:
                continue
            p = Path(raw)
            if p.is_file():
                try:
                    p.unlink()
                except OSError as e:
                    log.warning(f"Failed to delete {path_attr} {p}: {e}")
        if v.storage_key:
            for d in [settings.videos_dir, settings.temp_uploads_dir]:
                p = d / v.storage_key
                if p.exists():
                    try:
                        p.unlink()
                    except OSError as e:
                        log.warning(f"Failed to delete video by storage_key {p}: {e}")
                        break
        session.delete(v)
        summary["videos_deleted"] += 1
    session.flush()

    # ── 3. Delete content plans + scripts ──────────────────────────────────
    plans = session.query(ContentPlan).filter(ContentPlan.user_id == user_id).all()
    summary["content_plans_deleted"] = len(plans)
    for plan in plans:
        session.delete(plan)
    session.flush()

    # ── 4. Delete facts ────────────────────────────────────────────────────
    facts = session.query(Fact).filter(Fact.user_id == user_id).all()
    summary["facts_deleted"] = len(facts)
    for fact in facts:
        session.delete(fact)
    session.flush()

    # ── 5. Delete documents (physical files + DB) ──────────────────────────
    documents = session.query(Document).filter(Document.user_id == user_id).all()
    summary["documents_deleted"] = len(documents)
    for doc in documents:
        if doc.file_path:
            p = Path(doc.file_path)
            if p.exists():
                try:
                    p.unlink()
                except OSError as e:
                    log.warning(f"Failed to delete document {p}: {e}")
        session.delete(doc)
    session.flush()

    # ── 6. Delete knowledge items + embeddings + usage ─────────────────────
    ki_ids = [
        ki.id for ki in session.query(KnowledgeItem).filter(
            KnowledgeItem.user_id == user_id
        ).all()
    ]
    summary["knowledge_items_deleted"] = len(ki_ids)
    if ki_ids:
        session.query(KnowledgeItemEmbedding).filter(
            KnowledgeItemEmbedding.item_id.in_(ki_ids)
        ).delete(synchronize_session=False)
        session.query(KnowledgeItemUsage).filter(
            KnowledgeItemUsage.knowledge_item_id.in_(ki_ids)
        ).delete(synchronize_session=False)
        session.query(KnowledgeItem).filter(
            KnowledgeItem.id.in_(ki_ids)
        ).delete(synchronize_session=False)
    session.flush()

    # ── 7. Delete channel profile embeddings + editorial signals ───────────
    session.query(ChannelProfileEmbedding).filter(
        ChannelProfileEmbedding.user_id == user_id
    ).delete(synchronize_session=False)
    session.query(EditorialSignal).filter(
        EditorialSignal.user_id == user_id
    ).delete(synchronize_session=False)
    session.flush()

    # ── 8. Delete Games-specific data (if old domain was games) ────────────
    # This is the domain-specific cleanup. For Games, we delete:
    # - GameplayClipUsage, GameplayEventEmbedding, GameplayEvent
    # - GameplayAsset, GameplayDownload
    # - GameplaySource (soft-delete + cleanup job for worker files)
    # - GameAlias, Game
    old_domain = summary["old_domain"] or ContentDomain.games.value
    if old_domain == ContentDomain.games.value:
        summary.update(_delete_games_domain_data(session, user_id))
    session.flush()

    # ── 9. Reset ChannelProfile domain-specific fields + set new domain ────
    if profile:
        profile.domain = new_domain
        # Reset domain-specific configuration/learning fields
        profile.gameplay_driven_collection = True
        profile.learned_preferences = {}
        profile.production_history_summary = {}
        profile.content_type_affinity = {}
        profile.editorial_keywords = []
        profile.custom_feeds = []
        # Keep free-text fields (niche, tone, etc.) — user may want to
        # reconfigure them, but they're not domain-specific per se.
    session.flush()

    # ── 10. Pause automation ───────────────────────────────────────────────
    from gpcg.core.models import Automation
    automation = session.query(Automation).filter(Automation.user_id == user_id).first()
    if automation and automation.status != "paused":
        automation.status = "paused"

    log.info(
        f"Domain reset for user #{user_id}: {old_domain} → {new_domain}. "
        f"Cancelled {summary['jobs_cancelled']} jobs, "
        f"deleted {summary['videos_deleted']} videos, "
        f"{summary['content_plans_deleted']} plans, "
        f"{summary['facts_deleted']} facts, "
        f"{summary['knowledge_items_deleted']} knowledge items, "
        f"{summary['gameplay_sources_deleted']} gameplay sources. "
        f"Created {summary['cleanup_jobs_created']} cleanup jobs. "
        f"Preserved {summary['videos_preserved_published']} published videos."
    )

    return summary


def _delete_games_domain_data(session: Session, user_id: int) -> dict[str, Any]:
    """Delete all Games-specific data for a user.

    Creates cleanup_gameplay jobs so workers delete physical files.
    Returns a summary dict with counts.
    """
    import uuid as _uuid
    from gpcg.domains.games.models import (
        Game,
        GameAlias,
        GameplaySource,
        GameplayDownload,
        GameplayAsset,
        GameplayClipUsage,
        GameplayEvent,
        GameplayEventEmbedding,
    )

    result = {
        "games_deleted": 0,
        "gameplay_sources_deleted": 0,
        "cleanup_jobs_created": 0,
    }

    # Get all gameplay sources for the user (need filenames for cleanup jobs)
    sources = session.query(GameplaySource).filter(
        GameplaySource.user_id == user_id
    ).all()

    # Create cleanup jobs for each source so workers delete physical files
    for source in sources:
        cleanup_job = Job(
            job_uuid=str(_uuid.uuid4()),
            type=JobType.cleanup_gameplay.value,
            user_id=user_id,
            gameplay_source_id=source.id,
            status=JobStatus.queued.value,
            priority=JobPriority.high.value,
            artifacts={"source_id": source.id, "filename": source.filename},
        )
        session.add(cleanup_job)
        result["cleanup_jobs_created"] += 1
    session.flush()

    # Delete gameplay-related data (order matters for FK constraints)
    source_ids = [s.id for s in sources]

    if source_ids:
        # Delete clip usage
        session.query(GameplayClipUsage).filter(
            GameplayClipUsage.source_id.in_(source_ids)
        ).delete(synchronize_session=False)

        # Delete event embeddings
        event_ids = [
            e.id for e in session.query(GameplayEvent).filter(
                GameplayEvent.source_id.in_(source_ids)
            ).all()
        ]
        if event_ids:
            session.query(GameplayEventEmbedding).filter(
                GameplayEventEmbedding.event_id.in_(event_ids)
            ).delete(synchronize_session=False)
            session.query(GameplayEvent).filter(
                GameplayEvent.id.in_(event_ids)
            ).delete(synchronize_session=False)

        # Delete assets
        session.query(GameplayAsset).filter(
            GameplayAsset.source_id.in_(source_ids)
        ).delete(synchronize_session=False)

        # Delete downloads
        session.query(GameplayDownload).filter(
            GameplayDownload.source_id.in_(source_ids)
        ).delete(synchronize_session=False)

    result["gameplay_sources_deleted"] = len(sources)

    # Delete gameplay sources (hard delete — they're being reset)
    if source_ids:
        session.query(GameplaySource).filter(
            GameplaySource.id.in_(source_ids)
        ).delete(synchronize_session=False)
    session.flush()

    # Delete games owned by the user
    games = session.query(Game).filter(Game.user_id == user_id).all()
    result["games_deleted"] = len(games)
    for game in games:
        # GameAlias has cascade="all, delete-orphan" so aliases are auto-deleted
        session.delete(game)
    session.flush()

    return result
