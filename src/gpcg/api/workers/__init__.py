"""GPCG worker API — Control Plane ↔ Compute Plane communication.

This package contains the FastAPI routes that the VPS (Control Plane) exposes
to remote workers (Compute Plane). Workers authenticate via the ``X-Worker-Key``
header (shared secret), NOT BI Identity SSO (which is for human users).

The :data:`router` attribute is a composite :class:`fastapi.APIRouter` that
includes every worker-facing endpoint, registered under the ``/api`` prefix
(see ``gpcg.api.app``). Modules in this package register their endpoints on
the shared :data:`router` defined here.

Submodules:
    _common        — auth dependency, request/response schemas, shared helpers
    _queue         — stale-job recovery + orphan-gameplay cleanup helpers
    registry       — worker registration, heartbeat, status, list
    jobs           — atomic job claim, status update, result submission
    file_transfer  — gameplay/voice/kids-asset downloads, video upload
    mapping        — mapping result, game resolution, mapping job creation, events
    generation     — job data fetch (GET /jobs/{id}/data) + result sync
    knowledge      — document download/confirm, indexing/enrichment/collection sync
    panel          — worker-auth panel endpoints (no BI Identity)
    kids           — Kids Idea System sync endpoints
"""

from __future__ import annotations

from fastapi import APIRouter

# Composite router — aggregates endpoints from every worker submodule.
# ``gpcg.api.app`` imports this and mounts it under the ``/api`` prefix.
router = APIRouter(tags=["workers"])

# Re-export public helpers/schemas for backward compatibility with code that
# imports them from ``gpcg.api.worker_routes`` (the legacy module path).
from gpcg.api.workers._common import (  # noqa: E402,F401
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
from gpcg.api.workers._queue import (  # noqa: E402,F401
    _cleanup_orphan_gameplays,
    _requeue_stale_jobs_in_claim,
)

# ── Submodule routers ─────────────────────────────────────────────────────────
# Each submodule defines its own APIRouter and registers endpoints on it.
# We include them all here so the composite router exposes every endpoint.
# The legacy ``gpcg.api.worker_routes`` module is now a compatibility shim
# that re-exports helpers and schemas from this package.

from gpcg.api.workers.registry import router as _registry_router  # noqa: E402
from gpcg.api.workers.jobs import router as _jobs_router  # noqa: E402
from gpcg.api.workers.file_transfer import router as _file_transfer_router  # noqa: E402
from gpcg.api.workers.mapping import router as _mapping_router  # noqa: E402
from gpcg.api.workers.generation import router as _generation_router  # noqa: E402
from gpcg.api.workers.knowledge import router as _knowledge_router  # noqa: E402
from gpcg.api.workers.panel import router as _panel_router  # noqa: E402
from gpcg.api.workers.kids import router as _kids_router  # noqa: E402

router.include_router(_registry_router)
router.include_router(_jobs_router)
router.include_router(_file_transfer_router)
router.include_router(_mapping_router)
router.include_router(_generation_router)
router.include_router(_knowledge_router)
router.include_router(_panel_router)
router.include_router(_kids_router)

__all__ = ["router"]
