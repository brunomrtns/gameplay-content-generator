"""Worker configuration and shared exceptions for the Compute Plane.

Extracted from ``remote_worker.py`` to keep the config dataclass, the
``JobCancelledError`` sentinel, and the GPU/CPU/RAM probing helpers in a
single dependency-free module that handlers and mixins can import without
pulling in the full ``RemoteWorker`` lifecycle.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Optional


class JobCancelledError(Exception):
    """Raised when the VPS reports that a job has been cancelled.

    The worker should catch this and abort processing immediately.
    The job's intermediate files will be cleaned up by the cleanup_user_storage
    job created during domain reset.
    """
    def __init__(self, job_id: int):
        self.job_id = job_id
        super().__init__(f"Job #{job_id} has been cancelled on the VPS")


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
