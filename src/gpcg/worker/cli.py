"""CLI entrypoint for the remote worker.

Extracted from ``remote_worker.py``. Contains ``run_remote_worker`` (called
by ``gpcg remote-worker``) and the standalone ``_heuristic_score`` helper.
"""

from __future__ import annotations

import os


def run_remote_worker(
    vps_url: str = "",
    worker_id: str = "",
    api_key: str = "",
    storage_dir: str = "",
    capabilities: str = "",
) -> None:
    """Run the remote worker. Called by the CLI."""
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
