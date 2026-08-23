"""Knowledge routes — channel knowledge management endpoints.

Endpoints:
  Channel Profile:
    GET    /api/channel/profile           — get or auto-create user's channel profile
    PUT    /api/channel/profile           — update channel profile (free-text + structured)
    GET    /api/channel/presets           — list available editorial presets
    POST   /api/channel/preset            — apply an editorial preset to the profile
  Domain:
    GET    /api/channel/domains           — list available content domains
    POST   /api/channel/reset-domain      — destructive domain switch (reset channel)

NOTE: File-upload knowledge base (RAG) endpoints have been removed.
Channel knowledge is now managed via manual ideas (KnowledgeItem with
source_type="manual"). Legacy Document/KnowledgeChunk data is preserved
in the database but no longer used by the API.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from gpcg.application.editorial_profile_service import (
    apply_preset,
    list_presets,
    serialize_profile,
    update_structured_fields,
)
from gpcg.application.domain_reset_service import (
    reset_channel_domain,
    VALID_DOMAINS,
)
from gpcg.domains.registry import IMPLEMENTED_DOMAINS
from gpcg.core.models import ChannelProfile, ContentDomain, User
from gpcg.infrastructure.auth import get_current_user
from gpcg.infrastructure.database import get_db, session_scope
from gpcg.logging import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["knowledge"])


# ── Channel Profile ───────────────────────────────────────────────────────────


@router.get("/channel/profile")
def get_channel_profile(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the user's channel profile, auto-creating if it doesn't exist.

    V2: returns both free-text fields (for LLM prompts) and structured fields
    (for the Editorial Brief pipeline).
    """
    profile = db.query(ChannelProfile).filter(
        ChannelProfile.user_id == user.id
    ).first()

    if not profile:
        profile = ChannelProfile(user_id=user.id)
        with session_scope() as session:
            session.add(profile)
            session.flush()
            profile_id = profile.id
        profile = db.query(ChannelProfile).filter(
            ChannelProfile.id == profile_id
        ).first()

    return serialize_profile(profile)


@router.put("/channel/profile")
def update_channel_profile(
    data: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update the user's channel profile.

    All fields are optional — only provided fields are updated.

    V2: accepts both free-text fields (niche, tone_of_voice, ...) and
    structured fields (content_type_affinity, editorial_keywords,
    custom_feeds, gameplay_driven_collection, diversity_strictness).
    Learned fields (learned_preferences, production_history_summary)
    are NOT user-settable — they are populated by the feedback loop.
    """
    # Free-text fields (legacy)
    free_text_fields = [
        "channel_description", "niche", "target_audience",
        "tone_of_voice", "narrative_style", "content_goals", "special_rules",
    ]

    # Structured fields (V2)
    structured_fields = [
        "content_type_affinity", "editorial_keywords", "custom_feeds",
        "gameplay_driven_collection", "diversity_strictness",
    ]

    with session_scope() as session:
        # Update free-text fields
        profile = db.query(ChannelProfile).filter(
            ChannelProfile.user_id == user.id
        ).first()
        if not profile:
            profile = ChannelProfile(user_id=user.id)
            session.add(profile)
            session.flush()

        p = session.query(ChannelProfile).filter(
            ChannelProfile.id == profile.id
        ).first()
        for field in free_text_fields:
            if field in data:
                setattr(p, field, data[field])

        # Update metadata_json (Kids-specific fields like kids_age_range, categories, etc.)
        if "metadata" in data and isinstance(data["metadata"], dict):
            meta = dict(p.metadata_json or {})
            meta.update(data["metadata"])
            p.metadata_json = meta

        session.flush()

        # Update structured fields via the service (with validation)
        structured_data = {k: v for k, v in data.items() if k in structured_fields}
        if structured_data:
            update_structured_fields(session, user.id, **structured_data)

    return {"ok": True, "id": profile.id}


# ── Editorial Presets ─────────────────────────────────────────────────────────


@router.get("/channel/presets")
def get_editorial_presets(
    user: User = Depends(get_current_user),
):
    """List available editorial presets for channel configuration."""
    return {"presets": list_presets()}


@router.post("/channel/preset")
def apply_editorial_preset(
    data: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Apply an editorial preset to the user's channel profile.

    Body: {"preset": "curiosidades" | "noticias" | "lore" | "nostalgia" | "educacional"}

    Overwrites structured + free-text fields with preset defaults.
    Learned fields (learned_preferences, production_history_summary) are preserved.
    """
    preset_name = data.get("preset")
    if not preset_name:
        raise HTTPException(422, "preset is required")

    with session_scope() as session:
        try:
            profile = apply_preset(session, user.id, preset_name)
        except ValueError as e:
            raise HTTPException(422, str(e))

    return {"ok": True, "preset": preset_name, "profile_id": profile.id}


# ── Domain Management ─────────────────────────────────────────────────────────


@router.get("/channel/domains")
def list_domains(
    user: User = Depends(get_current_user),
):
    """List all available content domains.

    Games and Kids are fully implemented. Others are reserved for future use
    and cannot be selected until implemented.
    """
    domains = []
    for d in ContentDomain:
        domains.append({
            "value": d.value,
            "label": _DOMAIN_LABELS.get(d.value, d.value),
            "implemented": d.value in IMPLEMENTED_DOMAINS,
        })
    return {"domains": domains, "current": _get_current_domain(user.id)}


_DOMAIN_LABELS = {
    "games": "Games",
    "kids": "Kids",
    "movies": "Filmes & Séries",
    "conspiracy": "Mistérios & Teorias",
    "technology": "Tecnologia",
}


def _get_current_domain(user_id: int) -> str:
    """Get the current domain for a user's channel."""
    with session_scope() as session:
        profile = session.query(ChannelProfile).filter(
            ChannelProfile.user_id == user_id
        ).first()
        return profile.domain if profile else ContentDomain.games.value


@router.post("/channel/reset-domain")
def reset_domain(
    data: dict,
    user: User = Depends(get_current_user),
):
    """Destructive domain switch — reset the channel to a new domain.

    This is a DESTRUCTIVE operation. It cancels all jobs, deletes all
    domain-specific media/content/knowledge, and resets the channel to
    a clean state in the new domain.

    YouTube connection and already-published videos are NOT affected.

    Required body:
        {"new_domain": "kids", "confirm": true}

    The `confirm` field must be explicitly True. The frontend should
    present a confirmation dialog explaining what will be lost.
    """
    new_domain = data.get("new_domain")
    confirm = data.get("confirm", False)

    if not new_domain:
        raise HTTPException(422, "new_domain is required")
    if new_domain not in VALID_DOMAINS:
        raise HTTPException(
            422,
            f"Invalid domain '{new_domain}'. Valid: {sorted(VALID_DOMAINS)}",
        )
    if new_domain not in IMPLEMENTED_DOMAINS:
        raise HTTPException(
            422,
            f"Domain '{new_domain}' is not yet implemented. "
            f"Currently implemented: {sorted(IMPLEMENTED_DOMAINS)}",
        )
    if not confirm:
        raise HTTPException(
            422,
            "Domain reset requires explicit confirmation (confirm=true). "
            "This operation is destructive and cannot be undone.",
        )

    with session_scope() as session:
        try:
            summary = reset_channel_domain(
                session, user.id, new_domain, confirm=True
            )
        except ValueError as e:
            raise HTTPException(422, str(e))

    return {"ok": True, **summary}
