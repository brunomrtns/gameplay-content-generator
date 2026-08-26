"""Worker API routes — compatibility shim.

This module was the original home of all worker-facing API endpoints. It has
been split into the ``gpcg.api.workers`` package:

    gpcg/api/workers/
    ├── __init__.py      — composite router + re-exports
    ├── _common.py       — auth, schemas, shared helpers
    ├── _queue.py        — stale-job recovery + orphan cleanup
    ├── registry.py      — worker registration, heartbeat, status, list
    ├── jobs.py          — atomic claim, status update, result submission
    ├── file_transfer.py — gameplay/voice/kids-asset downloads, video upload
    ├── mapping.py       — mapping result, game resolution, mapping job, events
    ├── generation.py    — job data fetch + result sync
    ├── knowledge.py     — document indexing, enrichment, content collection
    ├── panel.py         — worker-auth panel endpoints
    └── kids.py          — Kids asset processing + idea system sync

This shim preserves backward compatibility for code that imports helpers,
schemas, or the router from ``gpcg.api.worker_routes``. New code should import
from ``gpcg.api.workers`` directly.

Architecture:
  - Worker registers → gets or creates Worker row
  - Worker sends heartbeats (frequent, just "I'm alive")
  - Worker sends status updates (less frequent, "what I'm doing")
  - Worker claims jobs (atomic, capability-matched)
  - Worker downloads gameplay files (streaming, token-authenticated)
  - Worker confirms download (checksum verification → VPS deletes temp file)
  - Worker reports mapping results (events only, no frames/crops)
  - Worker reports job results (video file or YouTube link)

All heavy processing (VLM, ASR, FFmpeg, rendering) happens on the worker.
The VPS only stores metadata and orchestrates.
"""

from __future__ import annotations

# ── Router (composite from the workers package) ──────────────────────────────
from gpcg.api.workers import router  # noqa: F401

# ── Common helpers, schemas, and auth ─────────────────────────────────────────
from gpcg.api.workers._common import (  # noqa: F401
    ConfirmDownloadRequest,
    JobClaimRequest,
    JobResultRequest,
    JobStatusUpdateRequest,
    MappingResultRequest,
    WorkerHeartbeatRequest,
    WorkerRegisterRequest,
    WorkerStatusRequest,
    _check_worker_offline,
    _ensure_dict,
    _generate_upload_token,
    _is_transient_error,
    _resolve_storage_path,
    _utcnow,
    _verify_worker_key,
    worker_auth,
)

# ── Queue helpers ─────────────────────────────────────────────────────────────
from gpcg.api.workers._queue import (  # noqa: F401
    _cleanup_orphan_gameplays,
    _requeue_stale_jobs_in_claim,
)

# ── Job serializers ───────────────────────────────────────────────────────────
from gpcg.api.workers.jobs import (  # noqa: F401
    _maybe_auto_publish,
    _serialize_document_for_job,
    _serialize_game_for_job,
    _serialize_gameplay_source_for_job,
    _serialize_job,
)

# ── File transfer schemas ────────────────────────────────────────────────────
from gpcg.api.workers.file_transfer import ConfirmDocumentDownloadRequest  # noqa: F401

# ── Mapping schemas ──────────────────────────────────────────────────────────
from gpcg.api.workers.mapping import GameResolutionRequest  # noqa: F401

# ── Generation schemas ───────────────────────────────────────────────────────
from gpcg.api.workers.generation import SyncResultRequest  # noqa: F401

# ── Knowledge schemas ────────────────────────────────────────────────────────
from gpcg.api.workers.knowledge import (  # noqa: F401
    ContentCollectionResultRequest,
    EnrichmentResultRequest,
    IndexingResultRequest,
    KnowledgeItemSyncItem,
)

# ── Kids schemas ─────────────────────────────────────────────────────────────
from gpcg.api.workers.kids import (  # noqa: F401
    KidsAssetProcessResult,
    KidsDiscoverySyncRequest,
    KidsIdeaSyncItem,
    KidsMappingResult,
    KidsMediaEventPayload,
    KidsScoreSyncRequest,
)
