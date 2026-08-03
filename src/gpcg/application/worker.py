"""Worker — background loop that runs the inbox watcher + processes jobs.

Auto-repair: if QA fails, the worker can retry the job from an earlier stage
(up to max_repair_retries). The repair strategy maps QA issues to stages.
"""

from __future__ import annotations

import time
from typing import Optional

from sqlalchemy import select

from gpcg.application.generation_service import GenerationError, GenerationService
from gpcg.application.ingestion_service import IngestionService
from gpcg.config import get_settings
from gpcg.domain.models import Job, JobStage, JobStatus, JobType
from gpcg.infrastructure.database import session_scope
from gpcg.infrastructure.llm import get_llm
from gpcg.infrastructure.video_generate_adapter import VideoGenerateAdapter
from gpcg.logging import get_logger

log = get_logger(__name__)


# Map QA issue types → stage to retry from
REPAIR_STAGE_MAP = {
    "hook": JobStage.script.value,
    "pacing": JobStage.script.value,
    "repetition": JobStage.script.value,
    "coherence": JobStage.script.value,
    "length": JobStage.script.value,
    "tone": JobStage.content_planning.value,
    "technical": JobStage.render.value,
}


def run_worker() -> None:
    """Main worker loop. Runs until interrupted."""
    settings = get_settings()
    log.info(f"worker started (poll={settings.gpcg_worker_poll_interval}s)")

    ingestion = IngestionService()
    llm = get_llm()
    try:
        vg = VideoGenerateAdapter()
    except Exception as e:
        log.error(f"video-generate adapter init failed: {e}")
        vg = None

    gen = GenerationService(llm=llm, vg_adapter=vg)

    last_inbox_scan = 0.0
    while True:
        try:
            # Inbox scan on interval
            now = time.time()
            if now - last_inbox_scan >= settings.gpcg_inbox_poll_interval:
                discovered = ingestion.scan_once()
                if discovered:
                    log.info(f"inbox: {discovered} new recording(s) ingested")
                last_inbox_scan = now

            # Re-queue stale jobs (running with no worker, or worker gone offline)
            _requeue_stale_jobs()

            # Process one pending job
            job_id = _claim_next_job()
            if job_id is not None:
                _process_job(gen, job_id)
            else:
                # No pending jobs — check if any automation needs a new job
                _check_running_automations()
                time.sleep(settings.gpcg_worker_poll_interval)
        except KeyboardInterrupt:
            log.info("worker interrupted, exiting")
            break
        except Exception as e:
            log.exception(f"worker loop error: {e}")
            time.sleep(settings.gpcg_worker_poll_interval)


def _requeue_stale_jobs() -> None:
    """Re-queue jobs that are stuck in 'running' with no active worker.

    A job is considered stale if:
    - status='running' AND worker_id IS NULL (never claimed via API)
    - status='running' AND worker_id is set but worker is offline
    - status='running' AND updated_at > 10 minutes ago (timeout)

    This prevents jobs from being stuck forever if a worker crashes
    mid-job or if a job was claimed by the VPS worker but never processed.
    """
    from datetime import datetime, timezone, timedelta

    stale_timeout = timedelta(minutes=10)
    now = datetime.now(timezone.utc)

    with session_scope() as session:
        # Find stale jobs
        stale = session.execute(
            select(Job)
            .where(Job.status == JobStatus.running.value)
            .where(
                (Job.worker_id.is_(None))
                | (Job.updated_at < (now - stale_timeout))
            )
        ).scalars().all()

        for job in stale:
            # Don't re-queue jobs that the VPS worker is actively processing
            # (VPS worker jobs have worker_id=NULL but updated_at is recent)
            if job.worker_id is None and job.updated_at and (now - job.updated_at) < stale_timeout:
                continue

            log.warning(
                f"Re-queuing stale job #{job.id} (type={job.type}, "
                f"worker_id={job.worker_id}, updated_at={job.updated_at})"
            )
            job.status = JobStatus.queued.value
            job.worker_id = None
            job.started_at = None
            if job.attempts < job.max_attempts:
                job.attempts += 1
            session.flush()


