"""Worker Panel endpoints — worker-auth, no BI Identity needed.

These endpoints power the worker's local monitoring panel. They use worker
authentication (X-Worker-Key) rather than BI Identity SSO, since the worker
process needs to query job/video/idea/automation status without a human
session.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from gpcg.core.models import Job, JobPriority, JobStatus
from gpcg.infrastructure.database import get_db

from gpcg.api.workers._common import worker_auth

log = logging.getLogger(__name__)
router = APIRouter(tags=["workers"])


@router.get("/panel/jobs")
def panel_list_jobs(
    limit: int = 50,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """List recent jobs for the worker panel (worker-auth, not user-auth)."""
    jobs = db.query(Job).order_by(Job.id.desc()).limit(limit).all()
    result = []
    for j in jobs:
        result.append({
            "id": j.id,
            "job_type": j.type,
            "status": j.status,
            "game_id": j.game_id,
            "stage": j.stage,
            "worker_id": j.worker_id,
            "created_at": j.created_at.isoformat() if j.created_at else None,
        })
    return result


@router.get("/panel/videos")
def panel_list_videos(
    limit: int = 30,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """List recent videos for the worker panel (worker-auth, not user-auth)."""
    from gpcg.core.models import Video as VideoModel
    videos = db.query(VideoModel).order_by(VideoModel.id.desc()).limit(limit).all()
    result = []
    for v in videos:
        result.append({
            "id": v.id,
            "game_id": v.game_id,
            "duration": v.duration,
            "status": v.status,
            "job_id": v.job_id,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        })
    return result


@router.get("/panel/ideas")
def panel_list_ideas(
    limit: int = 30,
    status: str = "",
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """List knowledge items for the worker panel (worker-auth, not user-auth)."""
    from gpcg.core.models import KnowledgeItem, KnowledgeItemStatus
    q = db.query(KnowledgeItem)
    if status:
        q = q.filter(KnowledgeItem.status == status)
    items = q.order_by(KnowledgeItem.id.desc()).limit(limit).all()
    result = []
    for ki in items:
        result.append({
            "id": ki.id,
            "status": ki.status,
            "item_type": ki.item_type,
            "editorial_score": ki.editorial_score,
            "title": ki.title,
            "source_name": ki.source_name,
        })
    return result


@router.get("/panel/automation")
def panel_get_automation(
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Get automation status for the worker panel (worker-auth, not user-auth)."""
    from gpcg.core.models import Automation
    auto = db.query(Automation).first()
    if not auto:
        return {"status": "none", "user_id": None}
    return {
        "status": auto.status,
        "user_id": auto.user_id,
        "format": (auto.config or {}).get("format"),
    }


@router.post("/panel/automation/pause")
def panel_pause_automation(
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Pause automation for the worker panel (worker-auth, not user-auth)."""
    from gpcg.core.models import Automation
    auto = db.query(Automation).first()
    if not auto:
        return {"error": "no automation found"}
    auto.status = "paused"
    db.commit()
    log.info(f"panel: automation paused (by worker)")
    return {"ok": True, "status": "paused"}


@router.post("/panel/automation/resume")
def panel_resume_automation(
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Resume automation for the worker panel (worker-auth, not user-auth)."""
    from gpcg.core.models import Automation
    auto = db.query(Automation).first()
    if not auto:
        return {"error": "no automation found"}
    auto.status = "running"
    db.commit()
    log.info(f"panel: automation resumed (by worker)")
    return {"ok": True, "status": "running"}


@router.post("/panel/collect-ideas")
def panel_collect_ideas(
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Trigger content collection for the worker panel (worker-auth, not user-auth)."""
    from gpcg.core.models import Automation
    auto = db.query(Automation).first()
    if not auto:
        return {"error": "no automation found"}
    # Create a content collection job
    job = Job(
        user_id=auto.user_id,
        job_type="content_collection",
        status=JobStatus.queued.value,
        priority=JobPriority.high.value,
    )
    db.add(job)
    db.commit()
    log.info(f"panel: content collection job #{job.id} created (by worker)")
    return {"ok": True, "job_id": job.id}
