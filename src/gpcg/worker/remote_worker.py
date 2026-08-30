"""Remote Worker — Compute Plane client for the GPCG Control Plane.

This module runs on a local PC with GPU and communicates with the VPS API.
It does NOT access the database directly — all communication is via HTTP.

Architecture:
  VPS (Control Plane)          Worker (Compute Plane)
  ────────────────────         ──────────────────────
  API + Frontend               RemoteWorker
  PostgreSQL/SQLite            Local storage (ToshibaHD)
  Job queue                    GPU (VLM, ASR, FFmpeg)
  Worker registry              ← HTTP → VPS API

The worker:
  1. Registers with the VPS (worker_id, capabilities, version)
  2. Sends heartbeats every 10s (thread)
  3. Sends status updates on state changes
  4. Polls for available jobs (POST /api/jobs/claim)
  5. Downloads gameplay files → saves to local storage
  6. Confirms download (checksum) → VPS deletes temp file
  7. Runs processing (mapping/generation) — Fase 4 will add real pipeline
  8. Reports results back to VPS

Usage:
  gpcg remote-worker --vps-url https://brunointegrations.com/gpcg \
                     --worker-id home-pc \
                     --api-key <secret>

Or via environment variables:
  GPCG_VPS_URL=https://brunointegrations.com/gpcg
  GPCG_WORKER_ID=home-pc
  GPCG_WORKER_API_KEY=<secret>

The implementation has been split across the ``gpcg.worker`` package:
  - ``config.py``       — WorkerConfig, JobCancelledError, GPU helpers
  - ``file_transfer.py`` — download/upload mixin
  - ``automation.py``   — automation scheduling mixin
  - ``handlers/``       — per-job-type handler mixins
  - ``cli.py``          — run_remote_worker entrypoint
This module retains the ``RemoteWorker`` class (core lifecycle + dispatch)
and re-exports the public symbols for backward compatibility.
"""

from __future__ import annotations

import logging
import platform
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

from .config import JobCancelledError, WorkerConfig, _get_gpu_name, _get_gpu_usage, _get_cpu_usage, _get_ram_usage
from .file_transfer import FileTransferMixin
from .automation import AutomationMixin
from .handlers.mapping import MappingMixin
from .handlers.generation import GenerationMixin
from .handlers.knowledge import KnowledgeMixin
from .handlers.enrichment import EnrichmentMixin
from .handlers.content_collect import ContentCollectMixin
from .handlers.cleanup import CleanupMixin
from .handlers.kids import KidsMixin

# Re-export for backward compatibility (CLI imports run_remote_worker from here)
from .cli import run_remote_worker, _heuristic_score  # noqa: F401

log = logging.getLogger(__name__)


