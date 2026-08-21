"""Kids domain models — topic and story asset ORM for the Kids domain.

These models are the Kids-specific layer: topics (educational/entertaining
subjects) and story assets (images uploaded for visual content).

They share the same ``Base`` as core models so SQLAlchemy FKs and
relationships resolve correctly across the boundary.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from gpcg.core.models import Base, _utcnow


# ── Enums ─────────────────────────────────────────────────────────────────────


class AssetProcessingStatus(str, enum.Enum):
    """Lifecycle of a story asset from upload to ready-to-use."""
    uploading = "uploading"
    ready = "ready"
    failed = "failed"


# ── Models ────────────────────────────────────────────────────────────────────


class KidsTopic(Base):
    """A topic for Kids content — educational or entertaining.

    Replaces Game in the Kids domain. A topic like "Dinosaurs", "Solar
    System", or "ABCs" drives the script and visual selection.
    """
    __tablename__ = "kids_topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    slug: Mapped[str] = mapped_column(String(200), index=True)
    category: Mapped[str] = mapped_column(String(50), default="general")  # educational, animals, science, story, etc
    age_range: Mapped[str] = mapped_column(String(20), default="3-6")  # "3-6", "7-10", "all"
    description: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    assets: Mapped[list["StoryAsset"]] = relationship(
        back_populates="topic", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<KidsTopic #{self.id} [{self.title}]>"


class StoryAsset(Base):
    """An image/illustration uploaded for a Kids topic.

    Replaces GameplaySource in the Kids domain. Instead of video clips
    with events and analysis, Kids uses simple images displayed during
    narration.
    """
    __tablename__ = "story_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("kids_topics.id"), nullable=False, index=True)

    filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(500), default="")  # relative path within storage
    file_hash: Mapped[str] = mapped_column(String(64), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)

    processing_status: Mapped[str] = mapped_column(
        String(20), default=AssetProcessingStatus.uploading.value
    )
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    topic: Mapped["KidsTopic"] = relationship(back_populates="assets")

    def __repr__(self) -> str:
        return f"<StoryAsset #{self.id} [{self.filename}]>"
