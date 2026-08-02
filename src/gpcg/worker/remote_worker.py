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
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)


# ── Config ───────────────────────────────────────────────────────────────────


@dataclass
class WorkerConfig:
    """Configuration for the remote worker."""
    vps_url: str = ""  # e.g., "https://brunointegrations.com/gpcg"
    worker_id: str = ""  # e.g., "home-pc"
    api_key: str = ""  # shared secret for API auth
    # Local storage for gameplay files (HD Toshiba)
    local_storage_dir: str = "/media/bruno/ToshibaHD/gpcg"
    # Polling interval (seconds) for job claiming
    poll_interval: float = 5.0
    # Heartbeat interval (seconds)
    heartbeat_interval: float = 10.0
    # Status update interval (seconds) — even if nothing changes
    status_interval: float = 30.0
    # Worker capabilities
    capabilities: list[str] = field(default_factory=lambda: ["mapping", "generation", "knowledge_index"])
    # Worker version info
    worker_version: str = "0.1.0"
    git_commit: str = ""
    build_number: str = ""

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        """Load config from environment variables."""
        return cls(
            vps_url=os.environ.get("GPCG_VPS_URL", ""),
            worker_id=os.environ.get("GPCG_WORKER_ID", ""),
            api_key=os.environ.get("GPCG_WORKER_API_KEY", ""),
            local_storage_dir=os.environ.get("GPCG_WORKER_STORAGE", "/media/bruno/ToshibaHD/gpcg"),
            poll_interval=float(os.environ.get("GPCG_WORKER_POLL_INTERVAL", "5")),
            heartbeat_interval=float(os.environ.get("GPCG_WORKER_HEARTBEAT_INTERVAL", "10")),
            capabilities=os.environ.get("GPCG_WORKER_CAPABILITIES", "mapping,generation").split(","),
        )

    def validate(self) -> None:
        """Raise ValueError if required config is missing."""
        if not self.vps_url:
            raise ValueError("VPS URL not configured (GPCG_VPS_URL)")
        if not self.worker_id:
            raise ValueError("Worker ID not configured (GPCG_WORKER_ID)")
        if not self.api_key:
            raise ValueError("Worker API key not configured (GPCG_WORKER_API_KEY)")


# ── GPU info ─────────────────────────────────────────────────────────────────


def _get_gpu_name() -> str:
    """Try to get GPU name via nvidia-smi."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().splitlines()[0]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return ""


def _get_gpu_usage() -> Optional[float]:
    """Try to get GPU usage percentage via nvidia-smi."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip().splitlines()[0])
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def _get_cpu_usage() -> Optional[float]:
    """Get CPU usage percentage."""
    try:
        import psutil
        return psutil.cpu_percent(interval=1)
    except ImportError:
        return None


def _get_ram_usage() -> Optional[float]:
    """Get RAM usage in GB."""
    try:
        import psutil
        return round(psutil.virtual_memory().used / (1024**3), 1)
    except ImportError:
        return None


# ── Remote Worker ────────────────────────────────────────────────────────────


