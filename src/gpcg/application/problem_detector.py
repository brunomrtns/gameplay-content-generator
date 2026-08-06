"""Problem detector service — proactively detects inventory issues.

Detects problems that would otherwise only surface when the system tries
to use a resource and fails (e.g. "no gameplay assets available").

All detection is read-only — this service never modifies data.
Actions (cleanup, auto-fail) are left to the caller or the user.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gpcg.domain.models import (
    GameplayAsset,
    GameplayEvent,
    GameplaySource,
    IngestionStatus,
    Job,
    JobStatus,
    KnowledgeItem,
    KnowledgeItemStatus,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def detect_problems(db: Session, user_id: int) -> dict[str, Any]:
    """Detect inventory problems for a specific user.

    Returns a dict with categories of problems and their counts/details.
    All queries are scoped to the user (own sources + public sources).
    """
    problems: dict[str, Any] = {
        "sources_without_clips": [],
        "sources_without_events": [],
        "stuck_jobs": [],
        "rejected_kis_in_queue": [],
        "kis_without_gameplay": [],
        "summary": {
            "total": 0,
            "critical": 0,
            "warning": 0,
        },
    }

    # 1. Sources with status=ready but 0 GameplayAssets
    #    (would cause "no gameplay assets available" at render time)
    ready_sources = db.execute(
        select(GameplaySource).where(
            GameplaySource.user_id == user_id,
            GameplaySource.ingestion_status == IngestionStatus.ready.value,
            GameplaySource.ingestion_status != IngestionStatus.deleted.value,
        )
    ).scalars().all()

    for src in ready_sources:
        clip_count = db.execute(
            select(func.count(GameplayAsset.id)).where(
                GameplayAsset.source_id == src.id
            )
        ).scalar()
        if clip_count == 0:
            problems["sources_without_clips"].append({
                "source_id": src.id,
                "filename": src.filename,
                "game_id": src.game_id,
                "processing_status": src.processing_status,
            })

    # 2. Sources with status=ready but 0 GameplayEvents (not mapped)
    for src in ready_sources:
        event_count = db.execute(
            select(func.count(GameplayEvent.id)).where(
                GameplayEvent.source_id == src.id
            )
        ).scalar()
        if event_count == 0:
            problems["sources_without_events"].append({
                "source_id": src.id,
                "filename": src.filename,
                "game_id": src.game_id,
                "processing_status": src.processing_status,
            })

    # 3. Stuck jobs (running for > 1h without heartbeat update)
    cutoff = _utcnow().replace(tzinfo=None) - timedelta(hours=1)
    stuck_jobs = db.execute(
        select(Job).where(
            Job.user_id == user_id,
            Job.status == JobStatus.running.value,
            Job.started_at < cutoff,
        )
    ).scalars().all()

    for job in stuck_jobs:
        problems["stuck_jobs"].append({
            "job_id": job.id,
            "type": job.type,
            "status": job.status,
            "stage": job.stage,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "gameplay_source_id": job.gameplay_source_id,
        })

    # 4. Rejected KIs still in the idea queue
    from gpcg.domain.models import Automation
    auto = db.query(Automation).filter(Automation.user_id == user_id).first()
    if auto and auto.config:
        config = auto.config if isinstance(auto.config, dict) else {}
        raw_queue = config.get("idea_queue", [])
        for entry in raw_queue:
            ki_id = entry.get("ki_id") if isinstance(entry, dict) else entry
            if ki_id is None:
                continue
            ki = db.get(KnowledgeItem, ki_id)
            if ki is None:
                problems["rejected_kis_in_queue"].append({
                    "ki_id": ki_id,
                    "reason": "not found",
                })
            elif ki.status == KnowledgeItemStatus.rejected.value:
                problems["rejected_kis_in_queue"].append({
                    "ki_id": ki_id,
                    "title": ki.title[:80] if ki.title else "",
                    "reason": "rejected",
                })

    # 5. KIs in queue with game_id but user has no clips for that game
    if auto and auto.config:
        config = auto.config if isinstance(auto.config, dict) else {}
        raw_queue = config.get("idea_queue", [])
        for entry in raw_queue:
            ki_id = entry.get("ki_id") if isinstance(entry, dict) else entry
            if ki_id is None:
                continue
            ki = db.get(KnowledgeItem, ki_id)
            if ki is None or ki.status != KnowledgeItemStatus.fresh.value:
                continue
            if ki.game_id is None:
                continue
            # Check if user has clips for this game
            user_clips = db.execute(
                select(func.count(GameplayAsset.id))
                .join(GameplaySource, GameplayAsset.source_id == GameplaySource.id)
                .where(
                    GameplaySource.game_id == ki.game_id,
                    GameplaySource.ingestion_status == IngestionStatus.ready.value,
                    GameplaySource.ingestion_status != IngestionStatus.deleted.value,
                    ((GameplaySource.user_id == user_id) |
                     (GameplaySource.is_public == True)),
                )
            ).scalar()
            if user_clips == 0:
                problems["kis_without_gameplay"].append({
                    "ki_id": ki_id,
                    "title": ki.title[:80] if ki.title else "",
                    "game_id": ki.game_id,
                    "reason": "no clips available for this game",
                })

    # Summary
    critical = (
        len(problems["sources_without_clips"])
        + len(problems["stuck_jobs"])
        + len(problems["rejected_kis_in_queue"])
    )
    warning = (
        len(problems["sources_without_events"])
        + len(problems["kis_without_gameplay"])
    )
    problems["summary"]["critical"] = critical
    problems["summary"]["warning"] = warning
    problems["summary"]["total"] = critical + warning

    return problems
