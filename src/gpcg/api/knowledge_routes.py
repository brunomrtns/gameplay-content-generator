"""Knowledge routes — channel knowledge management endpoints.

Endpoints:
  Channel Profile:
    GET    /api/channel/profile           — get or auto-create user's channel profile
    PUT    /api/channel/profile           — update channel profile

NOTE: File-upload knowledge base (RAG) endpoints have been removed.
Channel knowledge is now managed via manual ideas (KnowledgeItem with
source_type="manual"). Legacy Document/KnowledgeChunk data is preserved
in the database but no longer used by the API.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from gpcg.domain.models import ChannelProfile, User
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
    """Get the user's channel profile, auto-creating if it doesn't exist."""
    profile = db.query(ChannelProfile).filter(
        ChannelProfile.user_id == user.id
    ).first()

    if not profile:
        profile = ChannelProfile(user_id=user.id)
        with session_scope() as session:
            session.add(profile)
            session.flush()
            profile_id = profile.id
            # Re-fetch in the request session
        profile = db.query(ChannelProfile).filter(
            ChannelProfile.id == profile_id
        ).first()

    return {
        "id": profile.id,
        "channel_description": profile.channel_description,
        "niche": profile.niche,
        "target_audience": profile.target_audience,
        "tone_of_voice": profile.tone_of_voice,
        "narrative_style": profile.narrative_style,
        "content_goals": profile.content_goals,
        "special_rules": profile.special_rules,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }


@router.put("/channel/profile")
def update_channel_profile(
    data: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update the user's channel profile.

    All fields are optional — only provided fields are updated.
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

    allowed_fields = [
        "channel_description", "niche", "target_audience",
        "tone_of_voice", "narrative_style", "content_goals", "special_rules",
    ]

    with session_scope() as session:
        p = session.query(ChannelProfile).filter(
            ChannelProfile.id == profile.id
        ).first()
        for field in allowed_fields:
            if field in data:
                setattr(p, field, data[field])
        session.flush()

    return {"ok": True, "id": profile.id}