class RemoteWorker:
    """Compute Plane worker — communicates with VPS Control Plane via HTTP.

    Lifecycle:
      1. register() — register with VPS
      2. start_heartbeat() — background thread sending heartbeats
      3. run() — main loop: poll for jobs, process, report results
      4. stop() — graceful shutdown
    """

    def __init__(self, config: WorkerConfig):
        self.config = config
        config.validate()

        # HTTP client (persistent connection)
        self.client = httpx.Client(
            base_url=config.vps_url.rstrip("/"),
            headers={"X-Worker-Key": config.api_key},
            timeout=300.0,  # long timeout for file downloads
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
        """Register with the VPS. Creates or updates the Worker record."""
        resp = self.client.post("/api/workers/register", json={
            "worker_id": self.config.worker_id,
            "hostname": platform.node(),
            "capabilities": self.config.capabilities,
            "worker_version": self.config.worker_version,
            "git_commit": self.config.git_commit,
            "build_number": self.config.build_number,
            "gpu_name": self.gpu_name,
        })
        resp.raise_for_status()
        data = resp.json()
        log.info(f"Registered with VPS: {data}")

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
    ) -> None:
        """Send a status update to the VPS."""
        self._current_activity = activity or self._current_activity
        try:
            self.client.post(
                f"/api/workers/{self.config.worker_id}/status",
                json={
                    "status": status,
                    "current_activity": self._current_activity,
                    "current_job_id": job_id,
                    "gpu_usage": _get_gpu_usage(),
                    "cpu_usage": _get_cpu_usage(),
                    "ram_usage": _get_ram_usage(),
                },
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
        """
        resp = self.client.post("/api/jobs/claim", json={
            "worker_id": self.config.worker_id,
            "capabilities": self.config.capabilities,
        })
        resp.raise_for_status()
        data = resp.json()
        job = data.get("job")
        if job is None:
            return None
        # Embed related data into the job dict for downstream handlers
        if data.get("gameplay_source"):
            job["gameplay_source"] = data["gameplay_source"]
        if data.get("document"):
            job["document"] = data["document"]
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
        """Report job progress to the VPS."""
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
        resp.raise_for_status()

    # ── Gameplay download ────────────────────────────────────────────────────

    def download_gameplay(self, source: dict) -> Path:
        """Download a gameplay file from VPS to local storage.

        Returns the local file path. Raises on error.
        """
        source_id = source["id"]
        token = source["upload_token"]
        filename = source["filename"]

        # Local path: /ToshibaHD/gpcg/gameplays/{source_id}_{filename}
        local_path = self.storage_root / "gameplays" / f"{source_id}_{filename}"
        local_path.parent.mkdir(parents=True, exist_ok=True)

        log.info(f"Downloading gameplay #{source_id} ({filename})...")
        self.send_status("busy", f"Baixando {filename}", job_id=self._current_job["id"] if self._current_job else None)

        # Stream download
        with self.client.stream(
            "GET",
            f"/api/gameplays/{source_id}/download",
            params={"token": token},
        ) as resp:
            resp.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1024 * 1024):  # 1MB
                    f.write(chunk)

        file_size = local_path.stat().st_size
        log.info(f"Downloaded {filename} ({file_size} bytes) → {local_path}")
        return local_path

    # ── Confirm download (checksum) ──────────────────────────────────────────

    def confirm_download(self, source: dict, local_path: Path) -> bool:
        """Verify checksum and confirm download with VPS.

        Returns True if confirmed, False if checksum mismatch.
        """
        source_id = source["id"]
        expected_hash = source["file_hash"]

        # Compute SHA256
        log.info(f"Verifying checksum for {local_path.name}...")
        sha256 = hashlib.sha256()
        with open(local_path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                sha256.update(chunk)
        actual_hash = sha256.hexdigest()

        if actual_hash.lower() != expected_hash.lower():
            log.error(
                f"Checksum mismatch for #{source_id}: "
                f"expected={expected_hash[:16]}... got={actual_hash[:16]}..."
            )
            return False

        # Confirm with VPS
        resp = self.client.post(
            f"/api/gameplays/{source_id}/confirm-download",
            json={
                "worker_id": self.config.worker_id,
                "checksum": actual_hash,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        log.info(f"Download confirmed for #{source_id}: {data}")
        return True

    # ── Submit mapping result ────────────────────────────────────────────────

    def submit_mapping_result(
        self,
        source_id: int,
        events: list[dict],
        analysis_version: str = "v1",
        config_hash: str = "",
        compatibility: Optional[dict] = None,
    ) -> dict:
        """Send gameplay analysis events to VPS."""
        resp = self.client.post(
            f"/api/gameplays/{source_id}/mapping-result",
            json={
                "events": events,
                "analysis_version": analysis_version,
                "config_hash": config_hash,
                "compatibility": compatibility or {},
            },
        )
        resp.raise_for_status()
        return resp.json()

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

    # ── Upload video ─────────────────────────────────────────────────────────

    def upload_video(self, job_id: int, video_path: Path) -> dict:
        """Upload a rendered video to the VPS."""
        with open(video_path, "rb") as f:
            resp = self.client.post(
                f"/api/jobs/{job_id}/upload-video",
                files={"file": (video_path.name, f, "video/mp4")},
            )
        resp.raise_for_status()
        return resp.json()

    # ── Main loop ────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Main worker loop: poll for jobs and process them."""
        self._running = True
        self.register()
        self.start_heartbeat()
        self.send_status("online", "Idle")

        log.info(f"Worker '{self.config.worker_id}' started. Polling every {self.config.poll_interval}s...")

        while self._running:
            try:
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

                self._current_job = None
                self.send_status("online", "Idle")

            except KeyboardInterrupt:
                log.info("Worker stopping (KeyboardInterrupt)...")
                break
            except Exception as e:
                log.error(f"Main loop error: {e}", exc_info=True)
                time.sleep(self.config.poll_interval)

        self.stop()

    def _process_job(self, job: dict) -> None:
        """Process a claimed job. Dispatches by job type."""
        job_id = job["id"]
        job_type = job["type"]

        if job_type == "mapping":
            self._process_mapping_job(job)
        elif job_type in ("generate_short", "curiosity_short"):
            self._process_generation_job(job)
        elif job_type == "knowledge_index":
            self._process_knowledge_indexing_job(job)
        else:
            log.warning(f"Unknown job type: {job_type} — marking as completed (no-op)")
            self.update_job_status(job_id, status="running", stage="done", progress=1.0)
            self.submit_job_result(job_id, status="completed")

    def _process_mapping_job(self, job: dict) -> None:
        """Process a mapping job: download → confirm → analyze → report.

        Runs GameplayAnalyzer locally (VLM + ASR + merge + interesting score).
        Sends only the structured event data to the VPS — never frames, crops,
        caches, or intermediate files. Those stay on local storage.
        """
        job_id = job["id"]
        source = job.get("gameplay_source")
        if not source:
            self.submit_job_result(job_id, status="failed", error="No gameplay source in job")
            return

        # Stage 1: Download
        self.update_job_status(job_id, status="running", stage="download", progress=0.05)
        local_path = self.download_gameplay(source)

        # Stage 2: Confirm download (checksum) → VPS deletes temp file
        self.update_job_status(job_id, status="running", stage="confirm_download", progress=0.10)
        confirmed = self.confirm_download(source, local_path)
        if not confirmed:
            self.submit_job_result(job_id, status="failed", error="Checksum mismatch")
            return

        # Stage 3: Run GameplayAnalyzer locally
        self.update_job_status(job_id, status="running", stage="mapping", progress=0.15)
        self.send_status("busy", f"Mapeando {source['filename']}", job_id=job_id)

        from gpcg.application.gameplay_analyzer import GameplayAnalyzer
        from gpcg.domain.gameplay_events import AnalysisConfig
        from gpcg.config import get_settings

        settings = get_settings()

        # Determine camera_type from the game (if linked)
        camera_type = "unknown"
        game_id = source.get("game_id")
        if game_id:
            # Fetch game info from VPS to get camera_type
            try:
                resp = self.client.get(f"/api/jobs/{job_id}/data")
                if resp.status_code == 200:
                    job_data = resp.json()
                    game = job_data.get("game")
                    if game and game.get("camera_type") and game["camera_type"] != "unknown":
                        camera_type = game["camera_type"]
            except Exception:
                pass  # fallback to "unknown"

        # Build analysis config from settings
        config = AnalysisConfig(
            coarse_segment_sec=settings.gpcg_gameplay_coarse_segment_sec,
            refine_interval_sec=settings.gpcg_gameplay_refine_interval_sec,
            activity_threshold=settings.gpcg_gameplay_activity_threshold,
            high_activity_threshold=settings.gpcg_gameplay_high_activity_threshold,
            ultra_refine_interval_sec=settings.gpcg_gameplay_ultra_refine_interval_sec,
            interesting_threshold=settings.gpcg_gameplay_interesting_threshold,
            vlm_batch_size=settings.gpcg_gameplay_vlm_batch_size,
            analysis_version=settings.gpcg_gameplay_analysis_version,
            vision_model=settings.gpcg_gameplay_vision_model,
            asr_model=settings.gpcg_gameplay_asr_model,
            asr_device=settings.gpcg_gameplay_asr_device,
            asr_compute_type=settings.gpcg_gameplay_asr_compute_type,
            enable_asr=settings.gpcg_gameplay_analysis_enabled,
            enable_interesting_score=True,
        )

        analyzer = GameplayAnalyzer(camera_type=camera_type, config=config)

        # Progress callback: update VPS with mapping progress
        def _progress(stage: str, pct: float) -> None:
            # Map analyzer stage to 0.15-0.90 range
            mapped = 0.15 + pct * 0.75
            self.update_job_status(job_id, status="running", stage="mapping", progress=mapped)

        log.info(f"Starting GameplayAnalyzer on {local_path.name} (camera_type={camera_type})")
        timeline = analyzer.analyze(
            local_path,
            source_id=source["id"],
            progress_callback=_progress,
        )

        log.info(
            f"Analysis complete: {timeline.event_count} events "
            f"(confident={len(timeline.confident_events)}, "
            f"interesting={len(timeline.interesting_events)})"
        )

        # Save analysis JSON locally (for debugging/reference)
        analysis_json_path = self.storage_root / "mapped" / f"source_{source['id']}_analysis.json"
        analysis_json_path.parent.mkdir(parents=True, exist_ok=True)
        analysis_json_path.write_text(timeline.to_json(indent=2))

        # Stage 4: Submit mapping result (events only, no frames)
        self.update_job_status(job_id, status="running", stage="mapping", progress=0.90)
        events = [e.to_dict() for e in timeline.events]

        # Compute compatibility flags
        compatibility = {"game_related": True, "general_topic": True}

        self.submit_mapping_result(
            source_id=source["id"],
            events=events,
            analysis_version=timeline.analysis_version,
            config_hash=timeline.config_hash,
            compatibility=compatibility,
        )

        # Done
        self.update_job_status(job_id, status="running", stage="done", progress=1.0)
        self.submit_job_result(job_id, status="completed", artifacts={
            "mapping_completed": True,
            "analysis_version": timeline.analysis_version,
            "config_hash": timeline.config_hash,
            "events_count": len(events),
            "vision_model": timeline.vision_model,
            "asr_model": timeline.asr_model,
            "duration": timeline.duration,
        })
        log.info(f"Mapping job #{job_id} completed: {len(events)} events")

        # Clean up downloaded gameplay (HD space is finite — analysis JSON is enough)
        # The gameplay can be re-downloaded from VPS if re-mapping is needed.
        try:
            local_path.unlink()
            log.info(f"Cleaned up gameplay: {local_path.name} ({local_path.stat().st_size // (1024*1024)}MB freed)")
        except OSError:
            pass

    # ── Knowledge document download ──────────────────────────────────────────

    def download_document(self, doc: dict) -> Path:
        """Download a knowledge document from VPS to local storage.

        Returns the local file path. Raises on error.
        """
        doc_id = doc["id"]
        token = doc.get("upload_token")
        if not token:
            raise RuntimeError(f"No upload_token for document {doc_id}")

        filename = doc.get("filename", f"doc_{doc_id}")
        # Sanitize filename for local storage
        safe_name = filename.replace("/", "_").replace("\\", "_")
        local_dir = self.storage_root / "knowledge"
        local_dir.mkdir(parents=True, exist_ok=True)
        local_path = local_dir / f"doc_{doc_id}_{safe_name}"

        url = f"/api/documents/{doc_id}/download"
        resp = self.client.get(url, params={"token": token}, follow_redirects=True)
        resp.raise_for_status()

        # Stream response content to file
        with open(local_path, "wb") as f:
            f.write(resp.content)

        file_size = local_path.stat().st_size
        log.info(f"Downloaded document {filename} ({file_size} bytes) → {local_path}")
        return local_path

    def confirm_document_download(self, doc: dict, local_path: Path) -> bool:
        """Verify checksum and confirm document download with VPS."""
        doc_id = doc["id"]
        import hashlib as _hashlib
        sha256 = _hashlib.sha256()
        with open(local_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                sha256.update(chunk)
        checksum = sha256.hexdigest()

        resp = self.client.post(
            f"/api/documents/{doc_id}/confirm-download",
            json={
                "checksum": checksum,
                "worker_id": self.config.worker_id,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        log.info(f"Download confirmed for document #{doc_id}: {data}")
        return True

    def submit_indexing_result(
        self,
        doc_id: int,
        chunks: list[dict],
        error: str = "",
    ) -> dict:
        """Send indexed knowledge chunks back to VPS."""
        resp = self.client.post(
            f"/api/documents/{doc_id}/indexing-result",
            json={
                "chunks": chunks,
                "chunk_count": len(chunks),
                "error": error,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def _process_knowledge_indexing_job(self, job: dict) -> None:
        """Process a knowledge indexing job: download → parse → chunk → embed → sync.

        Downloads the document from VPS, parses it (with OCR fallback via VLM
        if needed — Ollama is available locally), chunks the text, generates
        embeddings (via Ollama nomic-embed-text), and sends the chunks back
        to VPS for storage in the knowledge_chunks table.
        """
        job_id = job["id"]
        doc_info = job.get("document")
        if not doc_info:
            self.submit_job_result(job_id, status="failed", error="No document info in job")
            return

        doc_id = doc_info["id"]
        filename = doc_info.get("filename", f"doc_{doc_id}")

        # Stage 1: Download document from VPS
        self.update_job_status(job_id, status="running", stage="download", progress=0.05)
        self.send_status("busy", f"Baixando documento {filename}", job_id=job_id)
        local_path = self.download_document(doc_info)

        # Stage 2: Confirm download (checksum verification)
        self.update_job_status(job_id, status="running", stage="confirm_download", progress=0.10)
        confirmed = self.confirm_document_download(doc_info, local_path)
        if not confirmed:
            self.submit_job_result(job_id, status="failed", error="Checksum mismatch")
            return

        # Stage 3: Parse + chunk + embed locally
        self.update_job_status(job_id, status="running", stage="knowledge_indexing", progress=0.15)
        self.send_status("busy", f"Indexando {filename}", job_id=job_id)

        from gpcg.infrastructure.document_parser import parse_document, DocumentParseError
        from gpcg.application.knowledge_service import chunk_text, generate_embedding

        # Parse the document (pdfplumber → pypdf → OCR via VLM as last resort)
        try:
            text = parse_document(local_path, file_type=doc_info.get("file_type"))
        except DocumentParseError as e:
            log.error(f"Failed to parse document {filename}: {e}")
            self.submit_indexing_result(doc_id, chunks=[], error=str(e))
            self.submit_job_result(job_id, status="failed", error=str(e))
            try:
                local_path.unlink()
            except OSError:
                pass
            return

        if not text or not text.strip():
            error_msg = f"Document {filename} produced no extractable text"
            log.error(error_msg)
            self.submit_indexing_result(doc_id, chunks=[], error=error_msg)
            self.submit_job_result(job_id, status="failed", error=error_msg)
            try:
                local_path.unlink()
            except OSError:
                pass
            return

        log.info(f"Parsed {filename}: {len(text)} chars of text")

        # Chunk the text
        chunks_data = chunk_text(text)
        log.info(f"Chunked {filename} into {len(chunks_data)} chunks")

        # Generate embeddings for each chunk
        from gpcg.application.knowledge_service import _get_embedding_model
        embed_model = _get_embedding_model()
        total = len(chunks_data)
        chunk_dicts = []
        for i, chunk in enumerate(chunks_data):
            embedding = generate_embedding(chunk.content, chunk.heading_path)
            chunk_dicts.append({
                "content": chunk.content,
                "embedding": embedding,
                "chunk_index": chunk.index,
                "section": chunk.section,
                "char_start": chunk.char_start,
                "char_end": chunk.char_end,
                "embedding_model": embed_model if embedding else None,
            })
            # Update progress (0.15 → 0.90 range)
            if (i + 1) % 5 == 0 or i + 1 == total:
                progress = 0.15 + (i + 1) / total * 0.75
                self.update_job_status(
                    job_id, status="running", stage="knowledge_indexing", progress=progress
                )

        # Stage 4: Submit indexing result (chunks) to VPS
        self.update_job_status(job_id, status="running", stage="knowledge_indexing", progress=0.90)
        result = self.submit_indexing_result(doc_id, chunks=chunk_dicts)
        log.info(f"Synced {len(chunk_dicts)} chunks to VPS for document {doc_id}: {result}")

        # Done
        self.update_job_status(job_id, status="running", stage="done", progress=1.0)
        self.submit_job_result(job_id, status="completed", artifacts={
            "indexing_completed": True,
            "chunk_count": len(chunk_dicts),
            "document_id": doc_id,
        })
        log.info(f"Knowledge indexing job #{job_id} completed: {len(chunk_dicts)} chunks")

        # Clean up local document file
        try:
            local_path.unlink()
            log.info(f"Cleaned up document: {local_path.name}")
        except OSError:
            pass

    def _process_generation_job(self, job: dict) -> None:
        """Process a generation job: fetch data → run pipeline → upload video.

        The worker fetches all needed data from the VPS API, populates a local
        temp SQLite DB, runs GenerationService locally (GPU + video-generate),
        then uploads the rendered video and syncs results back to the VPS.
        """
        job_id = job["id"]
        log.info(f"Generation job #{job_id} — fetching data from VPS...")

        # Fetch all data needed for generation
        resp = self.client.get(f"/api/jobs/{job_id}/data")
        resp.raise_for_status()
        job_data = resp.json()

        # Populate a local temp DB and run GenerationService
        from gpcg.worker.local_db_sync import populate_local_db, run_generation_locally

        # Disable YouTube upload in the local GenerationService — the worker
        # handles it after uploading the video to the VPS (the google-integration
        # service runs on the VPS, not locally, and needs the VPS file path).
        import os
        os.environ["GPCG_YOUTUBE_UPLOAD_ENABLED"] = "false"

        self.update_job_status(job_id, status="running", stage="content_planning", progress=0.05)
        self.send_status("busy", "Gerando vídeo", job_id=job_id)

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
            self.send_status("busy", "Enviando vídeo", job_id=job_id)
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

    # ── Shutdown ─────────────────────────────────────────────────────────────

    def stop(self) -> None:
        """Graceful shutdown."""
        self._running = False
        self.send_status("offline", "Shutting down")
        self.client.close()
        log.info("Worker stopped")


# ── CLI entry point ──────────────────────────────────────────────────────────


def run_remote_worker(
    vps_url: str = "",
    worker_id: str = "",
    api_key: str = "",
    storage_dir: str = "",
    capabilities: str = "",
) -> None:
    """Run the remote worker. Called by the CLI."""
    config = WorkerConfig(
        vps_url=vps_url or os.environ.get("GPCG_VPS_URL", ""),
        worker_id=worker_id or os.environ.get("GPCG_WORKER_ID", ""),
        api_key=api_key or os.environ.get("GPCG_WORKER_API_KEY", ""),
        local_storage_dir=storage_dir or os.environ.get("GPCG_WORKER_STORAGE", "/media/bruno/ToshibaHD/gpcg"),
        capabilities=(capabilities or os.environ.get("GPCG_WORKER_CAPABILITIES", "mapping,generation")).split(","),
    )
    worker = RemoteWorker(config)
    worker.run()
