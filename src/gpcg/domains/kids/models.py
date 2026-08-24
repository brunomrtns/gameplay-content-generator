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
    """Lifecycle of a story asset from upload to ready-to-use.

    Images are probed synchronously on the VPS (PIL is lightweight) and
    go straight to ``ready``. Videos require worker-side processing
    (FFprobe for metadata, thumbnail extraction) AND semantic mapping
    (VLM + ASR → KidsMediaEvent) — the same pipeline as GameplaySource
    in the Games domain::

        uploading → queued → processing → mapping → ready
                                    ↓          ↓
                                  failed     failed

    The ``mapping`` stage is the equivalent of ``GameplayProcessingStatus.mapping``
    in Games: the worker runs the analyzer (VLM + ASR) to produce
    ``KidsMediaEvent`` records that index the video for semantic selection.
    """
    uploading = "uploading"
    queued = "queued"          # video uploaded, waiting for worker
    processing = "processing"  # worker running FFprobe/thumbnail
    mapping = "mapping"        # worker running VLM+ASR analysis (semantic mapping)
    ready = "ready"            # metadata + mapping done, available for selection
    failed = "failed"


class AssetMediaKind(str, enum.Enum):
    """Type of media stored in a StoryAsset."""
    image = "image"
    video = "video"


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
    """A media asset uploaded for the Kids channel library.

    Supports both images and videos. Images are probed synchronously
    (PIL dimensions) and marked ``ready`` immediately. Videos require
    worker-side processing via a ``kids_asset_process`` job:
      1. FFprobe → metadata (duration, dimensions, codec, audio)
      2. Thumbnail extraction (FFmpeg)
      3. **Semantic mapping** (VLM + ASR → ``KidsMediaEvent[]``) — the
         same pipeline as ``GameplayAnalyzer`` in Games. The worker
         runs the analyzer to produce events that index the video for
         semantic selection by ``KidsMediaRetriever``.

    Lifecycle for videos::

        uploading → queued → processing → mapping → ready
                                    ↓          ↓
                                  failed     failed

    Lifecycle for images::

        uploading → ready

    Replaces GameplaySource in the Kids domain. The same analysis
    pipeline (VLM + ASR → events) is used so that ``KidsMediaRetriever``
    can select clips semantically — exactly like ``GameplayRetriever``
    uses ``GameplayEvent`` in Games.

    Mídia da **biblioteca do canal**, não do tópico. ``topic_id`` é
    opcional — a mídia pode ser vinculada a um tópico específico ou
    ficar na biblioteca geral do canal. O ``KidsMediaRetriever`` seleciona
    mídias da biblioteca que condizem com o conteúdo do vídeo via
    eventos semânticos (``KidsMediaEvent``) + ``tags`` + ``description``.
    """
    __tablename__ = "story_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    # Nullable: mídia da biblioteca do canal, não obrigatoriamente de um tópico
    topic_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("kids_topics.id"), nullable=True, index=True
    )

    filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(500), default="")  # relative path within storage
    file_hash: Mapped[str] = mapped_column(String(64), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)

    # Media type and video-specific metadata (populated by worker for videos)
    media_kind: Mapped[str] = mapped_column(
        String(10), default=AssetMediaKind.image.value
    )
    duration: Mapped[float] = mapped_column(Float, default=0.0)  # seconds (video only)
    codec: Mapped[str] = mapped_column(String(50), default="")  # e.g. "h264", "png"
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    thumbnail_key: Mapped[str] = mapped_column(String(500), default="")  # relative path to thumbnail

    processing_status: Mapped[str] = mapped_column(
        String(20), default=AssetProcessingStatus.uploading.value
    )
    process_error: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Semantic selection: tags + description for KidsMediaRetriever
    tags: Mapped[list] = mapped_column(JSON, default=list)  # ["dinosaur", "nature", "green"]
    description: Mapped[str] = mapped_column(Text, default="")  # optional human/AI description
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)  # channel library or public

    # Relationships
    topic: Mapped[Optional["KidsTopic"]] = relationship(back_populates="assets")
    events: Mapped[list["KidsMediaEvent"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan"
    )
    clip_usages: Mapped[list["AssetClipUsage"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan"
    )

    @property
    def analysis_info(self) -> dict:
        """Read analysis status from metadata_json (same pattern as GameplaySource)."""
        return self.metadata_json.get("analysis", {
            "status": "pending",
            "version": "",
            "vision_model": "",
            "event_count": 0,
            "analyzed_at": None,
            "error": None,
        })

    @property
    def analysis_status(self) -> str:
        return self.analysis_info.get("status", "pending")

    @property
    def is_analysis_ready(self) -> bool:
        return self.analysis_status == "ready"

    def __repr__(self) -> str:
        return f"<StoryAsset #{self.id} [{self.media_kind}] {self.filename}>"


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


class AssetClipUsage(Base):
    """Tracks which time ranges of a video asset have been used in a video.

    Equivalent to ``GameplayClipUsage`` in the Games domain. Prevents
    reusing the same video segment across videos. Only applies to video
    assets — images can be reused freely (Ken Burns effect doesn't
    "consume" the image), but we track usage for diversity scoring.

    When a video is deleted with the "release clips" option, the
    corresponding usage records are removed, making those segments
    available again.
    """
    __tablename__ = "asset_clip_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("story_assets.id"), index=True)
    consumer_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    start_sec: Mapped[float] = mapped_column(Float, default=0.0)
    end_sec: Mapped[float] = mapped_column(Float, default=0.0)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    asset: Mapped["StoryAsset"] = relationship(back_populates="clip_usages")

    def __repr__(self) -> str:
        return (
            f"<AssetClipUsage video={self.video_id} asset={self.asset_id} "
            f"consumer={self.consumer_user_id} [{self.start_sec:.1f}-{self.end_sec:.1f}]>"
        )


class KidsMediaEvent(Base):
    """A semantically-identified event in a Kids video asset.

    Equivalent to ``GameplayEvent`` in the Games domain. Produced by the
    ``KidsMediaAnalyzer`` (which reuses the same VLM + ASR pipeline as
    ``GameplayAnalyzer``) during the mapping stage of asset processing.

    Each event represents a temporally-bounded segment with:
    - event_type: VISUAL_ACTION, NARRATION, ANIMATION, STATIC_IMAGE,
      TEXT_OVERLAY, TRANSITION, etc. (Kids-specific taxonomy)
    - description: what the VLM sees in this segment
    - tags: free-form semantic tags for matching
    - transcript: ASR text overlapping this event (if audio available)
    - visual_confidence: how confident the VLM is (0-1)
    - interesting_score: editorial usefulness for Kids content (0-1)

    The index is built once during ingestion (mapping job) and queried
    during video generation by ``KidsMediaRetriever`` for semantic clip
    matching — exactly like ``GameplayRetriever`` queries ``GameplayEvent``.
    """
    __tablename__ = "kids_media_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("story_assets.id"), nullable=False, index=True
    )

    start_time: Mapped[float] = mapped_column(Float, default=0.0)
    end_time: Mapped[float] = mapped_column(Float, default=0.0)

    # Event classification (Kids-specific taxonomy)
    # Examples: VISUAL_ACTION, NARRATION, ANIMATION, STATIC_IMAGE,
    # TEXT_OVERLAY, TRANSITION, CHARACTER_INTRO, EDUCATIONAL_DEMO, etc.
    event_type: Mapped[str] = mapped_column(String(50), default="unknown", index=True)
    description: Mapped[str] = mapped_column(Text, default="")

    # Structured metadata from VLM analysis
    characters: Mapped[list] = mapped_column(JSON, default=list)
    location: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    actions: Mapped[list] = mapped_column(JSON, default=list)
    tags: Mapped[list] = mapped_column(JSON, default=list)

    # ASR transcript overlapping this event's time range
    transcript: Mapped[str] = mapped_column(Text, default="")

    # Confidence scores (0-1) — same semantics as GameplayEvent
    visual_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    interesting_score: Mapped[float] = mapped_column(Float, default=0.0)

    # Analysis provenance
    analysis_version: Mapped[str] = mapped_column(String(20), default="v1")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    asset: Mapped["StoryAsset"] = relationship(back_populates="events")

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    @property
    def is_confident(self) -> bool:
        return self.visual_confidence >= 0.7

    def __repr__(self) -> str:
        return f"<KidsMediaEvent [{self.start_time:.1f}-{self.end_time:.1f}] {self.event_type}>"