def _claim_next_job() -> Optional[int]:
    """Atomically claim the next queued/retrying job. Returns job_id or None.

    V2: The VPS worker only claims jobs that DON'T need GPU/LLM.
    Specifically, it skips:
    - generate_short / curiosity_short: need GPU (Ollama, video-generate)
    - mapping: needs GPU (VLM, ASR)
    - game_enrich: needs Ollama + Wikidata/Wikipedia (blocked on VPS IPs)
    - content_collect: needs Ollama for scoring

    All these job types are processed by the RemoteWorker (local PC) via
    the /api/jobs/claim endpoint. The VPS worker only handles jobs that
    are purely DB/API operations.
    """
    # Job types that the VPS worker should NOT claim (handled by remote worker)
    remote_only_types = {
        JobType.generate_short.value,
        JobType.curiosity_short.value,
        JobType.mapping.value,
        JobType.game_enrich.value,
        JobType.content_collect.value,
    }

    with session_scope() as session:
        # Claim jobs without required_capabilities, EXCLUDING remote-only types
        job = session.execute(
            select(Job)
            .where(Job.status.in_([JobStatus.queued.value, JobStatus.retrying.value]))
            .where(
                (Job.required_capabilities == None)  # noqa: E711
                | (Job.required_capabilities == "[]")
                | (Job.required_capabilities == "")
            )
            .where(~Job.type.in_(remote_only_types))
            .order_by(Job.created_at.asc())
            .limit(1)
        ).scalar_one_or_none()
        if job is None:
            return None
        # Mark as running to claim it
        job.status = JobStatus.running.value
        session.flush()
        return job.id


def _process_job(gen: GenerationService, job_id: int) -> None:
    """Process a job with auto-repair on QA failure."""
    settings = get_settings()
    max_retries = settings.gpcg_max_repair_retries

    with session_scope() as session:
        job = session.get(Job, job_id)
        job.attempts += 1
        job_type = job.type
        session.flush()

    # V2: game_enrich and content_collect are now processed by the RemoteWorker
    # (local PC), not the VPS worker. If they reach here, skip them — the
    # remote worker will claim them via /api/jobs/claim.
    if job_type in (JobType.game_enrich.value, JobType.content_collect.value):
        log.info(f"VPS worker skipping {job_type} job #{job_id} — handled by remote worker")
        with session_scope() as session:
            job = session.get(Job, job_id)
            job.status = JobStatus.queued.value  # re-queue for remote worker
            session.commit()
        return

    # Legacy: generation jobs (GPU)
    success = gen.run_job(job_id)
    if success:
        return

    # Job failed — check if it's a QA failure we can repair
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job.status != JobStatus.failed.value:
            return  # already handled
        artifacts = job.artifacts or {}
        qa_report = artifacts.get("qa_report") or {}
        # If we have a video_id, load its qa_report
        vid_id = artifacts.get("video_id")
        if vid_id and not qa_report:
            from gpcg.domain.models import Video

            v = session.get(Video, vid_id)
            if v:
                qa_report = v.qa_report or {}

        issues = qa_report.get("issues", []) if isinstance(qa_report, dict) else []
        # Determine repair stage
        repair_stage = _determine_repair_stage(issues)
        can_repair = repair_stage is not None and job.attempts <= max_retries

        if can_repair:
            log.info(f"job #{job_id} auto-repair: retrying from {repair_stage} (attempt {job.attempts}/{max_retries})")
            job.status = JobStatus.retrying.value
            job.stage = repair_stage
            job.error = None
            # Reset artifacts from the repair stage onward
            job.artifacts = _strip_artifacts_after(job.artifacts, repair_stage)
            session.flush()
            # Recurse to reprocess
            _process_job(gen, job_id)
        else:
            log.error(f"job #{job_id} failed permanently after {job.attempts} attempt(s)")
            job.status = JobStatus.failed.value
            session.flush()


def _determine_repair_stage(issues: list[dict]) -> Optional[str]:
    """Pick the earliest stage to retry from based on QA issues."""
    if not issues:
        return None
    stages = []
    for issue in issues:
        itype = issue.get("type", "")
        stage = REPAIR_STAGE_MAP.get(itype)
        if stage:
            stages.append(stage)
    if not stages:
        return None
    # Pick the earliest stage in pipeline order
    order = [
        JobStage.content_planning.value,
        JobStage.script.value,
        JobStage.tts.value,
        JobStage.render.value,
    ]
    for s in order:
        if s in stages:
            return s
    return stages[0]


def _strip_artifacts_after(artifacts: dict, stage: str) -> dict:
    """Remove artifacts produced after the given stage (for clean retry)."""
    # Keep content_plan_id and script_id (foundational)
    keep = {"content_plan_id", "script_id"}
    if stage in (JobStage.content_planning.value,):
        keep = set()
    elif stage == JobStage.script.value:
        keep = {"content_plan_id"}
    elif stage == JobStage.tts.value:
        keep = {"content_plan_id", "script_id"}
    return {k: v for k, v in artifacts.items() if k in keep}


