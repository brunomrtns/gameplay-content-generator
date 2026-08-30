"""Generation job handler mixin for :class:`RemoteWorker`.

Extracted from ``remote_worker.py``. Contains the video generation pipeline
(fetch data → populate local DB → run GenerationService → upload video →
sync results back to VPS).
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import JobCancelledError

log = logging.getLogger(__name__)


class GenerationMixin:
    """Generation job processing (generate_short / curiosity_short)."""

    def _process_generation_job(self, job: dict) -> None:
        """Process a generation job: fetch data → run pipeline → upload video.

        The worker fetches all needed data from the VPS API, populates a local
        temp SQLite DB, runs GenerationService locally (GPU + video-generate),
        then uploads the rendered video and syncs results back to the VPS.

        CANCELLATION: Checks for cancellation before starting the heavy
        generation pipeline. If cancelled, aborts immediately.
        """
        job_id = job["id"]
        log.info(f"Generation job #{job_id} — fetching data from VPS...")

        # Cooperative cancellation check before heavy work
        if self.check_job_cancelled(job_id):
            raise JobCancelledError(job_id)

        # Fetch all data needed for generation
        resp = self.client.get(f"/api/jobs/{job_id}/data")
        resp.raise_for_status()
        job_data = resp.json()

        # Download voice file from VPS if not present locally.
        # The voice_path in job artifacts is an absolute path inside the VPS
        # Docker container (e.g. /app/data/voices/bruno.wav). On the worker,
        # that path doesn't exist. We download the voice file by filename and
        # store it in the local voices_dir so GenerationService can find it.
        artifacts = job_data.get("job", {}).get("artifacts", {})
        if isinstance(artifacts, str):
            try:
                import json as _json
                artifacts = _json.loads(artifacts)
            except Exception:
                artifacts = {}
        voice_path_vps = artifacts.get("voice_path", "")
        if voice_path_vps:
            voice_filename = Path(voice_path_vps).name
            user_id = job.get("user_id")
            from gpcg.config import get_settings
            local_settings = get_settings()
            # Per-user isolation: save to voices_dir/user_{user_id}/filename
            if user_id:
                local_voice = local_settings.voices_dir / f"user_{user_id}" / voice_filename
            else:
                local_voice = local_settings.voices_dir / voice_filename
            if not local_voice.exists():
                try:
                    self._download_voice(voice_filename, user_id, local_voice)
                except Exception as e:
                    log.warning(f"Could not download voice {voice_filename}: {e}")

        # Populate a local temp DB and run GenerationService
        from gpcg.worker.local_db_sync import populate_local_db, run_generation_locally

        # Disable YouTube upload in the local GenerationService — the worker
        # handles it after uploading the video to the VPS (the google-integration
        # service runs on the VPS, not locally, and needs the VPS file path).
        import os
        os.environ["GPCG_YOUTUBE_UPLOAD_ENABLED"] = "false"

        # Kids: download story assets (images) from VPS before generation.
        # Games: gameplay files are downloaded separately during mapping jobs.
        job_domain = job_data.get("job", {}).get("domain", "games")
        if job_domain == "kids":
            self._download_kids_assets(job_data)

        self.update_job_status(job_id, status="running", stage="content_planning", progress=0.05)
        self.send_status("busy", "Gerando vídeo", job_id=job_id, activity_key="worker.activity.generating_video")

        result = run_generation_locally(
            job_data=job_data,
            storage_root=self.storage_root,
            progress_callback=lambda stage, pct: self.update_job_status(
                job_id, status="running", stage=stage, progress=pct
            ),
        )

        if result.get("status") == "failed":
            self.submit_job_result(job_id, status="failed", error=result.get("error", "Generation failed"))
            return

        # Upload the rendered video to VPS
        video_path = result.get("video_path")
        if video_path and Path(video_path).exists():
            self.update_job_status(job_id, status="running", stage="output", progress=0.95)
            self.send_status("busy", "Enviando vídeo", job_id=job_id, activity_key="worker.activity.uploading_video")
            upload_result = self.upload_video(job_id, Path(video_path))
            result["video"]["storage_key"] = upload_result.get("storage_key")
            # Clean up local video after successful upload (HD space is finite)
            try:
                Path(video_path).unlink()
                log.info(f"Cleaned up local video: {video_path}")
            except OSError:
                pass

        # YouTube upload is handled by the VPS (auto-publish or manual approval).
        # The VPS's submit_job_result endpoint checks the automation config:
        # - auto_publish=true  → VPS uploads to YouTube via google-integration
        # - auto_publish=false → video stays as pending_approval for UI review

        # Sync results back to VPS
        self.update_job_status(job_id, status="running", stage="done", progress=0.98)
        sync_payload = {}
        if result.get("content_plan"):
            sync_payload["content_plan"] = result["content_plan"]
        if result.get("script"):
            sync_payload["script"] = result["script"]
        if result.get("video"):
            sync_payload["video"] = result["video"]
        if result.get("artifacts"):
            sync_payload["artifacts"] = result["artifacts"]
        # V2: sync clip usage records so future jobs avoid same gameplay segments
        if result.get("clip_usages"):
            sync_payload["clip_usages"] = result["clip_usages"]

        if sync_payload:
            self.client.post(f"/api/jobs/{job_id}/sync", json=sync_payload)

        # Mark job as completed
        self.submit_job_result(
            job_id,
            status="completed",
            artifacts=result.get("artifacts", {}),
            video=result.get("video"),
        )
        log.info(f"Generation job #{job_id} completed")
