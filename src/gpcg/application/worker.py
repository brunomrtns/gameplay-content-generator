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


def _claim_next_job() -> Optional[int]:
    """Atomically claim the next queued/retrying job. Returns job_id or None.

    Claims two categories of jobs:
    1. Jobs WITHOUT required_capabilities — legacy generation jobs (GPU).
    2. Jobs with VPS-side capabilities (enrichment, content_intelligence) —
       these run on the VPS (Control Plane), not on the GPU worker.

    Jobs with GPU capabilities (mapping, generation, knowledge_index) are
    processed by the RemoteWorker via the /api/jobs/claim endpoint.
    """
    with session_scope() as session:
        # V2: claim enrichment/content_intelligence jobs (run on VPS, no GPU)
        vps_job = session.execute(
            select(Job)
            .where(Job.status.in_([JobStatus.queued.value, JobStatus.retrying.value]))
            .where(Job.type.in_([JobType.game_enrich.value, JobType.content_collect.value]))
            .order_by(Job.created_at.asc())
            .limit(1)
        ).scalar_one_or_none()
        if vps_job is not None:
            vps_job.status = JobStatus.running.value
            session.flush()
            return vps_job.id

        # Legacy: claim jobs without required_capabilities (generation jobs)
        job = session.execute(
            select(Job)
            .where(Job.status.in_([JobStatus.queued.value, JobStatus.retrying.value]))
            .where(
                (Job.required_capabilities == None)  # noqa: E711
                | (Job.required_capabilities == "[]")
                | (Job.required_capabilities == "")
            )
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

    # V2: dispatch VPS-side job types (no GPU needed)
    if job_type == JobType.game_enrich.value:
        _process_game_enrich_job(job_id)
        return
    if job_type == JobType.content_collect.value:
        _process_content_collect_job(job_id)
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
    """Verifica se há automações ativas que precisam de novos jobs.

    Para cada automação com status='running', se não houver job em andamento
    para aquele usuário, cria um novo job automaticamente.
    Isso implementa o loop contínuo de produção de vídeos.
    """
    try:
        from gpcg.api.automation_routes import create_job_from_automation
        from gpcg.domain.models import Automation

        with session_scope() as session:
            running = session.query(Automation).filter(
                Automation.status == "running"
            ).all()

        for auto in running:
            job_id = create_job_from_automation(auto.user_id)
            if job_id:
                log.info(
                    f"automação #{auto.id} (user={auto.user_id}): "
                    f"criado job #{job_id} para produção contínua"
                )
    except Exception as e:
        log.debug(f"automation check skipped: {e}")


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
    Placeholder — full implementation in Phase 3.
    """
    log.info(f"content_collect job #{job_id}: Phase 3 not yet implemented, marking as completed")
    with session_scope() as session:
        job = session.get(Job, job_id)
        if not job:
            return
        job.status = JobStatus.completed.value
        job.completed_at = _utcnow()
        job.stage = JobStage.done.value


def _fail_job(session, job, error: str) -> None:
    """Mark a job as failed with an error message."""
    job.status = JobStatus.failed.value
    job.completed_at = _utcnow()
    job.error = error
    session.flush()


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