def _check_running_automations() -> None:
    """V2: Automation check is now handled by the remote worker.

    The remote worker calls POST /api/automation/check on each poll cycle.
    The VPS endpoint returns pending automations, and the remote worker
    makes the editorial decision locally (with LLM/Ollama) and creates
    the job via POST /api/automation/create-job.

    This function is kept for backward compatibility but does nothing —
    the VPS worker no longer creates automation jobs because the editorial
    decision requires the LLM, which is only available on the local PC.
    """
    pass  # V2: handled by remote worker via /api/automation/check


# ── V2: VPS-side job processors (no GPU needed) ──────────────────────────────


def _process_game_enrich_job(job_id: int) -> None:
    """Process a game_enrich job — fetch Wikidata/Wikipedia + generate lore.

    V2: runs on the VPS (Control Plane), not on the GPU worker.
    See ARCHITECTURE_V2.md §6.5, §13.1.
    """
    from gpcg.application.game_enrichment import enrich_game
    from gpcg.infrastructure.llm import get_llm

    log.info(f"processing game_enrich job #{job_id}")
    llm = None
    try:
        llm = get_llm()
    except Exception as e:
        log.warning(f"LLM init failed for enrichment (lore will be skipped): {e}")

    with session_scope() as session:
        job = session.get(Job, job_id)
        if not job:
            log.error(f"game_enrich job #{job_id} not found")
            return
        game_id = job.game_id
        if not game_id:
            _fail_job(session, job, "game_enrich job has no game_id")
            return

        try:
            success = enrich_game(session, game_id, force=True, llm=llm)
            if success:
                job.status = JobStatus.completed.value
                job.completed_at = _utcnow()
                job.stage = JobStage.done.value
                log.info(f"game_enrich job #{job_id} completed for game #{game_id}")
            else:
                job.status = JobStatus.failed.value
                job.completed_at = _utcnow()
                from gpcg.domain.models import Game
                game = session.get(Game, game_id)
                job.error = game.enrichment_error if game else "enrichment failed"
                log.error(f"game_enrich job #{job_id} failed: {job.error}")
        except Exception as e:
            log.exception(f"game_enrich job #{job_id} error: {e}")
            _fail_job(session, job, str(e))


def _process_content_collect_job(job_id: int) -> None:
    """Process a content_collect job — collect RSS + score KnowledgeItems.

    V2: runs on the VPS (Control Plane). See ARCHITECTURE_V2.md §7, §13.1.
    Collects RSS for all games with gameplay available, scores new items,
    and cleans up old news.
    """
    from gpcg.application.content_collectors import collect_rss, cleanup_old_news
    from gpcg.application.knowledge_item_service import score_all_fresh
    from gpcg.infrastructure.llm import get_llm
    from gpcg.domain.models import Game, GameplaySource
    from sqlalchemy import distinct, select as sa_select

    log.info(f"processing content_collect job #{job_id}")

    llm = None
    try:
        llm = get_llm()
    except Exception as e:
        log.warning(f"LLM init failed for content scoring: {e}")

    total_collected = 0
    total_scored = 0
    total_cleaned = 0

    with session_scope() as session:
        job = session.get(Job, job_id)
        if not job:
            log.error(f"content_collect job #{job_id} not found")
            return

        try:
            # Get all games that have gameplay sources (only collect for games in use)
            game_ids = session.execute(
                sa_select(distinct(GameplaySource.game_id)).where(GameplaySource.game_id.isnot(None))
            ).scalars().all()

            for game_id in game_ids:
                count = collect_rss(session, game_id)
                total_collected += count

            # Score new fresh items
            if llm:
                total_scored = score_all_fresh(session, llm, limit=50)

            # Cleanup old news
            settings = get_settings()
            total_cleaned = cleanup_old_news(session, days=settings.gpcg_news_retention_days)

            job.status = JobStatus.completed.value
            job.completed_at = _utcnow()
            job.stage = JobStage.done.value
            job.artifacts = {
                **(job.artifacts or {}),
                "collected": total_collected,
                "scored": total_scored,
                "cleaned": total_cleaned,
            }
            log.info(
                f"content_collect job #{job_id} completed: "
                f"collected={total_collected}, scored={total_scored}, cleaned={total_cleaned}"
            )
        except Exception as e:
            log.exception(f"content_collect job #{job_id} error: {e}")
            _fail_job(session, job, str(e))


def _fail_job(session, job, error: str) -> None:
    """Mark a job as failed with an error message."""
    job.status = JobStatus.failed.value
    job.completed_at = _utcnow()
    job.error = error
    session.flush()


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