class RemoteWorker(
    FileTransferMixin,
    AutomationMixin,
    MappingMixin,
    GenerationMixin,
    KnowledgeMixin,
    EnrichmentMixin,
    ContentCollectMixin,
    CleanupMixin,
    KidsMixin,
):
    """Compute Plane worker — communicates with VPS Control Plane via HTTP.

    Lifecycle:
      1. register() — register with VPS
      2. start_heartbeat() — background thread sending heartbeats
      3. run() — main loop: poll for jobs, process, report results
      4. stop() — graceful shutdown

    Job-type-specific processing is provided by the handler mixins in
    ``gpcg.worker.handlers`` and the file-transfer/automation mixins.
    """

    def __init__(self, config: WorkerConfig):
        self.config = config
        config.validate()

        # HTTP client (persistent connection)
        # SSL verification can be disabled via GPCG_VERIFY_SSL=false when
        # the VPS is reached via a WireGuard IP and the TLS cert is for a
        # public domain.
        from gpcg.config import get_settings
        verify_ssl = get_settings().gpcg_verify_ssl
        self.client = httpx.Client(
            base_url=config.vps_url.rstrip("/"),
            headers={"X-Worker-Key": config.api_key},
            timeout=300.0,  # long timeout for file downloads
            verify=verify_ssl,
        )

        # State
        self._running = False
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._current_job: Optional[dict] = None
        self._current_activity: str = "Idle"

        # Local storage directories
        self.storage_root = Path(config.local_storage_dir)
        (self.storage_root / "gameplays").mkdir(parents=True, exist_ok=True)
        (self.storage_root / "mapped").mkdir(parents=True, exist_ok=True)
        (self.storage_root / "renders").mkdir(parents=True, exist_ok=True)
        (self.storage_root / "outputs").mkdir(parents=True, exist_ok=True)

        # GPU info (collected once at startup)
        self.gpu_name = _get_gpu_name()

        log.info(
            f"RemoteWorker initialized: id={config.worker_id} "
            f"vps={config.vps_url} gpu={self.gpu_name or 'N/A'} "
            f"storage={self.storage_root}"
        )

    # ── Registration ─────────────────────────────────────────────────────────

    def register(self) -> None:
        """Register with the VPS. Creates or updates the Worker record.

        Retries on transient errors (502, connection refused) so the worker
        can start even if the VPS API is temporarily unavailable.
        """
        max_retries = 10
        for attempt in range(max_retries):
            try:
                resp = self.client.post("/api/workers/register", json={
                    "worker_id": self.config.worker_id,
                    "hostname": platform.node(),
                    "capabilities": self.config.capabilities,
                    "worker_version": self.config.worker_version,
                    "git_commit": self.config.git_commit,
                    "build_number": self.config.build_number,
                    "gpu_name": self.gpu_name,
                })
                if resp.status_code < 500:
                    resp.raise_for_status()
                    data = resp.json()
                    log.info(f"Registered with VPS: {data}")
                    return
                log.warning(f"register: server error {resp.status_code}, retry {attempt+1}/{max_retries}")
            except Exception as e:
                log.warning(f"register: connection error: {e}, retry {attempt+1}/{max_retries}")
            time.sleep(5)
        log.error("register: failed after all retries, continuing anyway")

    def recover_stale_jobs(self) -> None:
        """Requeue any 'running' jobs assigned to this worker from a previous session.

        Called on startup right after register(). This handles the case where
        the worker was shut down (e.g., PC turned off for the night) while
        processing jobs — those jobs are stuck in 'running' and need to be
        released back to the queue.
        """
        try:
            resp = self.client.post("/api/jobs/recover-my-stale", json={
                "worker_id": self.config.worker_id,
                "capabilities": self.config.capabilities,
            })
            if resp.status_code == 200:
                data = resp.json()
                if data.get("requeued", 0) > 0:
                    log.info(
                        f"Startup recovery: requeued {data['requeued']} stale job(s) "
                        f"(checked {data.get('checked', 0)})"
                    )
                else:
                    log.debug(f"Startup recovery: no stale jobs found (checked {data.get('checked', 0)})")
            else:
                log.warning(f"Startup recovery: endpoint returned {resp.status_code}")
        except Exception as e:
            log.warning(f"Startup recovery: failed (non-critical): {e}")

    # ── Heartbeat (background thread) ────────────────────────────────────────

    def start_heartbeat(self) -> None:
        """Start background heartbeat thread."""
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name="heartbeat"
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        """Send heartbeats at configured interval."""
        while self._running:
            try:
                resp = self.client.post(
                    f"/api/workers/{self.config.worker_id}/heartbeat",
                    json={},
                )
                if resp.status_code != 200:
                    log.warning(f"Heartbeat failed: {resp.status_code} {resp.text}")
            except httpx.HTTPError as e:
                log.warning(f"Heartbeat error: {e}")
            time.sleep(self.config.heartbeat_interval)

    # ── Status update ────────────────────────────────────────────────────────

    def send_status(
        self,
        status: str = "online",
        activity: str = "",
        job_id: Optional[int] = None,
        activity_key: str = "",
    ) -> None:
        """Send a status update to the VPS.

        ``activity`` is a human-readable string (PT-BR fallback for backward compat).
        ``activity_key`` is a stable i18n key that the frontend can localize.
        """
        self._current_activity = activity or self._current_activity
        payload = {
            "status": status,
            "current_activity": self._current_activity,
            "current_job_id": job_id,
            "gpu_usage": _get_gpu_usage(),
            "cpu_usage": _get_cpu_usage(),
            "ram_usage": _get_ram_usage(),
        }
        if activity_key:
            payload["activity_key"] = activity_key
        try:
            self.client.post(
                f"/api/workers/{self.config.worker_id}/status",
                json=payload,
            )
        except httpx.HTTPError as e:
            log.warning(f"Status update failed: {e}")

    # ── Job claiming ─────────────────────────────────────────────────────────

    def claim_job(self) -> Optional[dict]:
        """Try to claim a job from the VPS. Returns job dict or None.

        The VPS returns {"job": {...}, "gameplay_source": {...}, "document": {...}}.
        We embed gameplay_source and document into the job dict so that
        _process_mapping_job and _process_knowledge_indexing_job can access
        them via job.get("gameplay_source") and job.get("document").

        Returns None on transient errors (502, 503, connection refused) so
        the main loop can retry on the next poll cycle instead of crashing.

        Tries Redis Streams first (XREADGROUP + claim-by-id), falls back to
        HTTP polling (POST /api/jobs/claim) if Redis is unavailable.
        """
        # ── Try Redis Streams first ────────────────────────────────────────
        try:
            from gpcg.infrastructure.job_queue import claim_job_via_redis
            from gpcg.infrastructure.redis_adapter import get_redis

            redis = get_redis()
            if redis.is_available():
                job_fields = claim_job_via_redis(
                    self.config.worker_id,
                    self.config.capabilities,
                    block_ms=self.config.poll_interval * 1000,
                )
                if job_fields and "job_id" in job_fields:
                    # Claim the job in SQLite via claim-by-id
                    try:
                        resp = self.client.post("/api/jobs/claim-by-id", json={
                            "job_id": job_fields["job_id"],
                            "worker_id": self.config.worker_id,
                        })
                        if resp.status_code == 200:
                            data = resp.json()
                            job = data.get("job")
                            if job:
                                if data.get("gameplay_source"):
                                    job["gameplay_source"] = data["gameplay_source"]
                                if data.get("document"):
                                    job["document"] = data["document"]
                                if data.get("game"):
                                    job["game"] = data["game"]
                                # Carry Redis stream/msg_id for later XACK
                                job["_redis_stream"] = job_fields.get("_stream")
                                job["_redis_msg_id"] = job_fields.get("_msg_id")
                                return job
                        elif resp.status_code == 409:
                            # Job already claimed by another worker — ack the stale
                            # Redis message so XAUTOCLAIM doesn't keep re-delivering it
                            from gpcg.infrastructure.job_queue import ack_job_data
                            ack_job_data(job_fields)
                            log.debug(f"claim_job: job {job_fields['job_id']} already claimed (acked stale msg)")
                            return None
                        else:
                            log.warning(f"claim_job: claim-by-id returned {resp.status_code}")
                            return None
                    except Exception as e:
                        log.warning(f"claim_job: claim-by-id error: {e}")
                        return None
                # No job in Redis Streams — fall through to HTTP polling
                # (handles jobs created while Redis was down and not yet reconciled)
        except Exception as e:
            log.debug(f"claim_job: Redis Streams unavailable, falling back to HTTP: {e}")

        # ── Fallback: HTTP polling ─────────────────────────────────────────
        try:
            resp = self.client.post("/api/jobs/claim", json={
                "worker_id": self.config.worker_id,
                "capabilities": self.config.capabilities,
            })
        except Exception as e:
            log.warning(f"claim_job: connection error: {e}")
            return None

        if resp.status_code >= 500:
            log.warning(f"claim_job: server error {resp.status_code}, will retry")
            return None

        if resp.status_code != 200:
            log.warning(f"claim_job: unexpected status {resp.status_code}")
            return None

        data = resp.json()
        job = data.get("job")
        if job is None:
            return None
        # Embed related data into the job dict for downstream handlers
        if data.get("gameplay_source"):
            job["gameplay_source"] = data["gameplay_source"]
        if data.get("document"):
            job["document"] = data["document"]
        if data.get("game"):
            job["game"] = data["game"]
        return job

    # ── Job status update ────────────────────────────────────────────────────

    def update_job_status(
        self,
        job_id: int,
        status: str,
        stage: str = "",
        progress: float = 0.0,
        error: str = "",
        artifacts: Optional[dict] = None,
    ) -> None:
        """Report job progress to the VPS.

        Raises JobCancelledError if the VPS responds with 409 (job was
        cancelled by domain reset or otherwise). The caller should catch
        this and abort processing.
        """
        payload = {
            "status": status,
            "stage": stage,
            "progress": progress,
        }
        if error:
            payload["error"] = error
        if artifacts:
            payload["artifacts"] = artifacts
        resp = self.client.post(f"/api/jobs/{job_id}/status", json=payload)
        if resp.status_code == 409:
            raise JobCancelledError(job_id)
        resp.raise_for_status()

    def check_job_cancelled(self, job_id: int) -> bool:
        """Check if a job has been cancelled on the VPS.

        Polls the job status endpoint. Returns True if the job is cancelled,
        False otherwise. On connection errors, returns False (optimistic —
        don't abort on transient network issues).
        """
        try:
            resp = self.client.get(f"/api/jobs/{job_id}/data")
            if resp.status_code == 409:
                return True
            if resp.status_code == 200:
                data = resp.json()
                job = data.get("job", {})
                return job.get("status") == "cancelled"
        except Exception:
            pass
        return False

    # ── Submit job result ────────────────────────────────────────────────────

    def submit_job_result(
        self,
        job_id: int,
        status: str,
        error: str = "",
        artifacts: Optional[dict] = None,
        video: Optional[dict] = None,
    ) -> dict:
        """Send final job result to VPS."""
        payload = {
            "status": status,
            "artifacts": artifacts or {},
        }
        if error:
            payload["error"] = error
        if video:
            payload["video"] = video
        resp = self.client.post(f"/api/jobs/{job_id}/result", json=payload)
        resp.raise_for_status()
        return resp.json()

    # ── Main loop ────────────────────────────────────────────────────────────

    def _sync_gameplays_on_startup(self) -> None:
        """Download gameplays from VPS that this worker doesn't have yet.

        Called on startup so a worker can generate jobs even if another
        worker did the mapping. Fetches the list of gameplays to sync from
        the VPS and downloads each one that isn't already local with a
        matching checksum.
        """
        try:
            resp = self.client.get(
                "/api/gameplays/list-for-sync",
                params={"worker_id": self.config.worker_id},
            )
            if resp.status_code != 200:
                log.warning(f"Gameplay sync: VPS returned {resp.status_code}")
                return

            data = resp.json()
            gameplays = data.get("gameplays", [])
            if not gameplays:
                log.info("Gameplay sync: no gameplays to download")
                return

            log.info(f"Gameplay sync: {len(gameplays)} gameplay(s) to download")
            for gp in gameplays:
                source_id = gp["id"]
                filename = gp["filename"]
                expected_hash = gp["file_hash"]

                # Check if already exists locally with matching checksum
                local_path = self.storage_root / "gameplays" / f"{source_id}_{filename}"
                if local_path.exists() and local_path.stat().st_size > 0:
                    if self._verify_local_file(local_path, expected_hash):
                        log.info(f"Gameplay sync: {filename} already local (checksum OK)")
                        # Confirm with VPS so it knows we have it
                        self.confirm_download(gp, local_path)
                        continue
                    else:
                        log.warning(f"Gameplay sync: {filename} checksum mismatch, re-downloading")
                        local_path.unlink()

                # Download
                try:
                    self.send_status("busy", f"Sincronizando {filename}", activity_key="worker.activity.synchronizing_file")
                    downloaded = self.download_gameplay(gp)
                    self.confirm_download(gp, downloaded)
                    log.info(f"Gameplay sync: downloaded {filename} ({gp.get('file_size', 0)} bytes)")
                except Exception as e:
                    log.error(f"Gameplay sync: failed to download {filename}: {e}")

            self.send_status("online", "Idle")
        except Exception as e:
            log.warning(f"Gameplay sync on startup failed: {e}")

    def run(self) -> None:
        """Main worker loop: poll for jobs and process them."""
        self._running = True
        self.register()
        self.recover_stale_jobs()
        self.start_heartbeat()
        self.send_status("online", "Idle")

        # Multi-worker: sync gameplays on startup so this worker can
        # generate jobs even if another worker did the mapping.
        self._sync_gameplays_on_startup()

        # Auto content collection scheduler
        self._last_collection_time = 0.0
        from gpcg.config import get_settings
        settings = get_settings()
        self._collection_interval_sec = settings.gpcg_content_collection_interval_hours * 3600
        log.info(
            f"Worker '{self.config.worker_id}' started. Polling every {self.config.poll_interval}s. "
            f"Auto content collection every {settings.gpcg_content_collection_interval_hours}h."
        )

        while self._running:
            try:
                # Check if any automation needs a new job created
                self._check_automations()

                # Auto content collection (every N hours)
                self._maybe_auto_collect()

                job = self.claim_job()
                if job is None:
                    time.sleep(self.config.poll_interval)
                    continue

                self._current_job = job
                log.info(f"Claimed job #{job['id']} (type={job['type']}, priority={job['priority']})")

                try:
                    self._process_job(job)
                except Exception as e:
                    log.error(f"Job #{job['id']} failed: {e}", exc_info=True)
                    self.submit_job_result(job["id"], status="failed", error=str(e))
                    self.send_status("error", f"Erro: {e}")

                # Ack the Redis Streams message so XAUTOCLAIM doesn't
                # re-deliver it to another worker
                redis_stream = job.pop("_redis_stream", None)
                redis_msg_id = job.pop("_redis_msg_id", None)
                if redis_stream and redis_msg_id:
                    try:
                        from gpcg.infrastructure.job_queue import ack_job
                        ack_job(redis_stream, redis_msg_id)
                    except Exception as e:
                        log.debug(f"XACK failed for job #{job['id']}: {e}")

                self._current_job = None
                self.send_status("online", "Idle")

            except KeyboardInterrupt:
                log.info("Worker stopping (KeyboardInterrupt)...")
                self.stop()
                break
            except Exception as e:
                log.error(f"Main loop error: {e}", exc_info=True)
                time.sleep(self.config.poll_interval)

    def _process_job(self, job: dict) -> None:
        """Process a claimed job. Dispatches by job type.

        Catches JobCancelledError — if the VPS reports the job as cancelled
        (e.g., by domain reset), the worker stops processing immediately
        and does NOT submit a result (the result would be rejected anyway).
        """
        job_id = job["id"]
        job_type = job["type"]

        try:
            if job_type == "mapping":
                self._process_mapping_job(job)
            elif job_type in ("generate_short", "curiosity_short"):
                self._process_generation_job(job)
            elif job_type == "knowledge_index":
                self._process_knowledge_indexing_job(job)
            elif job_type == "game_enrich":
                self._process_game_enrich_job(job)
            elif job_type == "content_collect":
                self._process_content_collect_job(job)
            elif job_type == "cleanup_gameplay":
                self._process_cleanup_gameplay_job(job)
            elif job_type == "cleanup_user_storage":
                self._process_cleanup_user_storage_job(job)
            elif job_type == "kids_idea_discovery":
                self._process_kids_idea_discovery_job(job)
            elif job_type == "kids_idea_score":
                self._process_kids_idea_score_job(job)
            elif job_type == "kids_asset_process":
                self._process_kids_asset_process_job(job)
            else:
                log.warning(f"Unknown job type: {job_type} — marking as completed (no-op)")
                self.update_job_status(job_id, status="running", stage="done", progress=1.0)
                self.submit_job_result(job_id, status="completed")
        except JobCancelledError:
            log.warning(
                f"Job #{job_id} (type={job_type}) was cancelled on the VPS — "
                f"aborting processing. Intermediate files will be cleaned up "
                f"by the domain reset cleanup job."
            )
            self.send_status("idle", "Job cancelled")
            # Do NOT submit a result — it would be rejected with 409.

    # ── Shutdown ─────────────────────────────────────────────────────────────

    def _release_current_job(self, reason: str = "worker shutting down") -> None:
        """Release the current job back to the queue so another worker can pick it up.

        Called during graceful shutdown. If the HTTP call fails (e.g., VPS
        unreachable), the reconciler will eventually requeue the job based
        on heartbeat timeout.
        """
        if not self._current_job:
            return
        job_id = self._current_job.get("id")
        if not job_id:
            return
        try:
            resp = self.client.post(
                f"/api/jobs/{job_id}/release",
                json={
                    "worker_id": self.config.worker_id,
                    "capabilities": self.config.capabilities,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                log.info(f"Released job #{job_id} ({reason}) → {data.get('status', 'unknown')}")
            else:
                log.warning(f"Release job #{job_id} returned {resp.status_code} "
                            f"(reconciler will requeue it)")
        except Exception as e:
            log.warning(f"Could not release job #{job_id} on shutdown: {e} "
                        f"(reconciler will requeue it)")

    def stop(self) -> None:
        """Graceful shutdown — release current job and notify VPS."""
        self._running = False
        self._release_current_job("worker shutting down")
        self._current_job = None
        self.send_status("offline", "Shutting down")
        self.client.close()
        log.info("Worker stopped")
