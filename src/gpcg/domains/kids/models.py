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
    Boolean,
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


class KidsIdeaStatus(str, enum.Enum):
    """Lifecycle of a KidsIdea — an editorial opportunity for Kids content.

    Flow::

        discovered → evaluated → queued → converted
                         ↓          ↓
                      rejected   rejected
                         ↓
                      expired

    - ``discovered``: newly created (manual or auto), awaiting safety + scoring
    - ``evaluated``: safety + scoring done, available for curation
    - ``queued``: user (or auto-fill) added to the idea queue
    - ``converted``: transformed into a KidsTopic (terminal)
    - ``rejected``: discarded by user or safety filter (terminal)
    - ``expired``: archived by age without use (terminal)
    """
    discovered = "discovered"
    evaluated = "evaluated"
    queued = "queued"
    converted = "converted"
    rejected = "rejected"
    expired = "expired"


class KidsIdeaSource(str, enum.Enum):
    """Origin of a KidsIdea."""
    ai_ideation = "ai_ideation"      # LLM-generated from channel profile + category
    topic_library = "topic_library"  # from the built-in topic library seeds
    seasonal = "seasonal"             # from the seasonal calendar (holidays, events)
    manual = "manual"                 # user-created via API
    research = "research"             # future: external research sources


# ── Models ────────────────────────────────────────────────────────────────────


class KidsTopic(Base):
    """A topic for Kids content — educational or entertaining.

    Replaces Game in the Kids domain. A topic like "Dinosaurs", "Solar
    System", or "ABCs" drives the script and visual selection.

    A KidsTopic can be created manually or converted from a KidsIdea.
    When converted, ``idea_id`` links back to the originating idea for
    traceability (which idea produced this topic, was it already
    produced, duplicate detection, editorial history).
    """
    __tablename__ = "kids_topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    slug: Mapped[str] = mapped_column(String(200), index=True)
    category: Mapped[str] = mapped_column(String(50), default="general")  # educational, animals, science, story, etc
    age_range: Mapped[str] = mapped_column(String(20), default="3-6")  # "3-6", "7-10", "all"
    description: Mapped[str] = mapped_column(Text, default="")
    # Traceability: if this topic was converted from a KidsIdea, link back.
    idea_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("kids_ideas.id"), nullable=True, index=True
    )
    # Editorial metadata (set when converted from an idea)
    editorial_intent: Mapped[str] = mapped_column(String(50), default="curiosity")  # curiosity, educational, story
    educational_goal: Mapped[str] = mapped_column(String(50), default="general")  # science, nature, math...
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    assets: Mapped[list["StoryAsset"]] = relationship(
        back_populates="topic", cascade="all, delete-orphan"
    )
    idea: Mapped[Optional["KidsIdea"]] = relationship(
        foreign_keys=[idea_id], back_populates="topic"
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


class KidsIdea(Base):
    """An editorial opportunity for Kids content.

    Unlike ``KnowledgeItem`` (Games — external content collected from RSS),
    a KidsIdea is an *opportunity*: a question, curiosity, or theme that
    can become an educational video. It is generated by AI ideation,
    seeded from the topic library, suggested by the seasonal calendar,
    or created manually by the user.

    Lifecycle::

        discovered → evaluated → queued → converted
                         ↓          ↓
                      rejected   rejected
                         ↓
                      expired

    The idea is NOT a fact. AI-generated ideas are editorial prompts, not
    verified truths. Fact validation happens later in the script pipeline
    (the script prompt instructs the LLM to not invent facts, and the
    anti-plagiarism/originality layer checks the final script).

    Deduplication: ``content_hash`` = SHA256(normalize(title)) detects
    exact and near-exact duplicates. Fuzzy similarity (embeddings) can be
    added later via the existing EmbeddingService without schema changes.

    Relation to KidsTopic: when an idea is selected for production, it is
    *converted* into a KidsTopic. The ``topic_id`` FK links back, and
    ``KidsTopic.idea_id`` provides the reverse link. This enables full
    traceability: which idea produced which topic → which video.
    """
    __tablename__ = "kids_ideas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)

    # ── Content ───────────────────────────────────────────────────────────
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(50), default="general", index=True)
    suggested_age_range: Mapped[str] = mapped_column(String(20), default="3-6")

    # ── Origin ────────────────────────────────────────────────────────────
    source: Mapped[str] = mapped_column(
        String(30), default=KidsIdeaSource.ai_ideation.value, index=True
    )
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)

    # ── Scoring (0–100 for editorial_score, 0.0–1.0 for sub-scores) ──────
    editorial_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    safety_score: Mapped[float] = mapped_column(Float, default=1.0)
    age_fit_score: Mapped[float] = mapped_column(Float, default=0.5)
    educational_value: Mapped[float] = mapped_column(Float, default=0.5)
    curiosity_score: Mapped[float] = mapped_column(Float, default=0.5)
    visual_potential: Mapped[float] = mapped_column(Float, default=0.5)
    final_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)

    # ── Safety ────────────────────────────────────────────────────────────
    safety_flags: Mapped[list] = mapped_column(JSON, default=list)
    safety_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)

    # ── Lifecycle ─────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(20), default=KidsIdeaStatus.discovered.value, index=True
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # ── Deduplication ─────────────────────────────────────────────────────
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    # ── Traceability ──────────────────────────────────────────────────────
    topic_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("kids_topics.id"), nullable=True, index=True
    )

    # ── Timestamps ────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationship: the KidsTopic created from this idea (if converted)
    topic: Mapped[Optional["KidsTopic"]] = relationship(
        foreign_keys=[topic_id]
    )

    def __repr__(self) -> str:
        return f"<KidsIdea #{self.id} [{self.status}] score={self.final_score:.2f}>"
