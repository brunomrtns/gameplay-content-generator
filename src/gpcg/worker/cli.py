"""CLI entrypoint for the remote worker.

Extracted from ``remote_worker.py``. Contains ``run_remote_worker`` (called
by ``gpcg remote-worker``) and the standalone ``_heuristic_score`` helper.
"""

from __future__ import annotations

import os
import signal
import sys


def run_remote_worker(
    vps_url: str = "",
    worker_id: str = "",
    api_key: str = "",
    storage_dir: str = "",
    capabilities: str = "",
) -> None:
    """Run the remote worker. Called by the CLI."""
    # Load optional worker .env file (e.g. ~/.config/gpcg/worker.env) so the
    # worker picks up the same feature flags as the VPS without requiring every
    # env var to be duplicated in the systemd service file.
    _load_worker_env()

    from .config import WorkerConfig
    from .remote_worker import RemoteWorker

    config = WorkerConfig(
        vps_url=vps_url or os.environ.get("GPCG_VPS_URL", ""),
        worker_id=worker_id or os.environ.get("GPCG_WORKER_ID", ""),
        api_key=api_key or os.environ.get("GPCG_WORKER_API_KEY", ""),
        local_storage_dir=storage_dir or os.environ.get("GPCG_WORKER_STORAGE", "./data/gpcg-worker"),
        capabilities=(capabilities or os.environ.get("GPCG_WORKER_CAPABILITIES", "mapping,generation")).split(","),
    )
    worker = RemoteWorker(config)

    # Graceful shutdown on SIGTERM (systemd) and SIGINT (Ctrl+C)
    def _signal_handler(signum, frame):
        worker.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

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


def _load_worker_env() -> None:
    """Load optional worker .env file.

    Searches (in order):
      1. $GPCG_WORKER_ENV (explicit path)
      2. ~/.config/gpcg/worker.env
      3. <repo>/.env (development)

    Existing environment variables (e.g. from systemd) take precedence —
    dotenv only sets variables that are NOT already in os.environ.
    """
    from pathlib import Path

    candidates: list[Path] = []
    explicit = os.environ.get("GPCG_WORKER_ENV", "")
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(Path.home() / ".config" / "gpcg" / "worker.env")
    # Development fallback: repo root .env
    candidates.append(Path(__file__).resolve().parents[3] / ".env")

    for path in candidates:
        if path.is_file():
            try:
                from dotenv import load_dotenv

                load_dotenv(str(path), override=False)
                return
            except ImportError:
                # python-dotenv not installed — env vars must come from systemd
                return
            except Exception:
                pass
