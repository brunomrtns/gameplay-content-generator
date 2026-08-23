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


class JobCancelledError(Exception):
    """Raised when the VPS reports that a job has been cancelled.

    The worker should catch this and abort processing immediately.
    The job's intermediate files will be cleaned up by the cleanup_user_storage
    job created during domain reset.
    """
    def __init__(self, job_id: int):
        self.job_id = job_id
        super().__init__(f"Job #{job_id} has been cancelled on the VPS")


# ── Config ───────────────────────────────────────────────────────────────────


@dataclass
class WorkerConfig:
    """Configuration for the remote worker."""
    vps_url: str = ""  # e.g., "https://brunointegrations.com/gpcg"
    worker_id: str = ""  # e.g., "home-pc"
    api_key: str = ""  # shared secret for API auth
    # Local storage for gameplay/renders/outputs. Override via GPCG_WORKER_STORAGE.
    # Default is a generic path that works on any machine (not Bruno-specific).
    local_storage_dir: str = "./data/gpcg-worker"
    # Polling interval (seconds) for job claiming
    poll_interval: float = 5.0
    # Heartbeat interval (seconds)
    heartbeat_interval: float = 10.0
    # Status update interval (seconds) — even if nothing changes
    status_interval: float = 30.0
    # Worker capabilities
    capabilities: list[str] = field(default_factory=lambda: ["mapping", "generation", "knowledge_index", "enrichment", "content_intelligence"])
    # Worker version info
    worker_version: str = "0.1.0"
    git_commit: str = ""
    build_number: str = ""
    # Ollama URL for local VLM (game resolution, gameplay analysis)
    ollama_url: str = "http://localhost:11434"

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        """Load config from environment variables."""
        return cls(
            vps_url=os.environ.get("GPCG_VPS_URL", ""),
            worker_id=os.environ.get("GPCG_WORKER_ID", ""),
            api_key=os.environ.get("GPCG_WORKER_API_KEY", ""),
            local_storage_dir=os.environ.get("GPCG_WORKER_STORAGE", "./data/gpcg-worker"),
            poll_interval=float(os.environ.get("GPCG_WORKER_POLL_INTERVAL", "5")),
            heartbeat_interval=float(os.environ.get("GPCG_WORKER_HEARTBEAT_INTERVAL", "10")),
            capabilities=os.environ.get("GPCG_WORKER_CAPABILITIES", "mapping,generation").split(","),
            ollama_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
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

        Returns None on transient errors (502, 503, connection refused) so
        the main loop can retry on the next poll cycle instead of crashing.
        """
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

    # ── Gameplay download ────────────────────────────────────────────────────

    def download_gameplay(self, source: dict) -> Path:
        """Download a gameplay file from VPS to local storage.

        Tries SCP first (faster, more robust for large files — bypasses
        nginx/HTTP). Falls back to HTTP streaming if SCP is not available.

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

        # Try SCP first (bypasses nginx, much more robust for large files)
        if self._try_scp_download(source, local_path):
            file_size = local_path.stat().st_size
            log.info(f"Downloaded {filename} via SCP ({file_size} bytes) → {local_path}")
            return local_path

        # Fallback: HTTP streaming download
        log.info(f"SCP unavailable, falling back to HTTP download for {filename}")
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
        log.info(f"Downloaded {filename} via HTTP ({file_size} bytes) → {local_path}")
        return local_path

    def _try_scp_download(self, source: dict, local_path: Path) -> bool:
        """Try to download via SCP directly from VPS host.

        Returns True if successful, False if SCP is not available.
        Reads SSH config from env vars:
        - GPCG_SSH_HOST: VPS host (default: extracted from vps_url or 10.0.0.1)
        - GPCG_SSH_USER: SSH user (default: root)
        - GPCG_DOCKER_VOLUME: Docker volume mount path on host
          (default: /var/lib/docker/volumes/gpcg_gpcg-data/_data)
        """
        import shutil as _shutil
        import urllib.parse

        if _shutil.which("scp") is None:
            return False

        ssh_host = os.environ.get("GPCG_SSH_HOST", "")
        if not ssh_host:
            # Extract host from vps_url
            parsed = urllib.parse.urlparse(self.config.vps_url)
            ssh_host = parsed.hostname or "10.0.0.1"

        ssh_user = os.environ.get("GPCG_SSH_USER", "root")
        volume_path = os.environ.get(
            "GPCG_DOCKER_VOLUME",
            "/var/lib/docker/volumes/gpcg_gpcg-data/_data",
        )

        # Build remote path from storage_key
        storage_key = source.get("storage_key", "")
        if not storage_key:
            return False

        # storage_key is like "user_2/filename" → temp_uploads/user_2/filename
        remote_rel = f"temp_uploads/{storage_key}"
        remote_path = f"{volume_path}/{remote_rel}"

        ssh_target = f"{ssh_user}@{ssh_host}"
        log.info(f"SCP download: {ssh_target}:{remote_path} → {local_path}")

        try:
            result = subprocess.run(
                ["scp", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no",
                 f"{ssh_target}:{remote_path}", str(local_path)],
                capture_output=True, text=True, timeout=600,
            )
            if result.returncode != 0:
                log.warning(f"SCP failed (exit {result.returncode}): {result.stderr[:200]}")
                # Clean up partial file
                if local_path.exists():
                    local_path.unlink()
                return False
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            log.warning(f"SCP error: {e}")
            if local_path.exists():
                local_path.unlink()
            return False

    def _verify_local_file(self, local_path: Path, expected_hash: str) -> bool:
        """Verify that a local file matches the expected SHA256 hash."""
        if not expected_hash or not local_path.exists():
            return False
        log.info(f"Verifying local file checksum for {local_path.name}...")
        sha256 = hashlib.sha256()
        with open(local_path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                sha256.update(chunk)
        actual_hash = sha256.hexdigest()
        match = actual_hash.lower() == expected_hash.lower()
        if match:
            log.info(f"Checksum OK for {local_path.name}")
        else:
            log.warning(f"Checksum mismatch for {local_path.name}: expected {expected_hash[:16]}... got {actual_hash[:16]}...")
        return match

    # ── Voice download ─────────────────────────────────────────────────────────

    def _download_voice(self, filename: str, user_id: int, local_path: Path) -> None:
        """Download a voice reference file from VPS to local voices_dir.

        Saves to voices_dir/user_{user_id}/filename to preserve per-user
        isolation. Tries SCP first (same as gameplay download), falls back
        to HTTP via the worker-auth endpoint /api/voices/{filename}/download.
        """
        local_path.parent.mkdir(parents=True, exist_ok=True)

        # Try SCP first (same approach as gameplay download)
        if self._try_scp_voice(filename, user_id, local_path):
            log.info(f"Downloaded voice {filename} via SCP → {local_path}")
            return

        # Fallback: HTTP download via worker-auth endpoint
        log.info(f"SCP unavailable, downloading voice {filename} via HTTP")
        resp = self.client.get(
            f"/api/voices/{filename}/download",
            params={"user_id": user_id},
        )
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            f.write(resp.content)
        log.info(f"Downloaded voice {filename} via HTTP → {local_path}")

    def _try_scp_voice(self, filename: str, user_id: int, local_path: Path) -> bool:
        """Try to download a voice file via SCP from VPS Docker volume."""
        import shutil as _shutil
        import urllib.parse

        if _shutil.which("scp") is None:
            return False

        ssh_host = os.environ.get("GPCG_SSH_HOST", "")
        if not ssh_host:
            parsed = urllib.parse.urlparse(self.config.vps_url)
            ssh_host = parsed.hostname or "10.0.0.1"

        ssh_user = os.environ.get("GPCG_SSH_USER", "root")
        volume_path = os.environ.get(
            "GPCG_DOCKER_VOLUME",
            "/var/lib/docker/volumes/gpcg_gpcg-data/_data",
        )

        # Voice files are in data/voices/{user_id_dir}/ or data/voices/
        # Try user-specific dir first, then shared dir
        candidates = [
            f"{volume_path}/voices/user_{user_id}/{filename}",
            f"{volume_path}/voices/{filename}",
        ]

        ssh_target = f"{ssh_user}@{ssh_host}"
        for remote_path in candidates:
            log.info(f"SCP voice: {ssh_target}:{remote_path} → {local_path}")
            try:
                result = subprocess.run(
                    ["scp", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no",
                     f"{ssh_target}:{remote_path}", str(local_path)],
                    capture_output=True, text=True, timeout=120,
                )
                if result.returncode == 0 and local_path.exists():
                    return True
                if local_path.exists():
                    local_path.unlink()
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
        return False

    # ── Kids asset download ───────────────────────────────────────────────────

    def _download_kids_assets(self, job_data: dict) -> None:
        """Download Kids story assets (images) from VPS to local storage.

        For Kids jobs, the pipeline needs the images locally to convert them
        to video clips (Ken Burns effect). This downloads each asset via the
        worker-auth endpoint /api/kids/assets/{id}/download and saves to
        {storage_root}/kids_assets/{storage_key}.
        """
        assets = job_data.get("story_assets", [])
        if not assets:
            log.warning("Kids job has no story_assets in job_data — pipeline will have no images")
            return

        kids_dir = self.storage_root / "kids_assets"
        kids_dir.mkdir(parents=True, exist_ok=True)

        for asset in assets:
            asset_id = asset.get("id")
            storage_key = asset.get("storage_key", "")
            filename = asset.get("filename", f"asset_{asset_id}")

            if not asset_id or not storage_key:
                log.warning(f"Skipping Kids asset with missing id/storage_key: {asset}")
                continue

            local_path = kids_dir / storage_key
            if local_path.exists():
                log.info(f"Kids asset #{asset_id} already exists locally: {local_path}")
                continue

            try:
                log.info(f"Downloading Kids asset #{asset_id} ({filename}) from VPS...")
                resp = self.client.get(f"/api/kids/assets/{asset_id}/download")
                resp.raise_for_status()
                with open(local_path, "wb") as f:
                    f.write(resp.content)
                log.info(f"Downloaded Kids asset #{asset_id} → {local_path}")
            except Exception as e:
                log.error(f"Failed to download Kids asset #{asset_id}: {e}")

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

    # ── VLM game resolution ──────────────────────────────────────────────────

    def _try_vlm_resolution(
        self,
        source_id: int,
        local_path: Path,
        source: dict,
    ) -> Optional[int]:
        """Run full game resolution (L1→L2→L3) locally and report to VPS.

        The VPS does NOT attempt game resolution — it just stores the upload
        and creates a mapping job. The worker (with GPU + Ollama + the video)
        runs the full hierarchical resolver:
          L1: deterministic (filename → slug/alias registry)
          L2: prior (capture_source → historical game association)
          L3: VLM (sampled frames → gemma3:12b identification)

        Fetches the game registry from VPS, builds a temp SQLite DB, runs
        resolve(), and reports the result back via
        POST /gameplays/{source_id}/resolve-game.

        Returns the resolved game_id if successful, None otherwise.
        """
        try:
            from gpcg.domain.game_resolver import resolve
            from gpcg.infrastructure.llm import LLMClient
        except ImportError as e:
            log.warning(f"Game resolver modules not available: {e}")
            return None

        # L1/L2 work without Ollama. L3 needs it.
        llm = None
        if self._ollama_available():
            llm = LLMClient()
        else:
            log.info("Ollama not available — will try L1/L2 only (no VLM)")

        self.send_status("busy", f"Identificando jogo — {source['filename']}")
        log.info(f"Running game resolution (L1→L2→L3) for source #{source_id}")

        try:
            # Fetch game registry from VPS and build a temp DB
            session = self._build_resolver_session()
            if session is None:
                log.warning("Could not build resolver session — skipping game resolution")
                return None

            try:
                result = resolve(local_path, local_path.name, session, llm=llm)
            finally:
                session.close()

            if not result or not result.game_name or result.confidence < 0.5:
                log.info(
                    f"Game resolution inconclusive for #{source_id}: "
                    f"game={result.game_name if result else 'None'} "
                    f"conf={result.confidence if result else 0} "
                    f"method={result.method if result else 'none'}"
                )
                return None

            log.info(
                f"Resolved #{source_id} → '{result.game_name}' "
                f"(method={result.method}, conf={result.confidence:.2f})"
            )

            # Report to VPS
            resp = self.client.post(
                f"/api/gameplays/{source_id}/resolve-game",
                json={
                    "game_name": result.game_name,
                    "method": result.method,
                    "confidence": result.confidence,
                    "notes": result.notes,
                    "capture_source": source.get("capture_source") or result.capture_source,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("updated"):
                    log.info(
                        f"VPS updated source #{source_id} → game_id={data.get('game_id')} "
                        f"({data.get('game_name')})"
                    )
                    return data.get("game_id")
                else:
                    log.info(f"VPS did not update source #{source_id}: {data.get('reason')}")
            else:
                log.warning(f"VPS rejected game resolution for #{source_id}: {resp.status_code}")

        except Exception as e:
            log.error(f"Game resolution failed for #{source_id}: {e}")

        return None

    def _build_resolver_session(self):
        """Build a temp SQLite DB with games + aliases from VPS for the resolver.

        Returns a SQLAlchemy session, or None on failure.
        """
        import tempfile
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        try:
            resp = self.client.get("/api/games/registry")
            if resp.status_code != 200:
                log.warning(f"Failed to fetch game registry: {resp.status_code}")
                return None
            data = resp.json()
        except Exception as e:
            log.warning(f"Error fetching game registry: {e}")
            return None

        from gpcg.core.models import Base
        from gpcg.domains.games.models import Game, GameAlias
        import gpcg.core.models  # noqa: side effect: register all tables
        import gpcg.domains.games.models  # noqa: side effect: register games tables

        tmpdir = tempfile.mkdtemp(prefix="gpcg_resolver_")
        db_path = Path(tmpdir) / "resolver.db"
        engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
        session = SessionLocal()

        try:
            for g in data.get("games", []):
                session.add(Game(
                    id=g["id"],
                    canonical_name=g["canonical_name"],
                    slug=g.get("slug", ""),
                    camera_type=g.get("camera_type", "unknown"),
                ))
            for a in data.get("aliases", []):
                session.add(GameAlias(
                    game_id=a["game_id"],
                    alias=a["alias"],
                ))
            session.commit()
            log.info(
                f"Resolver DB: {len(data.get('games', []))} games, "
                f"{len(data.get('aliases', []))} aliases"
            )
            return session
        except Exception:
            session.close()
            raise

    def _ollama_available(self) -> bool:
        """Check if Ollama is running locally."""
        import requests
        try:
            r = requests.get(f"{self.config.ollama_url}/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    # ── Submit mapping result ────────────────────────────────────────────────

    def submit_mapping_result(
        self,
        source_id: int,
        events: list[dict],
        analysis_version: str = "v1",
        config_hash: str = "",
        compatibility: Optional[dict] = None,
        media_info: Optional[dict] = None,
    ) -> dict:
        """Send gameplay analysis events to VPS.

        Args:
            media_info: Optional dict with duration, width, height, fps, codec,
                has_audio from ffprobe. Synced back to the GameplaySource so
                the VPS has accurate media metadata without probing the file.
        """
        payload = {
            "events": events,
            "analysis_version": analysis_version,
            "config_hash": config_hash,
            "compatibility": compatibility or {},
        }
        if media_info:
            payload.update(media_info)
        resp = self.client.post(
            f"/api/gameplays/{source_id}/mapping-result",
            json=payload,
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
                    self.send_status("busy", f"Sincronizando {filename}")
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

                self._current_job = None
                self.send_status("online", "Idle")

            except KeyboardInterrupt:
                log.info("Worker stopping (KeyboardInterrupt)...")
                break
            except Exception as e:
                log.error(f"Main loop error: {e}", exc_info=True)
                time.sleep(self.config.poll_interval)

    def _maybe_auto_collect(self) -> None:
        """Auto-trigger content collection every N hours if no collection job is active.

        Uses gpcg_content_collection_interval_hours from config (default: 6h).
        Creates a content_collect job on the VPS via the worker-auth endpoint.
        The VPS deduplicates (blocks if a content_collect job is already queued/running).
        """
        import time as _time
        now = _time.time()
        if now - self._last_collection_time < self._collection_interval_sec:
            return

        # Check if there's already a content_collect job queued/running on VPS
        try:
            resp = self.client.get("/api/jobs?type=content_collect&status=queued,running&limit=1")
            if resp.status_code == 200:
                data = resp.json()
                jobs = data if isinstance(data, list) else data.get("jobs", [])
                if jobs:
                    # Already collecting — wait
                    self._last_collection_time = now  # reset timer to avoid spamming
                    return
        except Exception:
            pass  # non-fatal — try to create the job anyway

        # Trigger content collection via worker-auth endpoint
        try:
            resp = self.client.post("/api/automation/trigger-content-collection")
            if resp.status_code == 200:
                log.info(f"Auto content collection triggered (interval={self._collection_interval_sec/3600:.0f}h)")
                self._last_collection_time = now
            elif resp.status_code == 409:
                # Already queued — reset timer
                self._last_collection_time = now
            else:
                log.warning(f"Auto content collection failed: {resp.status_code} {resp.text}")
        except Exception as e:
            log.warning(f"Auto content collection error: {e}")

    def _check_automations(self) -> None:
        """Check if any running automation needs a new job created.

        V2 flow:
        1. POST /api/automation/check — VPS returns pending automations
           (running, no active job, has gameplays, YouTube connected)
        2. For each pending automation, GET /api/automation/editorial-data/{user_id}
           — VPS returns inventory + history + channel profile
        3. Run EditorialStrategyService locally (with LLM/Ollama)
        4. POST /api/automation/create-job — VPS creates the job with
           automation config (subtitle/transition/voice settings)
        """
        try:
            resp = self.client.post("/api/automation/check")
            if resp.status_code != 200:
                return
            data = resp.json()
            pending = data.get("pending", [])
            if not pending:
                return

            for item in pending:
                user_id = item["user_id"]
                idea_queue = item.get("idea_queue", [])
                queue_mode = item.get("queue_mode", "automatic")
                if idea_queue:
                    # User has curated ideas in queue — consume directly (no LLM needed)
                    self._consume_idea_queue(user_id)
                elif queue_mode == "manual":
                    # V3: Manual mode — do NOT auto-generate when queue is empty.
                    # The user explicitly wants to curate every video.
                    log.info(f"User {user_id}: queue empty + manual mode — skipping")
                else:
                    # Automatic mode — fall back to editorial decision
                    self._make_editorial_decision(user_id)
        except Exception as e:
            log.warning(f"Automation check failed: {e}")

    def _consume_idea_queue(self, user_id: int) -> None:
        """Create a job from the user's idea queue (no LLM editorial decision needed)."""
        try:
            resp = self.client.post("/api/automation/consume-queue", json={"user_id": user_id})
            if resp.status_code == 200:
                job_data = resp.json()
                log.info(f"Idea queue: created job #{job_data.get('job_id')} for user {user_id}")
            elif resp.status_code == 409:
                # Queue empty or active job exists — not an error
                pass
            else:
                log.warning(f"Consume idea queue failed for user {user_id}: {resp.status_code} {resp.text}")
        except Exception as e:
            log.warning(f"Consume idea queue for user {user_id} failed: {e}")

    def _make_editorial_decision(self, user_id: int) -> None:
        """Fetch editorial data from VPS, decide locally with LLM, create job."""
        try:
            # 1. Fetch editorial data from VPS
            resp = self.client.get(f"/api/automation/editorial-data/{user_id}")
            if resp.status_code != 200:
                log.warning(f"Failed to fetch editorial data for user {user_id}: {resp.status_code}")
                return
            data = resp.json()
            inventory = data.get("inventory", [])
            history = data.get("history", {})
            channel_context = data.get("channel_context", "")
            general_ideas = data.get("general_ideas", [])

            if not inventory:
                log.info(f"No games in inventory for user {user_id}")
                return

            # 2. Run editorial decision locally (with LLM)
            from gpcg.infrastructure.llm import LLMClient
            from gpcg.application.editorial_strategy import (
                EditorialStrategyService,
                EditorialDecision,
                GameInventory,
            )

            llm = LLMClient()
            editorial = EditorialStrategyService(llm=llm)

            # Reconstruct GameInventory objects from the API data
            inventories = []
            for inv_data in inventory:
                inv = GameInventory(
                    game_id=inv_data["game_id"],
                    game_name=inv_data["game_name"],
                )
                inv.gameplay_sources_ready = inv_data.get("gameplay_sources_ready", 0)
                inv.gameplay_clips_available = inv_data.get("gameplay_clips_available", 0)
                inv.total_gameplay_duration = inv_data.get("total_gameplay_duration", 0.0)
                inv.gameplay_sources_total = inv_data.get("gameplay_sources_total", 0)
                inv.facts_available = inv_data.get("facts_available", 0)
                inv.facts_unused = inv_data.get("facts_unused", 0)
                inv.knowledge_chunks = inv_data.get("knowledge_chunks", 0)
                inv.knowledge_items = inv_data.get("knowledge_items", 0)
                inv.videos_produced = inv_data.get("videos_produced", 0)
                inv.recent_topics = inv_data.get("recent_topics", [])
                inventories.append(inv)

            # Use LLM to decide (or heuristic fallback)
            # V2: Pass ALL inventories (not just producible) + general_ideas
            # so the LLM can also choose curiosity_short with a general idea.
            # The LLM prompt explains both options clearly.
            try:
                decision = editorial._llm_decision_from_data(
                    inventories, history, channel_context,
                    general_ideas=general_ideas,
                )
            except Exception as e:
                log.warning(f"LLM editorial decision failed: {e}, using heuristic")
                decision = editorial._heuristic_decision(inventories, history)

            if not decision.success:
                log.info(f"Editorial decision not successful: {decision.error}")
                return

            # Pick a fact or knowledge_item for the chosen game
            if decision.game_id:
                chosen_inv = next((g for g in inventory if g["game_id"] == decision.game_id), None)
                if chosen_inv:
                    # V2: Prefer KnowledgeItems if available (content ideas)
                    ki_list = chosen_inv.get("knowledge_items_list", [])
                    if ki_list:
                        decision.fact_id = None  # KI will be picked by ContentPlanningService
                    elif chosen_inv.get("facts"):
                        recent_fact_ids = set(history.get("recent_fact_ids", []))
                        fresh_facts = [f for f in chosen_inv["facts"] if f["id"] not in recent_fact_ids]
                        if fresh_facts:
                            decision.fact_id = fresh_facts[0]["id"]
                        elif chosen_inv["facts"]:
                            decision.fact_id = chosen_inv["facts"][0]["id"]

            log.info(
                f"Editorial decision for user {user_id}: "
                f"type={decision.job_type} game_id={decision.game_id} "
                f"bg_game_id={decision.background_game_id} "
                f"fact_id={decision.fact_id} reason={decision.reason[:80]}"
            )

            # 3. Create the job on VPS via API
            create_resp = self.client.post("/api/automation/create-job", json={
                "user_id": user_id,
                "game_id": decision.game_id,
                "fact_id": decision.fact_id,
                "job_type": decision.job_type,
                "background_game_id": decision.background_game_id,
                "topic_hint": decision.topic_hint,
                "reason": decision.reason,
            })
            if create_resp.status_code == 200:
                job_data = create_resp.json()
                log.info(f"Automation created job #{job_data.get('job_id')} for user {user_id}")
            else:
                log.warning(f"Failed to create job from decision: {create_resp.status_code} {create_resp.text}")
        except Exception as e:
            log.warning(f"Editorial decision for user {user_id} failed: {e}")

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

        source_id = source["id"]
        filename = source["filename"]
        expected_hash = source.get("file_hash", "")
        expected_size = source.get("file_size", 0)

        # Local path: /ToshibaHD/gpcg/gameplays/{source_id}_{filename}
        local_path = self.storage_root / "gameplays" / f"{source_id}_{filename}"

        # Cooperative cancellation check before heavy work
        if self.check_job_cancelled(job_id):
            raise JobCancelledError(job_id)

        # Stage 1: Download (skip if file already exists locally with matching hash)
        self.update_job_status(job_id, status="running", stage="download", progress=0.05)

        if local_path.exists() and local_path.stat().st_size > 0:
            # File already exists locally — verify checksum before reusing
            if self._verify_local_file(local_path, expected_hash):
                log.info(f"Reusing existing local file for {filename} (checksum OK)")
            else:
                log.warning(f"Local file exists but checksum mismatch — re-downloading")
                local_path.unlink()
                local_path = self.download_gameplay(source)
        else:
            local_path = self.download_gameplay(source)

        # Stage 2: Confirm download (checksum) → VPS deletes temp file
        # Skip if temp file was already deleted (re-processing a job)
        self.update_job_status(job_id, status="running", stage="confirm_download", progress=0.10)
        try:
            confirmed = self.confirm_download(source, local_path)
            if not confirmed:
                self.submit_job_result(job_id, status="failed", error="Checksum mismatch")
                return
        except Exception as e:
            # Temp file may have been deleted already (re-processing after restart)
            # If local file exists and hash is valid, continue with mapping
            if local_path.exists() and self._verify_local_file(local_path, expected_hash):
                log.warning(f"Confirm-download failed (temp already deleted?): {e} — continuing with local file")
            else:
                self.submit_job_result(job_id, status="failed", error=f"Download confirm failed: {e}")
                return

        # Stage 2b: VLM game resolution (if not already resolved with high confidence)
        game_id = source.get("game_id")
        resolution_confidence = source.get("resolution_confidence", 0.0) or 0.0
        resolution_method = source.get("resolution_method", "unknown") or "unknown"

        if not game_id or resolution_confidence < 0.6:
            log.info(
                f"Source #{source_id} needs VLM game resolution "
                f"(game_id={game_id}, method={resolution_method}, conf={resolution_confidence})"
            )
            game_id = self._try_vlm_resolution(source_id, local_path, source)
        else:
            log.debug(f"Source #{source_id} already resolved (game_id={game_id}, conf={resolution_confidence})")

        # Stage 3: Run GameplayAnalyzer locally
        self.update_job_status(job_id, status="running", stage="mapping", progress=0.15)
        self.send_status("busy", f"Mapeando {source['filename']}", job_id=job_id)

        from gpcg.application.gameplay_analyzer import GameplayAnalyzer
        from gpcg.domain.gameplay_events import AnalysisConfig
        from gpcg.config import get_settings

        settings = get_settings()

        # Determine camera_type from the game (if linked)
        camera_type = "unknown"
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

        # Stage 4: Submit mapping result (events + media metadata)
        self.update_job_status(job_id, status="running", stage="mapping", progress=0.90)
        events = [e.to_dict() for e in timeline.events]

        # Compute compatibility flags
        compatibility = {"game_related": True, "general_topic": True}

        # Send media metadata (duration, has_audio) back to VPS so the
        # source record has accurate info without the VPS probing the
        # (now-deleted) temp file. EventTimeline has duration + has_audio.
        media_info = {
            "duration": timeline.duration,
            "has_audio": timeline.has_audio,
        }

        self.submit_mapping_result(
            source_id=source["id"],
            events=events,
            analysis_version=timeline.analysis_version,
            config_hash=timeline.config_hash,
            compatibility=compatibility,
            media_info=media_info,
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

    # ── V2: Game Enrichment + Content Collection ─────────────────────────────
    # These jobs run on the local PC (not VPS) because:
    # 1. Ollama (LLM) is only available locally for lore generation + scoring
    # 2. Wikidata/Wikipedia block VPS datacenter IPs (403/429)
    # 3. Results are synced back to VPS via dedicated endpoints

    def _process_game_enrich_job(self, job: dict) -> None:
        """Process a game_enrich job: fetch Wikidata/Wikipedia → generate lore → sync.

        Runs entirely locally (Wikidata + Wikipedia + Ollama for lore).
        Syncs the enriched Game data back to VPS via /jobs/{id}/sync-enrichment.
        """
        job_id = job["id"]
        game_id = job.get("game_id")
        if not game_id:
            self.submit_job_result(job_id, status="failed", error="No game_id in job")
            return

        self.update_job_status(job_id, status="running", stage="enrichment", progress=0.1)
        self.send_status("busy", f"Enriquecendo jogo #{game_id}", job_id=job_id)

        from gpcg.application.game_enrichment import fetch_enrichment_data
        from gpcg.infrastructure.llm import LLMClient

        # Get game name from job data or fetch from VPS API
        game_name = job.get("game", {}).get("canonical_name", "")
        if not game_name:
            try:
                resp = self.client.get(f"/api/games/{game_id}")
                if resp.status_code == 200:
                    game_name = resp.json().get("canonical_name", "")
            except Exception:
                pass

        if not game_name:
            self.submit_job_result(job_id, status="failed", error="Could not determine game name")
            return

        log.info(f"Enriching game '{game_name}' (id={game_id})")

        # Run enrichment locally (Wikidata + Wikipedia + LLM lore) — headless, no DB
        try:
            llm = LLMClient()
            result = fetch_enrichment_data(game_name, llm=llm)
        except Exception as e:
            log.exception(f"Enrichment failed for '{game_name}': {e}")
            self._sync_enrichment(job_id, enrichment_error=str(e))
            self.submit_job_result(job_id, status="failed", error=str(e))
            return

        if not result.success:
            log.warning(f"Enrichment failed for '{game_name}': {result.error}")
            self._sync_enrichment(job_id, enrichment_error=result.error)
            self.submit_job_result(job_id, status="failed", error=result.error)
            return

        # Sync enriched data back to VPS
        self.update_job_status(job_id, status="running", stage="sync", progress=0.9)
        sync_data = {
            "description": result.description,
            "developer": result.developer,
            "publisher": result.publisher,
            "franchise": result.franchise,
            "genres": result.genres or [],
            "themes": result.themes or [],
            "lore_summary": result.lore_summary,
            "release_date": result.release_date.isoformat() if result.release_date else None,
            "external_ids": result.external_ids or {},
            "aliases": result.aliases or [],
        }
        sync_resp = self._sync_enrichment(job_id, **sync_data)
        log.info(f"Enrichment synced to VPS for '{game_name}': {sync_resp}")

        # Done
        self.update_job_status(job_id, status="running", stage="done", progress=1.0)
        self.submit_job_result(job_id, status="completed", artifacts={
            "enriched": True,
            "game_id": game_id,
            "developer": result.developer,
            "franchise": result.franchise,
        })
        log.info(f"Game enrichment job #{job_id} completed for '{game_name}'")

    def _sync_enrichment(self, job_id: int, enrichment_error: str = None, **kwargs) -> dict:
        """Send enrichment results to VPS."""
        payload = {"enrichment_error": enrichment_error} if enrichment_error else kwargs
        resp = self.client.post(f"/api/jobs/{job_id}/sync-enrichment", json=payload)
        resp.raise_for_status()
        return resp.json()

    def _process_content_collect_job(self, job: dict) -> None:
        """Process a content_collect job: collect RSS → score → sync.

        Runs entirely locally (RSS + Ollama for editorial scoring).
        Syncs collected KnowledgeItems back to VPS via /jobs/{id}/sync-knowledge-items.
        """
        job_id = job["id"]

        self.update_job_status(job_id, status="running", stage="content_collect", progress=0.1)
        self.send_status("busy", "Coletando conteúdo (RSS)", job_id=job_id)

        from gpcg.application.content_collectors import collect_rss_items
        from gpcg.infrastructure.llm import LLMClient

        # Get game names from VPS (games that have gameplay sources)
        game_names = []
        try:
            resp = self.client.get("/api/games")
            if resp.status_code == 200:
                games = resp.json()
                if isinstance(games, list):
                    game_names = [g.get("canonical_name") for g in games if g.get("canonical_name")]
                elif isinstance(games, dict) and "games" in games:
                    game_names = [g.get("canonical_name") for g in games["games"] if g.get("canonical_name")]
        except Exception as e:
            log.warning(f"Could not fetch game list from VPS: {e}")

        # V2: Try to get editorial brief (expanded search queries) from VPS
        # This replaces basic "{game} game" queries with editorial queries
        # like "Bully hidden secrets", "Bully easter egg", "Bully story lore"
        search_queries = None
        user_id = job.get("user_id")
        if user_id:
            try:
                resp = self.client.get(f"/api/automation/editorial-brief/{user_id}")
                if resp.status_code == 200:
                    brief_data = resp.json()
                    search_queries = brief_data.get("search_queries", [])
                    if search_queries:
                        log.info(f"Editorial brief: {len(search_queries)} expanded queries "
                                 f"(templates={brief_data.get('active_templates', [])})")
                    else:
                        log.info("Editorial brief empty — falling back to basic game queries")
            except Exception as e:
                log.warning(f"Could not fetch editorial brief: {e}")

        log.info(f"Collecting RSS for games: {game_names} (editorial_queries={len(search_queries) if search_queries else 0})")

        # Collect RSS feeds locally (headless, no DB)
        try:
            items = collect_rss_items(
                game_names=game_names if game_names else None,
                search_queries=search_queries if search_queries else None,
            )
            log.info(f"Collected {len(items)} items from RSS feeds")
        except Exception as e:
            log.exception(f"RSS collection failed: {e}")
            self._sync_knowledge_items(job_id, error=str(e))
            self.submit_job_result(job_id, status="failed", error=str(e))
            return

        if not items:
            log.info("No items collected from RSS")
            self._sync_knowledge_items(job_id, items=[], cleaned_count=0)
            self.submit_job_result(job_id, status="completed", artifacts={"collected": 0})
            return

        # Score items with local LLM (5 editorial dimensions)
        self.update_job_status(job_id, status="running", stage="scoring", progress=0.3)
        from gpcg.application.knowledge_item_service import score_rss_item_headless
        try:
            llm = LLMClient()
        except Exception as e:
            log.warning(f"LLM init failed for scoring (using heuristic): {e}")
            llm = None

        scored_items = []
        rejected_count = 0
        for i, item in enumerate(items):
            score, rejection_reason = score_rss_item_headless(
                title=item.title,
                content=item.content,
                item_type=item.item_type,
                source_type=item.source_name or item.source_type,
                llm=llm,
            )
            item.editorial_score = score
            if rejection_reason:
                # Skip rejected items (clickbait/promotion/rumor) — don't sync to VPS
                rejected_count += 1
                if (i + 1) % 10 == 0:
                    progress = 0.3 + (i + 1) / len(items) * 0.5
                    self.update_job_status(job_id, status="running", stage="scoring", progress=progress)
                continue

            scored_items.append(item)

            if (i + 1) % 10 == 0 or i + 1 == len(items):
                progress = 0.3 + (i + 1) / len(items) * 0.5
                self.update_job_status(job_id, status="running", stage="scoring", progress=progress)

        if rejected_count > 0:
            log.info(f"Content collection: {rejected_count} items rejected by quality gate (not synced)")

        # Sync items back to VPS (VPS handles cleanup of old news)
        self.update_job_status(job_id, status="running", stage="sync", progress=0.9)
        sync_items = []
        for item in scored_items:
            sync_items.append({
                "title": item.title,
                "content": item.content,
                "item_type": item.item_type,
                "source_type": item.source_type,
                "source_url": item.source_url,
                "source_name": item.source_name,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "editorial_score": item.editorial_score,
                "franchise": item.franchise,
                "developer": item.developer,
                "game_id": item.game_id,
                "content_hash": item.content_hash,
                "tags": item.tags,
            })

        sync_resp = self._sync_knowledge_items(job_id, items=sync_items, cleaned_count=0)
        log.info(f"Content collection synced to VPS: {sync_resp}")

        # Done
        self.update_job_status(job_id, status="running", stage="done", progress=1.0)
        self.submit_job_result(job_id, status="completed", artifacts={
            "collected": len(scored_items),
            "synced": sync_resp.get("inserted", 0),
            "skipped": sync_resp.get("skipped", 0),
        })
        log.info(f"Content collection job #{job_id} completed: {len(scored_items)} items")

    def _sync_knowledge_items(self, job_id: int, items: list = None, cleaned_count: int = 0, error: str = None) -> dict:
        """Send collected KnowledgeItems to VPS."""
        payload = {
            "items": items or [],
            "cleaned_count": cleaned_count,
        }
        if error:
            payload["error"] = error
        resp = self.client.post(f"/api/jobs/{job_id}/sync-knowledge-items", json=payload)
        resp.raise_for_status()
        return resp.json()

    # ── Kids Idea System: discovery + scoring (run locally with LLM) ───────────

    def _process_kids_idea_discovery_job(self, job: dict) -> None:
        """Process a kids_idea_discovery job: run discovery locally → sync ideas.

        Runs AI ideation + topic library + seasonal locally (with Ollama LLM).
        Syncs the created ideas back to VPS via /jobs/{id}/sync-kids-ideas.
        """
        job_id = job["id"]
        artifacts = job.get("artifacts") or {}

        self.update_job_status(job_id, status="running", stage="discovery", progress=0.1)
        self.send_status("busy", "Descobrindo ideias Kids", job_id=job_id)

        # Fetch channel profile from VPS (worker-auth endpoint)
        try:
            resp = self.client.get(f"/api/workers/channel-profile/{job['user_id']}")
            if resp.status_code != 200:
                self.submit_job_result(job_id, status="failed", error="Could not fetch channel profile")
                return
            profile_data = resp.json()
        except Exception as e:
            self.submit_job_result(job_id, status="failed", error=f"Failed to fetch profile: {e}")
            return

        # Build a lightweight profile object for discovery
        from gpcg.domains.kids.discovery import KidsIdeaDiscovery
        from gpcg.infrastructure.llm import LLMClient

        class _Profile:
            """Minimal profile stub for headless discovery."""
            def __init__(self, data: dict):
                self.metadata_json = data.get("metadata_json") or data.get("metadata") or {}
                self.channel_description = data.get("channel_description", "")
                self.niche = data.get("niche", "")
                self.target_audience = data.get("target_audience", "")
                self.tone_of_voice = data.get("tone_of_voice", "")
                self.narrative_style = data.get("narrative_style", "")
                self.content_goals = data.get("content_goals", "")
                self.special_rules = data.get("special_rules", "")

            def to_prompt_context(self) -> str:
                parts = []
                if self.channel_description:
                    parts.append(f"Channel: {self.channel_description}")
                if self.niche:
                    parts.append(f"Niche: {self.niche}")
                if self.target_audience:
                    parts.append(f"Audience: {self.target_audience}")
                if self.tone_of_voice:
                    parts.append(f"Tone: {self.tone_of_voice}")
                if self.narrative_style:
                    parts.append(f"Style: {self.narrative_style}")
                if self.content_goals:
                    parts.append(f"Goals: {self.content_goals}")
                return " | ".join(parts) if parts else ""

        profile = _Profile(profile_data)

        # Init LLM (local Ollama)
        try:
            llm = LLMClient()
        except Exception as e:
            log.warning(f"LLM init failed for discovery: {e}")
            llm = None

        self.update_job_status(job_id, status="running", stage="discovery", progress=0.3)

        # Run discovery locally — no DB session, collect results in memory
        discovery = KidsIdeaDiscovery(llm=llm)

        # We need a DB session for create_idea's dedup check.
        # Use a local temp SQLite DB (same pattern as generation jobs).
        # But discovery's create_idea checks for duplicates in the DB,
        # and we don't have the VPS's ideas locally. So we'll collect
        # the raw ideas and send them to VPS for dedup + storage.
        categories = artifacts.get("categories")
        ideas_per_category = artifacts.get("ideas_per_category", 3)
        include_seasonal = artifacts.get("include_seasonal", True)
        include_topic_library = artifacts.get("include_topic_library", True)

        # Collect ideas without DB persistence
        sync_ideas: list[dict] = []

        from gpcg.domains.kids.topic_library import get_all_categories, get_category, get_seeds_for_category
        from gpcg.domains.kids.seasonal_calendar import get_active_seasonal

        # Determine categories
        if categories:
            cats = [get_category(c) for c in categories if get_category(c)]
            cats = [c for c in cats if c is not None]
        else:
            cats = get_all_categories()

        age_range = discovery._get_age_range(profile)
        channel_context = profile.to_prompt_context()

        # 1. AI Ideation
        self.update_job_status(job_id, status="running", stage="discovery", progress=0.4)
        for cat in cats:
            try:
                ai_ideas = discovery._ai_ideation(
                    cat, age_range, channel_context, ideas_per_category
                )
                for idea_data in ai_ideas:
                    sync_ideas.append({
                        "title": idea_data["title"],
                        "description": idea_data.get("description", ""),
                        "category": idea_data.get("category", cat.name),
                        "suggested_age_range": idea_data.get("suggested_age_range", age_range),
                        "source": "ai_ideation",
                        "source_metadata": {
                            "category": cat.name,
                            "channel_context": channel_context[:200],
                        },
                    })
            except Exception as e:
                log.warning(f"discovery.ai_ideation_failed: category={cat.name}, error={e}")

        # 2. Topic Library seeds
        self.update_job_status(job_id, status="running", stage="discovery", progress=0.6)
        if include_topic_library:
            for cat in cats:
                for seed in get_seeds_for_category(cat.name):
                    sync_ideas.append({
                        "title": seed.title_hint,
                        "description": seed.description,
                        "category": cat.name,
                        "suggested_age_range": age_range,
                        "source": "topic_library",
                        "source_metadata": {"category": cat.name, "seed": True},
                    })

        # 3. Seasonal
        self.update_job_status(job_id, status="running", stage="discovery", progress=0.8)
        if include_seasonal:
            seasonal_entries = get_active_seasonal()
            for entry in seasonal_entries:
                try:
                    seasonal_ideas = discovery._seasonal_ideation(
                        entry, age_range, channel_context
                    )
                    for idea_data in seasonal_ideas:
                        sync_ideas.append({
                            "title": idea_data["title"],
                            "description": idea_data.get("description", ""),
                            "category": idea_data.get("category", entry.category),
                            "suggested_age_range": idea_data.get("suggested_age_range", age_range),
                            "source": "seasonal",
                            "source_metadata": {
                                "seasonal_entry": entry.name,
                                "date": entry.date,
                            },
                        })
                except Exception as e:
                    log.warning(f"discovery.seasonal_failed: entry={entry.name}, error={e}")

        log.info(f"Kids discovery: collected {len(sync_ideas)} ideas locally")

        # 4. Safety + Scoring (run locally with LLM, same as content_collect does)
        self.update_job_status(job_id, status="running", stage="scoring", progress=0.85)
        from gpcg.domains.kids.safety_filter import KidsSafetyFilter
        from gpcg.domains.kids.scorer import KidsScorer

        strictness = float(
            (profile.metadata_json or {}).get("kids_safety_strictness", 0.7)
        )
        safety_filter = KidsSafetyFilter(llm=llm)
        scorer = KidsScorer(llm=llm)

        evaluated_ideas: list[dict] = []
        rejected_count = 0
        for idea_data in sync_ideas:
            try:
                safety_result = safety_filter.review(
                    title=idea_data["title"],
                    description=idea_data.get("description", ""),
                    age_range=idea_data.get("suggested_age_range", age_range),
                    strictness=strictness,
                )
                if not safety_result.safe:
                    rejected_count += 1
                    continue

                score_result = scorer.score(
                    title=idea_data["title"],
                    description=idea_data.get("description", ""),
                    age_range=idea_data.get("suggested_age_range", age_range),
                    category=idea_data.get("category", ""),
                    channel_context=channel_context,
                )

                idea_data["safety_score"] = safety_result.safety_score
                idea_data["safety_flags"] = safety_result.flags
                idea_data["safety_reviewed"] = True
                idea_data["editorial_score"] = score_result.editorial_score_0_100
                idea_data["age_fit_score"] = score_result.age_fit
                idea_data["educational_value"] = score_result.educational_value
                idea_data["curiosity_score"] = score_result.curiosity
                idea_data["visual_potential"] = score_result.visual_potential
                idea_data["final_score"] = score_result.final_score
                idea_data["score_breakdown"] = score_result.breakdown
                idea_data["evaluated"] = True
                evaluated_ideas.append(idea_data)
            except Exception as e:
                log.warning(f"discovery.score_failed: title='{idea_data['title'][:40]}', error={e}")
                # Keep the idea as discovered (no score) — better than dropping it
                idea_data["evaluated"] = False
                evaluated_ideas.append(idea_data)

        log.info(
            f"Kids discovery: {len(evaluated_ideas)} evaluated, "
            f"{rejected_count} rejected by safety"
        )

        # Sync ideas to VPS
        self.update_job_status(job_id, status="running", stage="sync", progress=0.9)
        try:
            sync_resp = self.client.post(
                f"/api/jobs/{job_id}/sync-kids-ideas",
                json={"ideas": evaluated_ideas},
            )
            sync_resp.raise_for_status()
            sync_data = sync_resp.json()
            log.info(f"Kids discovery synced to VPS: {sync_data}")
        except Exception as e:
            log.error(f"Failed to sync kids ideas to VPS: {e}")
            self.submit_job_result(job_id, status="failed", error=f"Sync failed: {e}")
            return

        # Done
        self.update_job_status(job_id, status="running", stage="done", progress=1.0)
        self.submit_job_result(job_id, status="completed", artifacts={
            "discovery_completed": True,
            "ideas_collected": len(sync_ideas),
            "created_count": sync_data.get("created_count", 0),
            "skipped_count": sync_data.get("skipped_count", 0),
        })
        log.info(f"Kids discovery job #{job_id} completed: {len(sync_ideas)} ideas collected")

    def _process_kids_idea_score_job(self, job: dict) -> None:
        """Process a kids_idea_score job: run safety + scoring locally → sync.

        Runs KidsSafetyFilter (LLM review) and KidsScorer locally (with Ollama).
        Syncs the results back to VPS via /jobs/{id}/sync-kids-score.
        """
        job_id = job["id"]
        artifacts = job.get("artifacts") or {}
        idea_id = artifacts.get("idea_id")
        if not idea_id:
            self.submit_job_result(job_id, status="failed", error="No idea_id in job artifacts")
            return

        self.update_job_status(job_id, status="running", stage="scoring", progress=0.1)
        self.send_status("busy", f"Avaliando ideia Kids #{idea_id}", job_id=job_id)

        # Fetch the idea from VPS (worker-auth endpoint)
        try:
            resp = self.client.get(f"/api/workers/kids-ideas/{idea_id}")
            if resp.status_code != 200:
                self.submit_job_result(job_id, status="failed", error=f"Could not fetch idea #{idea_id}")
                return
            idea_data = resp.json()
        except Exception as e:
            self.submit_job_result(job_id, status="failed", error=f"Failed to fetch idea: {e}")
            return

        # Fetch channel profile from VPS (worker-auth endpoint)
        try:
            resp = self.client.get(f"/api/workers/channel-profile/{job['user_id']}")
            if resp.status_code != 200:
                self.submit_job_result(job_id, status="failed", error="Could not fetch channel profile")
                return
            profile_data = resp.json()
        except Exception as e:
            self.submit_job_result(job_id, status="failed", error=f"Failed to fetch profile: {e}")
            return

        meta = profile_data.get("metadata_json") or profile_data.get("metadata") or {}
        strictness = float(meta.get("kids_safety_strictness", 0.7))
        age_range = idea_data.get("suggested_age_range") or str(
            meta.get("age_range", meta.get("kids_age_range", "3-6"))
        )

        # Build channel context
        channel_context = " | ".join(
            f"{k}: {v}" for k, v in [
                ("Channel", profile_data.get("channel_description", "")),
                ("Niche", profile_data.get("niche", "")),
                ("Audience", profile_data.get("target_audience", "")),
                ("Tone", profile_data.get("tone_of_voice", "")),
            ] if v
        )

        # Init LLM (local Ollama)
        try:
            llm = LLMClient()
        except Exception as e:
            log.warning(f"LLM init failed for scoring: {e}")
            llm = None

        self.update_job_status(job_id, status="running", stage="scoring", progress=0.3)

        # 1. Safety review
        from gpcg.domains.kids.safety_filter import KidsSafetyFilter
        safety_filter = KidsSafetyFilter(llm=llm)
        safety_result = safety_filter.review(
            title=idea_data["title"],
            description=idea_data.get("description", ""),
            age_range=age_range,
            strictness=strictness,
        )

        safety_dict = {
            "safe": safety_result.safe,
            "safety_score": safety_result.safety_score,
            "flags": safety_result.flags,
            "age_suitability": safety_result.age_suitability,
            "reason": safety_result.reason,
        }

        if not safety_result.safe:
            # Auto-reject — no need for scoring
            self.update_job_status(job_id, status="running", stage="sync", progress=0.9)
            try:
                sync_resp = self.client.post(
                    f"/api/jobs/{job_id}/sync-kids-score",
                    json={
                        "idea_id": idea_id,
                        "safety": safety_dict,
                        "scoring": {},
                        "status": "rejected",
                    },
                )
                sync_resp.raise_for_status()
            except Exception as e:
                self.submit_job_result(job_id, status="failed", error=f"Sync failed: {e}")
                return

            self.update_job_status(job_id, status="running", stage="done", progress=1.0)
            self.submit_job_result(job_id, status="completed", artifacts={
                "idea_id": idea_id,
                "safe": False,
                "rejected": True,
            })
            log.info(f"Kids scoring job #{job_id}: idea #{idea_id} rejected (safety)")
            return

        # 2. Scoring
        self.update_job_status(job_id, status="running", stage="scoring", progress=0.6)
        from gpcg.domains.kids.scorer import KidsScorer
        scorer = KidsScorer(llm=llm)
        score_result = scorer.score(
            title=idea_data["title"],
            description=idea_data.get("description", ""),
            age_range=age_range,
            category=idea_data.get("category", ""),
            channel_context=channel_context,
        )

        scoring_dict = {
            "editorial_quality": score_result.editorial_quality,
            "age_fit": score_result.age_fit,
            "educational_value": score_result.educational_value,
            "curiosity": score_result.curiosity,
            "visual_potential": score_result.visual_potential,
            "simplicity": score_result.simplicity,
            "final_score": score_result.final_score,
            "editorial_score_0_100": score_result.editorial_score_0_100,
            "reason": score_result.reason,
            "breakdown": score_result.breakdown,
        }

        # Sync results to VPS
        self.update_job_status(job_id, status="running", stage="sync", progress=0.9)
        try:
            sync_resp = self.client.post(
                f"/api/jobs/{job_id}/sync-kids-score",
                json={
                    "idea_id": idea_id,
                    "safety": safety_dict,
                    "scoring": scoring_dict,
                    "status": "evaluated",
                },
            )
            sync_resp.raise_for_status()
        except Exception as e:
            self.submit_job_result(job_id, status="failed", error=f"Sync failed: {e}")
            return

        # Done
        self.update_job_status(job_id, status="running", stage="done", progress=1.0)
        self.submit_job_result(job_id, status="completed", artifacts={
            "idea_id": idea_id,
            "safe": True,
            "final_score": score_result.final_score,
            "editorial_score_0_100": score_result.editorial_score_0_100,
        })
        log.info(f"Kids scoring job #{job_id}: idea #{idea_id} scored (final={score_result.final_score:.2f})")

    # ── Kids asset processing: video → FFprobe + thumbnail ────────────────────

    def _process_kids_asset_process_job(self, job: dict) -> None:
        """Process a kids_asset_process job: download video → FFprobe → thumbnail → sync.

        Downloads the uploaded video from the VPS, runs FFprobe to extract
        metadata (duration, dimensions, codec, audio presence), generates
        a thumbnail with FFmpeg, uploads the thumbnail back, and syncs
        the metadata to the VPS so the StoryAsset is marked ``ready``.

        If FFprobe/FFmpeg are not available, the asset is still marked
        ready with zero metadata — the pipeline can still use the raw
        video file. This is a graceful degradation, not a hard failure.
        """
        job_id = job["id"]
        artifacts = job.get("artifacts") or {}
        asset_id = artifacts.get("asset_id")
        if not asset_id:
            self.submit_job_result(job_id, status="failed", error="No asset_id in job artifacts")
            return

        filename = artifacts.get("filename", f"asset_{asset_id}")
        file_hash = artifacts.get("file_hash", "")

        self.update_job_status(job_id, status="running", stage="download", progress=0.1)
        self.send_status("busy", f"Processando mídia Kids: {filename}", job_id=job_id)

        # Download the video from VPS
        kids_dir = self.storage_root / "kids_assets"
        kids_dir.mkdir(parents=True, exist_ok=True)

        # Use storage_key from artifacts if available, else derive from hash+filename
        storage_key = artifacts.get("storage_key", f"{file_hash[:8]}_{filename}")
        local_path = kids_dir / storage_key

        if not local_path.exists():
            try:
                log.info(f"Downloading Kids video asset #{asset_id} ({filename}) from VPS...")
                resp = self.client.get(f"/api/kids/assets/{asset_id}/download")
                resp.raise_for_status()
                with open(local_path, "wb") as f:
                    f.write(resp.content)
                log.info(f"Downloaded Kids video asset #{asset_id} → {local_path}")
            except Exception as e:
                error = f"Failed to download asset #{asset_id}: {e}"
                log.error(error)
                self._submit_kids_asset_error(job_id, asset_id, error)
                return
        else:
            log.info(f"Kids video asset #{asset_id} already cached locally: {local_path}")

        # Verify checksum if we have a hash
        if file_hash:
            sha256 = hashlib.sha256()
            with open(local_path, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    sha256.update(chunk)
            actual_hash = sha256.hexdigest()
            if actual_hash.lower() != file_hash.lower():
                error = f"Checksum mismatch for asset #{asset_id}: expected {file_hash[:16]}... got {actual_hash[:16]}..."
                log.error(error)
                self._submit_kids_asset_error(job_id, asset_id, error)
                return

        self.update_job_status(job_id, status="running", stage="mapping", progress=0.4)

        # Run FFprobe to extract metadata
        width, height, duration, codec, has_audio = 0, 0, 0.0, "", False
        try:
            width, height, duration, codec, has_audio = self._ffprobe_video(local_path)
            log.info(
                f"FFprobe asset #{asset_id}: {width}x{height} {duration:.1f}s "
                f"codec={codec} audio={has_audio}"
            )
        except Exception as e:
            log.warning(f"FFprobe failed for asset #{asset_id}: {e} — continuing with zero metadata")

        self.update_job_status(job_id, status="running", stage="render", progress=0.7)

        # Generate thumbnail with FFmpeg (best-effort, non-fatal)
        thumbnail_key = ""
        try:
            thumbnail_key = self._generate_thumbnail(local_path, asset_id, kids_dir)
            if thumbnail_key:
                # Upload thumbnail to VPS
                thumb_path = kids_dir / thumbnail_key
                with open(thumb_path, "rb") as f:
                    files = {"file": (thumbnail_key, f, "image/jpeg")}
                    resp = self.client.post(
                        f"/api/kids/assets/{asset_id}/thumbnail",
                        files=files,
                    )
                    resp.raise_for_status()
                log.info(f"Uploaded thumbnail for asset #{asset_id}: {thumbnail_key}")
        except Exception as e:
            log.warning(f"Thumbnail generation failed for asset #{asset_id}: {e} — non-fatal")

        # Sync metadata to VPS
        self.update_job_status(job_id, status="running", stage="sync", progress=0.9)
        try:
            resp = self.client.post(
                f"/api/kids/assets/{asset_id}/process-result",
                json={
                    "asset_id": asset_id,
                    "width": width,
                    "height": height,
                    "duration": duration,
                    "codec": codec,
                    "has_audio": has_audio,
                    "thumbnail_key": thumbnail_key,
                    "error": "",
                },
            )
            resp.raise_for_status()
        except Exception as e:
            error = f"Failed to sync process result: {e}"
            log.error(error)
            self._submit_kids_asset_error(job_id, asset_id, error)
            return

        self.update_job_status(job_id, status="running", stage="done", progress=1.0)
        self.submit_job_result(job_id, status="completed", artifacts={
            "asset_id": asset_id,
            "media_kind": "video",
            "width": width,
            "height": height,
            "duration": duration,
            "has_audio": has_audio,
            "thumbnail_key": thumbnail_key,
        })
        log.info(f"Kids asset process job #{job_id}: asset #{asset_id} ready")

    def _submit_kids_asset_error(self, job_id: int, asset_id: int, error: str) -> None:
        """Submit a processing error to the VPS and mark the job as failed."""
        try:
            self.client.post(
                f"/api/kids/assets/{asset_id}/process-result",
                json={"asset_id": asset_id, "error": error},
            )
        except Exception:
            pass  # Best-effort — the job failure is reported below
        self.submit_job_result(job_id, status="failed", error=error)

    @staticmethod
    def _ffprobe_video(path: Path) -> tuple:
        """Run FFprobe on a video file and return (width, height, duration, codec, has_audio).

        Uses subprocess to call ffprobe (must be installed on the worker).
        Returns zeros/empty if ffprobe is not available or fails.
        """
        import subprocess as _sp
        import json as _json

        # Check if ffprobe is available
        try:
            _sp.run(["ffprobe", "-version"], capture_output=True, check=True)
        except (FileNotFoundError, _sp.CalledProcessError):
            raise RuntimeError("ffprobe not available on worker")

        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ]
        result = _sp.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe failed: {result.stderr[:200]}")

        data = _json.loads(result.stdout)
        width = height = 0
        duration = 0.0
        codec = ""
        has_audio = False

        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                width = int(stream.get("width", 0))
                height = int(stream.get("height", 0))
                codec = stream.get("codec_name", "")
            elif stream.get("codec_type") == "audio":
                has_audio = True

        fmt = data.get("format", {})
        duration = float(fmt.get("duration", 0.0))

        return width, height, duration, codec, has_audio

    @staticmethod
    def _generate_thumbnail(video_path: Path, asset_id: int, output_dir: Path) -> str:
        """Generate a thumbnail from a video using FFmpeg.

        Extracts a frame at 1 second (or 10% of duration for short clips).
        Returns the thumbnail filename (relative to output_dir), or empty
        string if generation failed.
        """
        import subprocess as _sp

        try:
            _sp.run(["ffmpeg", "-version"], capture_output=True, check=True)
        except (FileNotFoundError, _sp.CalledProcessError):
            return ""

        thumb_name = f"thumb_{asset_id}.jpg"
        thumb_path = output_dir / thumb_name

        # Extract frame at 1s (or 0.5s for very short videos)
        cmd = [
            "ffmpeg", "-y", "-i", str(video_path),
            "-ss", "00:00:01", "-frames:v", "1",
            "-vf", "scale=320:-1",
            "-q:v", "2",
            str(thumb_path),
        ]
        result = _sp.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0 or not thumb_path.exists():
            # Try at 0s (some videos are shorter than 1s)
            cmd = [
                "ffmpeg", "-y", "-i", str(video_path),
                "-ss", "00:00:00", "-frames:v", "1",
                "-vf", "scale=320:-1",
                "-q:v", "2",
                str(thumb_path),
            ]
            result = _sp.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0 or not thumb_path.exists():
                return ""

        return thumb_name if thumb_path.exists() else ""

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

    def _process_cleanup_gameplay_job(self, job: dict) -> None:
        """Process a cleanup_gameplay job: delete physical files from local storage.

        Removes the gameplay video file, analysis JSON, and any renders
        associated with the source from the worker's HD.
        """
        job_id = job["id"]
        artifacts = job.get("artifacts", {})
        if isinstance(artifacts, str):
            try:
                import json as _json
                artifacts = _json.loads(artifacts)
            except Exception:
                artifacts = {}
        source_id = artifacts.get("source_id") or job.get("gameplay_source_id")
        filename = artifacts.get("filename", "")

        self.update_job_status(job_id, status="running", stage="cleanup", progress=0.1)
        self.send_status("busy", f"Limpando gameplay #{source_id}", job_id=job_id)

        deleted_files: list[str] = []

        # 1. Delete gameplay video file: gameplays/{source_id}_{filename}
        if source_id and filename:
            gameplay_path = self.storage_root / "gameplays" / f"{source_id}_{filename}"
            if gameplay_path.exists():
                try:
                    gameplay_path.unlink()
                    deleted_files.append(str(gameplay_path))
                    log.info(f"Deleted gameplay file: {gameplay_path}")
                except OSError as e:
                    log.warning(f"Failed to delete {gameplay_path}: {e}")

        # 2. Delete analysis JSON: mapped/source_{source_id}_analysis.json
        if source_id:
            analysis_path = self.storage_root / "mapped" / f"source_{source_id}_analysis.json"
            if analysis_path.exists():
                try:
                    analysis_path.unlink()
                    deleted_files.append(str(analysis_path))
                    log.info(f"Deleted analysis file: {analysis_path}")
                except OSError as e:
                    log.warning(f"Failed to delete {analysis_path}: {e}")

        # 3. Delete any renders associated with this source
        # Renders are named by job_id, but we can clean orphans matching source_id
        renders_dir = self.storage_root / "renders"
        if renders_dir.exists():
            for render_file in renders_dir.glob(f"*source_{source_id}*"):
                try:
                    render_file.unlink()
                    deleted_files.append(str(render_file))
                    log.info(f"Deleted render file: {render_file}")
                except OSError as e:
                    log.warning(f"Failed to delete {render_file}: {e}")

        log.info(
            f"Cleanup job #{job_id} completed — deleted {len(deleted_files)} file(s) "
            f"for gameplay source #{source_id}"
        )

        self.update_job_status(job_id, status="running", stage="done", progress=1.0)
        self.submit_job_result(
            job_id,
            status="completed",
            artifacts={"deleted_files": deleted_files, "source_id": source_id},
        )

    def _process_cleanup_user_storage_job(self, job: dict) -> None:
        """Process a cleanup_user_storage job: delete ALL files for a user/domain.

        This is the comprehensive storage cleanup triggered by domain reset.
        It removes all files belonging to the user across all worker storage
        directories (gameplays, mapped, renders, outputs).

        SAFETY:
        - Only deletes files within self.storage_root (path traversal protection).
        - Only deletes files matching the user_id prefix (user_{user_id}_ or
          {source_id}_ where source_id belongs to the user — but since we're
          deleting everything, we use the user-scoped subdirectories).
        - Missing files are NOT errors (idempotent).
        - Does NOT touch files belonging to other users.
        """
        job_id = job["id"]
        artifacts = job.get("artifacts", {})
        if isinstance(artifacts, str):
            try:
                import json as _json
                artifacts = _json.loads(artifacts)
            except Exception:
                artifacts = {}

        user_id = artifacts.get("user_id") or job.get("user_id")
        old_domain = artifacts.get("old_domain", "games")
        if not user_id:
            self.submit_job_result(job_id, status="failed", error="No user_id in cleanup job")
            return

        self.update_job_status(job_id, status="running", stage="cleanup", progress=0.1)
        self.send_status("busy", f"Limpando storage {old_domain} do usuário #{user_id}", job_id=job_id)

        deleted_files: list[str] = []
        errors: list[str] = []

        # Resolve storage_root to an absolute path for safety checks
        storage_root = self.storage_root.resolve()

        def _safe_delete(path: Path) -> None:
            """Delete a file if it exists and is within storage_root. Idempotent."""
            try:
                resolved = path.resolve()
                # Path traversal protection: ensure the path is within storage_root
                if not str(resolved).startswith(str(storage_root)):
                    log.warning(f"Skipping file outside storage_root: {path}")
                    errors.append(f"Path outside storage_root: {path}")
                    return
                if resolved.is_file():
                    resolved.unlink()
                    deleted_files.append(str(resolved))
                    log.info(f"Deleted: {resolved}")
                elif resolved.is_dir():
                    # Only delete empty directories within storage_root
                    try:
                        resolved.rmdir()
                        log.info(f"Removed empty dir: {resolved}")
                    except OSError:
                        pass  # Directory not empty — leave it
            except OSError as e:
                log.warning(f"Failed to delete {path}: {e}")
                errors.append(str(e))

        # Domain-aware cleanup: only clean directories belonging to old_domain.
        # Games: gameplays/, mapped/
        # Kids: kids_assets/
        # Shared (cleaned for both): renders/, outputs/ (per-job, safe to clean)
        #
        # MULTI-USER SAFETY: The cleanup job carries "filenames" and
        # "storage_keys" lists in artifacts — only files matching those
        # names are deleted. This prevents deleting other users' files
        # in a multi-user deployment. If the lists are empty (legacy job
        # or no files to clean), we fall back to deleting all files in
        # the domain-specific directories (single-user backward compat).

        cleanup_filenames = set(artifacts.get("filenames", []))
        cleanup_storage_keys = set(artifacts.get("storage_keys", []))
        # Also derive filenames from storage_keys (worker may store by storage_key)
        cleanup_names = cleanup_filenames | cleanup_storage_keys

        def _should_delete(filename: str) -> bool:
            """Check if a file should be deleted (user-scoped)."""
            if not cleanup_names:
                # No list provided — backward compat: delete all
                return True
            # Check if the filename matches any known filename or storage_key
            # Files are stored as {source_id}_{original_filename} or {hash}_{filename}
            for name in cleanup_names:
                if filename == name or filename.endswith(name) or name in filename:
                    return True
            return False

        if old_domain == "games":
            # 1a. Clean gameplays directory (Games-specific)
            gameplays_dir = storage_root / "gameplays"
            if gameplays_dir.exists():
                for f in gameplays_dir.iterdir():
                    if f.is_file() and _should_delete(f.name):
                        _safe_delete(f)

            # 1b. Clean mapped directory — analysis JSONs (Games-specific)
            mapped_dir = storage_root / "mapped"
            if mapped_dir.exists():
                for f in mapped_dir.iterdir():
                    if f.is_file() and _should_delete(f.name):
                        _safe_delete(f)

        elif old_domain == "kids":
            # Clean kids_assets directory (Kids-specific)
            kids_assets_dir = storage_root / "kids_assets"
            if not kids_assets_dir.exists():
                kids_assets_dir = storage_root / "data" / "kids_assets"
            if kids_assets_dir.exists():
                for f in kids_assets_dir.iterdir():
                    if f.is_file() and _should_delete(f.name):
                        _safe_delete(f)

        # 3. Clean renders directory — intermediate render files (shared)
        renders_dir = storage_root / "renders"
        if renders_dir.exists():
            for f in renders_dir.iterdir():
                if f.is_file():
                    _safe_delete(f)

        # 4. Clean outputs directory — final output files (shared)
        outputs_dir = storage_root / "outputs"
        if outputs_dir.exists():
            for f in outputs_dir.iterdir():
                if f.is_file():
                    _safe_delete(f)

        # 5. Clean per-user voice directory if it exists
        from gpcg.config import get_settings
        try:
            local_settings = get_settings()
            user_voices = local_settings.voices_dir / f"user_{user_id}"
            if user_voices.exists():
                for f in user_voices.iterdir():
                    if f.is_file():
                        _safe_delete(f)
        except Exception as e:
            log.warning(f"Could not clean user voices: {e}")

        log.info(
            f"User storage cleanup #{job_id} completed — "
            f"deleted {len(deleted_files)} file(s) for user #{user_id}"
        )

        self.update_job_status(job_id, status="running", stage="done", progress=1.0)
        self.submit_job_result(
            job_id,
            status="completed",
            artifacts={
                "deleted_files": deleted_files,
                "user_id": user_id,
                "errors": errors,
                "deleted_count": len(deleted_files),
            },
        )

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
        local_storage_dir=storage_dir or os.environ.get("GPCG_WORKER_STORAGE", "./data/gpcg-worker"),
        capabilities=(capabilities or os.environ.get("GPCG_WORKER_CAPABILITIES", "mapping,generation")).split(","),
    )
    worker = RemoteWorker(config)
    worker.run()


def _heuristic_score(item) -> float:
    """Heuristic editorial score for RSS items (no LLM needed).

    Scores based on:
    - Title length (longer = more substantive, 10-80 chars is sweet spot)
    - Content length (longer = more detail)
    - Source reputation (established gaming sites score higher)
    - Item type (curiosity > news for editorial value)
    """
    score = 50.0  # baseline

    # Title length: sweet spot is 30-80 chars
    title_len = len(item.title)
    if 30 <= title_len <= 80:
        score += 10
    elif title_len < 15:
        score -= 10  # too short, probably clickbait
    elif title_len > 120:
        score -= 5  # too long

    # Content length: more content = more material for editorial
    content_len = len(item.content)
    if content_len > 500:
        score += 10
    elif content_len < 100:
        score -= 5  # too little content

    # Source reputation bonus
    reputable_sources = {"IGN", "GameSpot", "Polygon", "Eurogamer", "Rock Paper Shotgun", "Kotaku"}
    if item.source_name in reputable_sources:
        score += 8

    # Curiosity items score higher (evergreen content)
    if item.item_type == "curiosity":
        score += 5

    # Clamp to 0-100
    return max(0.0, min(100.0, score))
