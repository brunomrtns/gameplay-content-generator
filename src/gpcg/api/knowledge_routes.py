"""Knowledge routes — channel knowledge management endpoints.

Endpoints:
  Channel Profile:
    GET    /api/channel/profile           — get or auto-create user's channel profile
    PUT    /api/channel/profile           — update channel profile (free-text + structured)
    GET    /api/channel/presets           — list available editorial presets
    POST   /api/channel/preset            — apply an editorial preset to the profile

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
from gpcg.core.models import ChannelProfile, User
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
