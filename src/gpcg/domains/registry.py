"""Domain registry — the single dispatch point for domain pipelines.

This is the ONLY place where domain selection happens. The worker and
application services call ``get_generation_service(domain)`` instead of
directly importing a specific domain's pipeline.

Architecture:
    Core ← Domains (Games, Kids)
    Core does NOT import this module.
    Domains do NOT import each other.

This registry imports domain pipelines lazily (inside functions) to avoid
circular imports and to keep Core independent.
"""

from __future__ import annotations

from typing import Any

from gpcg.core.models import ContentDomain
from gpcg.logging import get_logger

log = get_logger(__name__)

# Domains that have a working pipeline implementation.
IMPLEMENTED_DOMAINS = {ContentDomain.games.value, ContentDomain.kids.value}


def get_generation_service(domain: str, session_scope: Any, progress_callback: Any = None) -> Any:
    """Return the generation service for the given domain.

    This is the single dispatch point. The worker calls this instead of
    directly importing GenerationService or KidsGenerationService.

    Args:
        domain: Domain string ("games", "kids", etc.)
        session_scope: A session scope context manager for DB access.
        progress_callback: Optional callback(stage, pct) for progress updates.

    Returns:
        A generation service instance with a ``run_job(job_id)`` method.

    Raises:
        ValueError: If the domain is not implemented.
    """
    if domain == ContentDomain.games.value:
        from gpcg.application.generation_service import GenerationService
        return GenerationService(session_scope=session_scope, progress_callback=progress_callback)

    if domain == ContentDomain.kids.value:
        from gpcg.domains.kids.pipeline import KidsGenerationService
        return KidsGenerationService(session_scope=session_scope, progress_callback=progress_callback)

    raise ValueError(
        f"Domain '{domain}' does not have a pipeline implementation. "
        f"Implemented domains: {sorted(IMPLEMENTED_DOMAINS)}"
    )


def is_domain_implemented(domain: str) -> bool:
    """Check if a domain has a working pipeline."""
    return domain in IMPLEMENTED_DOMAINS
