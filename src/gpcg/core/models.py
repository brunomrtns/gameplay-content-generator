"""Core domain models — domain-agnostic SQLAlchemy ORM for GPCG.

These models are the reusable core of the platform: users, jobs, workers,
content plans, scripts, videos, knowledge items, channel profiles, etc.

Games-specific models (Game, GameplaySource, GameplayEvent, etc.) live in
``gpcg.domains.games.models`` and share the same ``Base`` so that SQLAlchemy
FKs and relationships resolve correctly across the boundary.

Both modules must be imported before any ``create_all()`` or query is
executed. ``database.init_db()`` handles this.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for all models (core and domain)."""


# ── Enums ─────────────────────────────────────────────────────────────────────


class ContentDomain(str, enum.Enum):
    """Content domain (niche) of a channel.

    Determines which pipeline, media model, and editorial logic apply.
    Games is the only fully implemented domain; others are reserved for
    future use. The domain is a characteristic of the channel, NOT the user.
    Switching domain is a destructive reset operation (see domain_reset_service).
    """
    games = "games"
    # Reserved for future domains (not yet implemented):
    kids = "kids"
    movies = "movies"
    conspiracy = "conspiracy"
    technology = "technology"


class FactVerification(str, enum.Enum):
    unverified = "unverified"
    verified = "verified"
    disputed = "disputed"


class JobType(str, enum.Enum):
    generate_short = "generate_short"
    curiosity_short = "curiosity_short"  # general curiosity + gameplay background
    ingest = "ingest"
    extract_facts = "extract_facts"
    re_render = "re_render"
    # Control Plane + Compute Plane: mapping is a worker job
    mapping = "mapping"  # analyze a gameplay (download → VLM → ASR → index events)
    knowledge_index = "knowledge_index"  # index a document for RAG (download → OCR → chunk → embed)
    # V2: enrichment + content intelligence (run on VPS, no GPU)
    game_enrich = "game_enrich"  # enrich a Game with Wikidata + Wikipedia data
    content_collect = "content_collect"  # collect external content (RSS) into KnowledgeItems
    cleanup_gameplay = "cleanup_gameplay"  # delete physical gameplay files from worker storage
    cleanup_user_storage = "cleanup_user_storage"  # delete ALL files for a user/domain from worker storage
    # Kids Idea System: discovery + scoring (run on VPS, no GPU)
    kids_idea_discovery = "kids_idea_discovery"  # AI ideation + topic library + seasonal
    kids_idea_score = "kids_idea_score"  # batch safety + scoring for discovered ideas
    # Kids media processing: worker downloads video → FFprobe + thumbnail → sync metadata
    kids_asset_process = "kids_asset_process"


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    paused = "paused"
    completed = "completed"
    failed = "failed"
    retrying = "retrying"
    cancelled = "cancelled"


class JobPriority(str, enum.Enum):
    """Job priority. Higher priority jobs are claimed by workers first."""
    low = "low"
    normal = "normal"
    high = "high"


class JobStage(str, enum.Enum):
    ingest = "ingest"
    game_resolution = "game_resolution"
    extract_facts = "extract_facts"
    content_planning = "content_planning"
    story_finding = "story_finding"  # V2: StoryConcept (angle, frame, curiosity_gap)
    editorial_planning = "editorial_planning"  # NEW: VideoCreativePlan
    creative_engine = "creative_engine"  # optional: Qwen3-14B creative material
    script = "script"
    humanization = "humanization"  # V2: break AI patterns, ensure orality
    script_review = "script_review"  # NEW: ScriptCritic PASS/REVISE
    tts = "tts"
    gameplay_selection = "gameplay_selection"
    visual_selection = "visual_selection"  # Kids: image selection (analogous to gameplay_selection)
    presentation = "presentation"  # Presentation Layer: thumbnail + opening (optional)
    music_selection = "music_selection"
    render_plan = "render_plan"
    render = "render"
    qa = "qa"
    metadata_generation = "metadata_generation"  # NEW: LLM-generated social metadata
    youtube_upload = "youtube_upload"  # NEW: auto-upload to YouTube
    output = "output"
    done = "done"
    # Worker stages (Control Plane + Compute Plane)
    download = "download"  # worker downloading gameplay from VPS
    confirm_download = "confirm_download"  # verifying checksum, confirming integrity
    mapping = "mapping"  # running GameplayAnalyzer (VLM + ASR + merge + score)
    knowledge_indexing = "knowledge_indexing"  # worker indexing a document (OCR + chunk + embed)
    # V2: VPS-side stages (no GPU)
    enrichment = "enrichment"  # fetching Wikidata/Wikipedia + generating lore
    content_collection = "content_collection"  # collecting RSS + scoring KnowledgeItems


class WorkerStatus(str, enum.Enum):
    """Worker lifecycle status. Reported via heartbeat/status, not assumed."""
    online = "online"  # registered, heartbeat recent, idle
    busy = "busy"  # actively processing a job
    offline = "offline"  # heartbeat timeout or explicit deregister
    error = "error"  # worker reported an error


class WorkerCapability(str, enum.Enum):
    """Capabilities a worker can fulfill. Used for job-to-worker matching.
    A worker may have multiple capabilities (e.g., mapping + generation).
    Future workers may be specialized (e.g., only youtube upload).
    """
    mapping = "mapping"  # gameplay analysis (VLM, ASR, YOLO, frame extraction)
    generation = "generation"  # video generation (TTS, render, FFmpeg)
    knowledge_index = "knowledge_index"  # document indexing (OCR, chunking, embeddings)
    youtube = "youtube"  # YouTube upload via google-integration
    future_ai = "future_ai"  # reserved for future AI capabilities
    # V2: VPS-side capabilities (no GPU needed)
    enrichment = "enrichment"  # game enrichment (Wikidata + Wikipedia + LLM lore)
    content_intelligence = "content_intelligence"  # RSS collection + scoring


class KnowledgeItemType(str, enum.Enum):
    """Type of content in a KnowledgeItem (V2 content intelligence)."""
    news = "news"  # current news (RSS, high freshness weight)
    curiosity = "curiosity"  # evergreen curiosity
    lore = "lore"  # game narrative/history (Wikipedia)
    fact = "fact"  # fact from user document (user_doc source)


class KnowledgeItemSource(str, enum.Enum):
    """Source of a KnowledgeItem (V2 content intelligence)."""
    rss = "rss"
    wikipedia = "wikipedia"
    steam = "steam"  # future
    reddit = "reddit"  # future
    igdb = "igdb"  # future
    user_doc = "user_doc"
    manual = "manual"  # user-curated idea entered manually via API


class KnowledgeItemStatus(str, enum.Enum):
    """Lifecycle status of a KnowledgeItem (V2 content intelligence)."""
    fresh = "fresh"  # available for content planning
    used = "used"  # already used in a generated video
    rejected = "rejected"  # user discarded this idea


class VideoStatus(str, enum.Enum):
    pending = "pending"
    ready = "ready"
    qa_passed = "qa_passed"
    qa_failed = "qa_failed"
    pending_approval = "pending_approval"
    published = "published"
    publish_failed = "publish_failed"


class ScriptStatus(str, enum.Enum):
    draft = "draft"
    optimized = "optimized"
    final = "final"
    rejected = "rejected"


# ── Models ────────────────────────────────────────────────────────────────────


class User(Base):
    """Platform user. Each user has isolated data (gameplays, videos, automations).

    Admin users (is_admin=True) can manage other users. The admin email is
    configured via GPCG_ADMIN_EMAIL and auto-promoted on first login/creation.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # Nullable since SSO migration — BI Identity handles passwords
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # BI Identity user ID — links to the Identity Service user
    bi_identity_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Google-integration user ID — maps to oauth_credentials.userId in that service.
    # Assigned when user connects their YouTube channel.
    google_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    # UI language (BCP-47 tag). Content language is ChannelProfile.target_language.
    ui_language: Mapped[str] = mapped_column(String(10), default="pt-BR")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    def __repr__(self) -> str:
        return f"<User #{self.id} {self.email} admin={self.is_admin}>"


class Automation(Base):
    """Per-user automation config. Each user has exactly ONE automation.

    Stores all video generation settings (format, subtitles, transitions,
    voice, creative style, etc.) as a JSON config blob. The generation
    pipeline reads this config when producing videos for the user.

    status: idle | running | paused | error
    schedule: cron-like schedule or "manual" for on-demand only
    """
    __tablename__ = "automations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="Minha Automação")
    status: Mapped[str] = mapped_column(String(20), default="idle")  # idle|running|paused|error
    schedule: Mapped[str] = mapped_column(String(100), default="manual")
    # All video generation config (format, subtitles, transitions, voice, etc.)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    # YouTube upload settings (privacy, category, auto-publish)
    upload_config: Mapped[dict] = mapped_column(JSON, default=dict)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    user: Mapped["User"] = relationship()

    def __repr__(self) -> str:
        return f"<Automation #{self.id} user={self.user_id} status={self.status}>"


class Document(Base):
    """Uploaded reference document (PDF/TXT/MD/DOCX) containing facts.

    game_id is nullable: NULL = general document (not tied to a specific game),
    used for the "random curiosities with gameplay background" format.
    """
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    game_id: Mapped[Optional[int]] = mapped_column(ForeignKey("games.id"), nullable=True, index=True)
    # REFACTORY_V2: visibility for the hybrid content pool model.
    # NULL user_id = system-collected (shared pool). user_id set + is_public=False
    # = private to owner. user_id set + is_public=True = shared with other users.
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(1024))
    file_type: Mapped[str] = mapped_column(String(20))  # pdf, txt, md, docx
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    file_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # SHA256 for download verification
    upload_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)  # one-time download token for worker
    text_extracted: Mapped[bool] = mapped_column(Boolean, default=False)
    facts_extracted: Mapped[bool] = mapped_column(Boolean, default=False)
    # Knowledge processing status: pending → processing → indexed → error
    # (separate from facts_extracted — knowledge indexing is the RAG layer)
    knowledge_status: Mapped[str] = mapped_column(String(20), default="pending")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    game: Mapped[Optional["Game"]] = relationship(back_populates="documents")

    def __repr__(self) -> str:
        return f"<Document {self.filename}>"


class Fact(Base):
    """A fact/curiosity extracted from documents.

    game_id is nullable: NULL = general fact (not tied to a specific game),
    used for the "random curiosities with gameplay background" format.
    """
    __tablename__ = "facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    game_id: Mapped[Optional[int]] = mapped_column(ForeignKey("games.id"), nullable=True, index=True)
    document_id: Mapped[Optional[int]] = mapped_column(ForeignKey("documents.id"), nullable=True)
    # REFACTORY_V2: visibility for the hybrid content pool model.
    # NULL user_id = system-collected (shared pool). user_id set + is_public=False
    # = private to owner. user_id set + is_public=True = shared with other users.
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    category: Mapped[str] = mapped_column(String(50), default="general")  # curiosity, easter_egg, trivia, dev, etc
    claim: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # doc filename + page/section
    verification: Mapped[str] = mapped_column(String(20), default=FactVerification.unverified.value)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)  # 0-100 editorial potential
    novelty_score: Mapped[float] = mapped_column(Float, default=0.0)
    # Curiosity score (V2 editorial architecture). Composite of 5 editorial
    # sub-scores + 1 technical sub-score (visual_potential). See
    # docs/EDITORIAL_REFACTOR_PLAN_V2.md §3.1, §4.2.
    # curiosity_score = curiosity_gap*0.30 + surprise_potential*0.25
    #                   + retention_potential*0.20 + familiarity*0.15
    #                   + insight_quality*0.10
    curiosity_score: Mapped[float] = mapped_column(Float, default=0.0)
    curiosity_subscores: Mapped[dict] = mapped_column(JSON, default=dict)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    game: Mapped[Optional["Game"]] = relationship(back_populates="facts")

    def __repr__(self) -> str:
        return f"<Fact #{self.id} [{self.category}]>"


class ContentPlan(Base):
    """AI-decided plan for a video.

    game_id is nullable: NULL = general curiosity plan (not about a specific game).
    background_game_id: for curiosity shorts, the game whose gameplay runs in background.
    """
    __tablename__ = "content_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    game_id: Mapped[Optional[int]] = mapped_column(ForeignKey("games.id"), nullable=True, index=True)
    fact_id: Mapped[Optional[int]] = mapped_column(ForeignKey("facts.id"), nullable=True)
    # For curiosity shorts: the game whose gameplay is used as background
    background_game_id: Mapped[Optional[int]] = mapped_column(ForeignKey("games.id"), nullable=True)

    format: Mapped[str] = mapped_column(String(30), default="youtube_short")
    target_duration: Mapped[int] = mapped_column(Integer, default=60)
    # Content language (BCP-47). Frozen from ChannelProfile at plan creation.
    target_language: Mapped[str] = mapped_column(String(10), default="pt-BR")
    topic: Mapped[str] = mapped_column(Text)
    hook: Mapped[str] = mapped_column(Text)
    tone: Mapped[str] = mapped_column(String(50), default="curious")
    energy: Mapped[float] = mapped_column(Float, default=0.7)
    music_mood: Mapped[str] = mapped_column(String(50), default="neutral")
    visual_strategy: Mapped[str] = mapped_column(String(50), default="auto")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    game: Mapped[Optional["Game"]] = relationship(back_populates="content_plans", foreign_keys=[game_id])
    background_game: Mapped[Optional["Game"]] = relationship(foreign_keys=[background_game_id])
    scripts: Mapped[list["Script"]] = relationship(back_populates="content_plan", cascade="all, delete-orphan")
    videos: Mapped[list["Video"]] = relationship(back_populates="content_plan", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<ContentPlan #{self.id} [{self.topic[:40]}...]>"


class Script(Base):
    __tablename__ = "scripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_plan_id: Mapped[int] = mapped_column(ForeignKey("content_plans.id"), index=True)
    draft: Mapped[str] = mapped_column(Text, default="")
    optimized: Mapped[str] = mapped_column(Text, default="")
    final: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=ScriptStatus.draft.value)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    # Content language (BCP-47). Frozen from ContentPlan at script creation.
    language: Mapped[str] = mapped_column(String(10), default="pt-BR")
    # Anti-plagiarism: originality score (0-100, higher = more original)
    # Computed by comparing the final script against source documents + fact claims
    # via n-gram overlap. Scripts below 70 trigger an automatic rewrite.
    originality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    originality_report: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    rewrite_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    content_plan: Mapped["ContentPlan"] = relationship(back_populates="scripts")

    def __repr__(self) -> str:
        return f"<Script #{self.id} [{self.status}] orig={self.originality_score}"


class Worker(Base):
    """A compute worker (local PC with GPU) registered with the VPS.

    The VPS is a Control Plane: it only orchestrates jobs and stores metadata.
    Workers do all heavy processing (VLM, ASR, FFmpeg, rendering) on their own
    GPU and storage. Multiple workers can register (home-pc, office-pc, etc.).

    Heartbeat vs Status:
      - Heartbeat: just "I'm alive" (last_heartbeat timestamp). Frequent (10s).
      - Status: "what I'm doing" (current_activity, gpu/cpu/ram, current_job).
        Less frequent, sent when state changes or periodically.

    Capabilities: a worker declares what it can do (mapping, generation, etc.).
    Jobs declare required_capabilities. The claim endpoint matches them.
    """
    __tablename__ = "workers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Human-readable unique identifier (e.g., "home-pc", "gpu-server")
    worker_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    hostname: Mapped[str] = mapped_column(String(255), default="")

    # Lifecycle
    status: Mapped[str] = mapped_column(
        String(20), default=WorkerStatus.offline.value, index=True
    )
    last_heartbeat: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    last_status_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # What the worker is currently doing (reported via status, not heartbeat)
    current_job_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("jobs.id"), nullable=True, index=True
    )
    current_activity: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Hardware info (reported via status, updated periodically)
    gpu_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    gpu_usage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 0-100 %
    cpu_usage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 0-100 %
    ram_usage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # GB

    # Capabilities: ["mapping", "generation", "youtube", ...]
    capabilities: Mapped[list] = mapped_column(JSON, default=list)

    # Versioning for production diagnostics
    worker_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    git_commit: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    build_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    def __repr__(self) -> str:
        return f"<Worker {self.worker_id} [{self.status}]>"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    job_uuid: Mapped[str] = mapped_column(String(36), unique=True, index=True, default=lambda: str(uuid.uuid4()))
    type: Mapped[str] = mapped_column(String(30), default=JobType.generate_short.value, index=True)
    # Domain the job belongs to. Set at creation time from ChannelProfile.domain.
    # Used by the domain guard: if the channel's current domain != job.domain,
    # the job result is rejected (prevents old-domain jobs from producing
    # content after a domain switch).
    domain: Mapped[str] = mapped_column(
        String(30), default=ContentDomain.games.value, index=True
    )
    game_id: Mapped[Optional[int]] = mapped_column(ForeignKey("games.id"), nullable=True, index=True)
    content_plan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("content_plans.id"), nullable=True)
    # GameplaySource being processed (for mapping jobs)
    gameplay_source_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("gameplay_sources.id"), nullable=True, index=True
    )

    status: Mapped[str] = mapped_column(String(20), default=JobStatus.queued.value, index=True)
    stage: Mapped[str] = mapped_column(String(30), default=JobStage.ingest.value)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    artifacts: Mapped[dict] = mapped_column(JSON, default=dict)

    # Control Plane + Compute Plane: worker assignment and priority
    worker_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("workers.id"), nullable=True, index=True
    )
    priority: Mapped[str] = mapped_column(
        String(10), default=JobPriority.normal.value, index=True
    )
    # Required capabilities (JSON array of WorkerCapability strings).
    # The claim endpoint only offers jobs whose required_capabilities are a
    # subset of the worker's declared capabilities.
    required_capabilities: Mapped[list] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    worker: Mapped[Optional["Worker"]] = relationship(foreign_keys=[worker_id])

    def __repr__(self) -> str:
        return f"<Job #{self.id} [{self.status}/{self.stage}] prio={self.priority}>"


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    job_id: Mapped[Optional[int]] = mapped_column(ForeignKey("jobs.id"), nullable=True, index=True)
    content_plan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("content_plans.id"), nullable=True, index=True)
    game_id: Mapped[Optional[int]] = mapped_column(ForeignKey("games.id"), nullable=True, index=True)

    # Legacy physical path (kept for backward compat). New videos use storage_key.
    file_path: Mapped[str] = mapped_column(String(1024), default="")
    # Opaque storage key for the video file on VPS (or future: just YouTube link)
    storage_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, index=True)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    qa_score: Mapped[float] = mapped_column(Float, default=0.0)
    qa_report: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default=VideoStatus.pending.value)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    # YouTube publication (future: video may only exist as a YouTube link)
    youtube_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    youtube_video_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)

    # V2: traceability — if the video was based on a KnowledgeItem (external content),
    # this FK links back to it. NULL = video based on a Fact (legacy) or no specific item.
    # See D13 in ARCHITECTURE_V2.md — direction is Video→KnowledgeItem (not reverse).
    knowledge_item_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("knowledge_items.id"), nullable=True, index=True
    )

    # Content language (BCP-47). Frozen from Script/ContentPlan at video creation.
    language: Mapped[str] = mapped_column(String(10), default="pt-BR")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    content_plan: Mapped[Optional["ContentPlan"]] = relationship(back_populates="videos")
    # V2: link back to KnowledgeItem if video was based on external content
    knowledge_item: Mapped[Optional["KnowledgeItem"]] = relationship(
        foreign_keys=[knowledge_item_id], back_populates="videos"
    )

    def __repr__(self) -> str:
        return f"<Video #{self.id} [{self.status}]>"


# ── Channel Knowledge Architecture ───────────────────────────────────────────


class ChannelProfile(Base):
    """Per-user channel identity and editorial direction.

    The Editorial Profile is the persisted identity of a channel. It is
    organized into four conceptual groups with distinct lifecycles and
    ownership:

    ┌──────────────────────────────────────────────────────────────────┐
    │ GROUP          │ WHO WRITES    │ LIFECYCLE     │ EXAMPLES        │
    ├──────────────────────────────────────────────────────────────────┤
    │ Configuration  │ User / preset │ Permanent     │ niche, tone,    │
    │                │               │ (until changed│ affinity, feeds │
    │                │               │  by user)     │                 │
    ├──────────────────────────────────────────────────────────────────┤
    │ Learning       │ Feedback loop │ Adaptive      │ preferred_games,│
    │                │ (system)      │ (grows +      │ avoided_topics,  │
    │                │               │  decays)      │ preferred_styles│
    ├──────────────────────────────────────────────────────────────────┤
    │ Statistics     │ System        │ Continuously  │ total_videos,   │
    │                │ (auto)        │ recomputed    │ top_games,      │
    │                │               │               │ avg_performance │
    ├──────────────────────────────────────────────────────────────────┤
    │ Caches         │ System        │ Ephemeral     │ metadata_json   │
    │                │ (auto)        │ (can rebuild) │ (extensible)    │
    └──────────────────────────────────────────────────────────────────┘

    This separation ensures that:
    - User intent (Configuration) is never silently overwritten by the system
    - System learning (Learning) is bounded and decays (no unbounded growth)
    - Statistics are always derivable from source data (no stale snapshots)
    - Caches can be dropped without data loss

    One per user (unique user_id).
    """
    __tablename__ = "channel_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)

    # ═══════════════════════════════════════════════════════════════════════
    # GROUP 1: Configuration (user-defined, permanent until changed)
    # ═══════════════════════════════════════════════════════════════════════
    # These fields define the channel's editorial identity. They are set by
    # the user (or by applying a preset) and persist until explicitly changed.
    # The system NEVER modifies these fields automatically.

    # Content domain — determines which pipeline/media/editorial logic applies.
    # Defaults to "games" (the only fully implemented domain). Switching domain
    # is a destructive reset (see domain_reset_service). NOT coupled to YouTube.
    domain: Mapped[str] = mapped_column(
        String(30), default=ContentDomain.games.value, index=True
    )

    # Target content language (BCP-47 tag, e.g. "pt-BR", "en-US").
    # Determines the language of generated scripts, narration, subtitles, etc.
    # UI language is User.ui_language (separate concern).
    target_language: Mapped[str] = mapped_column(String(10), default="pt-BR")
    # Prompt version for A/B testing and checkpoint compatibility.
    prompt_version: Mapped[str] = mapped_column(String(20), default="v1")
    # Per-language model preferences (e.g. {"zh-CN": {"script": "qwen3:14b"}}).
    # When empty/NULL, GenerationContext falls back to get_recommended_model(language).
    model_preferences: Mapped[Optional[dict]] = mapped_column(JSON, default=dict, nullable=True)

    # Free-form channel description — the "elevator pitch" of the channel.
    channel_description: Mapped[str] = mapped_column(Text, default="")

    # Structured identity fields (optional but recommended)
    niche: Mapped[str] = mapped_column(String(200), default="")
    target_audience: Mapped[str] = mapped_column(String(300), default="")
    tone_of_voice: Mapped[str] = mapped_column(String(100), default="")
    narrative_style: Mapped[str] = mapped_column(String(100), default="")
    content_goals: Mapped[str] = mapped_column(Text, default="")
    special_rules: Mapped[str] = mapped_column(Text, default="")

    # Structured collection parameters (drive the deterministic pipeline)
    content_type_affinity: Mapped[dict] = mapped_column(JSON, default=dict)
    editorial_keywords: Mapped[list] = mapped_column(JSON, default=list)
    custom_feeds: Mapped[list] = mapped_column(JSON, default=list)
    gameplay_driven_collection: Mapped[bool] = mapped_column(Boolean, default=True)
    diversity_strictness: Mapped[float] = mapped_column(Float, default=0.5)

    # Collection focus — temporary editorial direction for a "campaign".
    # When set, the EditorialIntentBuilder/BriefBuilder direct collection
    # (RSS search queries) toward this game and/or topic, regardless of
    # gameplay inventory. Null = no focus (default, legacy behavior).
    # Shape: {"type": "game"|"topic"|"game+topic",
    #         "game_id": int, "game_name": str,
    #         "topic": str, "item_types": [str], "added_at": ISO}
    collection_focus: Mapped[Optional[dict]] = mapped_column(JSON, default=None, nullable=True)

    # ═══════════════════════════════════════════════════════════════════════
    # GROUP 2: Learning (system-acquired, adaptive with decay)
    # ═══════════════════════════════════════════════════════════════════════
    # These fields are populated by the feedback loop, NOT by the user.
    # They grow with usage but are capped (FIFO eviction) and decay over time
    # to prevent unbounded growth and allow the system to re-explore.
    # See editorial_profile_service.py: decay_learned_preferences()

    # {preferred_games: [...], avoided_topics: [...], preferred_styles: [...]}
    learned_preferences: Mapped[dict] = mapped_column(JSON, default=dict)

    # ═══════════════════════════════════════════════════════════════════════
    # GROUP 3: Statistics (aggregated metrics, continuously recomputed)
    # ═══════════════════════════════════════════════════════════════════════
    # These fields are derived from source data (videos, jobs) and recomputed
    # after each production. They are NOT authoritative — they can always be
    # reconstructed by querying the source tables.

    # {total_videos: N, top_games: [...], avg_performance: X}
    production_history_summary: Mapped[dict] = mapped_column(JSON, default=dict)

    # ═══════════════════════════════════════════════════════════════════════
    # GROUP 4: Caches (ephemeral, can be dropped without data loss)
    # ═══════════════════════════════════════════════════════════════════════
    # Extensible metadata for any transient data. Not critical for operation.

    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)

    # ═══════════════════════════════════════════════════════════════════════
    # Timestamps
    # ═══════════════════════════════════════════════════════════════════════
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    user: Mapped["User"] = relationship()

    def __repr__(self) -> str:
        return f"<ChannelProfile user={self.user_id} niche={self.niche!r}>"

    def to_prompt_context(self, language_context=None) -> str:
        """Build a natural-language context block for LLM prompts.

        This is the text that gets injected into system/user prompts across
        the pipeline so the AI knows the channel's identity and direction.

        When a ``LanguageContext`` is provided, labels are localized and the
        language directive is prepended.
        """
        from gpcg.i18n.labels import get_label

        lang = language_context.language if language_context else "pt-BR"
        parts = []
        if language_context:
            directive = language_context.language_directive()
            if directive:
                parts.append(directive)
        if self.channel_description:
            parts.append(f"{get_label('channel_description', lang)}: {self.channel_description}")
        if self.niche:
            parts.append(f"{get_label('niche', lang)}: {self.niche}")
        if self.target_audience:
            parts.append(f"{get_label('target_audience', lang)}: {self.target_audience}")
        if self.tone_of_voice:
            parts.append(f"{get_label('tone_of_voice', lang)}: {self.tone_of_voice}")
        if self.narrative_style:
            parts.append(f"{get_label('narrative_style', lang)}: {self.narrative_style}")
        if self.content_goals:
            parts.append(f"{get_label('content_goals', lang)}: {self.content_goals}")
        if self.special_rules:
            parts.append(f"{get_label('special_rules', lang)}: {self.special_rules}")
        return "\n".join(parts) if parts else ""

    def to_stage_context(self, stage: str, language_context=None) -> str:
        """Return channel context relevant to a specific pipeline stage.

        Stages:
        - content_planning: niche, content_goals, target_audience
        - story_finding: tone_of_voice, narrative_style
        - editorial_planning: niche, target_audience, tone_of_voice, special_rules
        - script: full context (use to_prompt_context())
        """
        from gpcg.i18n.labels import get_label

        lang = language_context.language if language_context else "pt-BR"
        parts = []
        if language_context:
            directive = language_context.language_directive()
            if directive:
                parts.append(directive)
        if stage == "content_planning":
            if self.niche:
                parts.append(f"{get_label('niche_channel', lang)}: {self.niche}")
            if self.content_goals:
                parts.append(f"{get_label('content_goals_label', lang)}: {self.content_goals}")
            if self.target_audience:
                parts.append(f"{get_label('target_audience', lang)}: {self.target_audience}")
        elif stage == "story_finding":
            if self.tone_of_voice:
                parts.append(f"{get_label('tone_of_voice', lang)}: {self.tone_of_voice}")
            if self.narrative_style:
                parts.append(f"{get_label('narrative_style_label', lang)}: {self.narrative_style}")
        elif stage == "editorial_planning":
            if self.niche:
                parts.append(f"{get_label('niche_channel', lang)}: {self.niche}")
            if self.target_audience:
                parts.append(f"{get_label('target_audience', lang)}: {self.target_audience}")
            if self.tone_of_voice:
                parts.append(f"{get_label('tone_of_voice', lang)}: {self.tone_of_voice}")
            if self.special_rules:
                parts.append(f"{get_label('special_rules', lang)}: {self.special_rules}")
        else:
            # Full context for script and other stages
            return self.to_prompt_context(language_context)

        return "\n".join(parts) if parts else ""


class KnowledgeChunk(Base):
    """A chunk of a knowledge document, with embedding vector for RAG retrieval.

    When a user uploads a knowledge document (PDF, TXT, MD, DOCX), the
    document is parsed, chunked (structure-aware), and each chunk is embedded
    via Ollama's embedding API. The embeddings are stored as JSON arrays
    (SQLite-compatible) and used for cosine-similarity retrieval during
    script generation.

    This is the lightweight RAG layer — no external vector DB needed.
    Retrieval is in-memory cosine similarity, which is fine for hundreds
    of chunks per channel (typical use case).
    """
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    document_id: Mapped[Optional[int]] = mapped_column(ForeignKey("documents.id"), nullable=True, index=True)

    # Game association: NULL = general channel knowledge (always retrieved),
    # non-NULL = game-specific knowledge (only retrieved when generating
    # content for that game). This prevents cross-game knowledge leakage.
    game_id: Mapped[Optional[int]] = mapped_column(ForeignKey("games.id"), nullable=True, index=True)

    # The chunk text (the actual content that gets retrieved and injected
    # into LLM prompts as "channel knowledge context")
    content: Mapped[str] = mapped_column(Text)

    # Embedding vector stored as JSON array of floats.
    # SQLite doesn't have a native vector type, so we store as JSON and
    # compute cosine similarity in Python (fast for hundreds of chunks).
    embedding: Mapped[list] = mapped_column(JSON, default=list)

    # Chunk metadata for traceability and context building
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)  # position in document
    section: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # heading path
    char_start: Mapped[int] = mapped_column(Integer, default=0)
    char_end: Mapped[int] = mapped_column(Integer, default=0)

    # Embedding model used (for invalidation if model changes)
    embedding_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Multilingual: language of the chunk content (BCP-47 tag).
    # Used to filter embeddings by language during RAG retrieval.
    language: Mapped[str] = mapped_column(String(10), default="pt-BR")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    def __repr__(self) -> str:
        return f"<KnowledgeChunk #{self.id} doc={self.document_id} idx={self.chunk_index}>"


# ── V2: Content Intelligence + Embeddings ────────────────────────────────────


class KnowledgeItem(Base):
    """External content collected for the idea bank (V2 content intelligence).

    Distinct from Fact (which is user-uploaded document content). KnowledgeItem
    is exclusively for external content (RSS, Wikipedia, etc.). No mirroring
    between Fact and KnowledgeItem (D3 in ARCHITECTURE_V2.md) — unification
    happens at query time in ContentPlanningService.

    Lifecycle: fresh → used (after a video is generated from it) | rejected
    (user discarded). See D12.
    """
    __tablename__ = "knowledge_items"
    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_ki_content_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    game_id: Mapped[Optional[int]] = mapped_column(ForeignKey("games.id"), nullable=True, index=True)
    # REFACTORY_V2: visibility for the hybrid content pool model.
    # NULL user_id = system-collected (shared pool). user_id set + is_public=False
    # = private to owner. user_id set + is_public=True = shared with other users.
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # Identification
    title: Mapped[str] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)

    # Classification
    item_type: Mapped[str] = mapped_column(String(30), default=KnowledgeItemType.news.value, index=True)
    source_type: Mapped[str] = mapped_column(String(30), default=KnowledgeItemSource.rss.value)

    # Provenance
    source_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    source_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Editorial quality (single score — see D9 in ARCHITECTURE_V2.md)
    editorial_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)

    # Lifecycle state
    status: Mapped[str] = mapped_column(
        String(20), default=KnowledgeItemStatus.fresh.value, index=True
    )
    # REFACTORY_V2: reason for rejection (auto-rejected by quality gate or user)
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Denormalized from Game for filter-without-JOIN (see §7.1)
    franchise: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    developer: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)

    # Metadata
    tags: Mapped[list] = mapped_column(JSON, default=list)

    # Deduplication: SHA256(normalize(title) + normalize(content[:500]))
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    # ── Editorial Intelligence V2 — lifecycle ──────────────────────────────
    # freshness_score decays over time based on item_type (news decays fast,
    # lore is evergreen). Computed by LifecycleManager. 0.0–1.0.
    # See docs/EDITORIAL_INTELLIGENCE_V2_PROPOSAL.md §10.
    freshness_score: Mapped[float] = mapped_column(Float, default=1.0, index=True)
    # lifecycle_stage is ORTHOGONAL to status. status tracks fresh/used/rejected
    # (the editorial state). lifecycle_stage tracks fresh/aging/archived (the
    # temporal state). A KI can be status=fresh + lifecycle_stage=aging.
    lifecycle_stage: Mapped[str] = mapped_column(String(20), default="fresh", index=True)

    # ── Editorial Intelligence V2 — feedback adjustment ────────────────────
    # Cumulative per-user feedback adjustment to editorial_score. Capped at
    # ±MAX_CUMULATIVE_ADJUSTMENT to prevent death spirals. Decays over time.
    # See docs/CONVERGENCE_RISK_ANALYSIS.md Risk 4.
    feedback_adjustment: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationship to videos that used this item (D13: Video→KnowledgeItem direction)
    videos: Mapped[list["Video"]] = relationship(
        foreign_keys="Video.knowledge_item_id", back_populates="knowledge_item"
    )

    def __repr__(self) -> str:
        return f"<KnowledgeItem #{self.id} [{self.item_type}/{self.status}] score={self.editorial_score}>"


class KnowledgeItemEmbedding(Base):
    """Embedding vector for a KnowledgeItem, in a separate table (D4).

    Stored as BLOB (serialized float array). Separate table facilitates
    migration to pgvector without touching the main knowledge_items schema.
    """
    __tablename__ = "knowledge_item_embeddings"

    item_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="CASCADE"), primary_key=True
    )
    embedding: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    def __repr__(self) -> str:
        return f"<KnowledgeItemEmbedding item={self.item_id} model={self.model}>"


class KnowledgeItemUsage(Base):
    """Tracks per-consumer usage of a KnowledgeItem.

    For public/shared KnowledgeItems (user_id IS NULL, is_public=True),
    the global `status` field cannot track per-user consumption. This table
    records when a specific consumer used a specific KI, allowing:
    - User A uses public KI X → recorded here, KI stays fresh globally
    - User B sees KI X as still available (no usage record for B)
    - User B uses KI X → second usage record

    For private KIs (user_id set, is_public=False), the existing `status`
    field on KnowledgeItem continues to work since only the owner consumes.
    """
    __tablename__ = "knowledge_item_usages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_item_id: Mapped[int] = mapped_column(ForeignKey("knowledge_items.id"), nullable=False, index=True)
    consumer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    video_id: Mapped[Optional[int]] = mapped_column(ForeignKey("videos.id"), nullable=True, index=True)
    used_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    def __repr__(self) -> str:
        return f"<KnowledgeItemUsage ki={self.knowledge_item_id} consumer={self.consumer_user_id}>"


class ChannelProfileEmbedding(Base):
    """Embedding vector for a ChannelProfile, in a separate table.

    Generated from niche + channel_description + content_goals.
    Used by CompositeScorer to compute channel_affinity (Layer 2).
    Same pattern as KnowledgeItemEmbedding (BLOB, separate table).
    See docs/EDITORIAL_INTELLIGENCE_V2_PROPOSAL.md §14.3.
    """
    __tablename__ = "channel_profile_embeddings"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    embedding: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    def __repr__(self) -> str:
        return f"<ChannelProfileEmbedding user={self.user_id} model={self.model}>"


class EditorialSignal(Base):
    """Editorial feedback signal — records a learning event for the channel.

    Populated by the feedback loop (rejections, manual additions, production
    history, future: YouTube Analytics). Used to propagate adjustments to
    similar KIs via embeddings.

    See docs/EDITORIAL_INTELLIGENCE_V2_PROPOSAL.md §13.4.
    """
    __tablename__ = "editorial_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ki_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    signal_type: Mapped[str] = mapped_column(String(50))  # rejection_penalty, manual_add_boost, etc.
    signal_value: Mapped[float] = mapped_column(Float, default=0.0)
    source_ki_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_video_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    def __repr__(self) -> str:
        return f"<EditorialSignal #{self.id} type={self.signal_type} value={self.signal_value}>"


class AppRelease(Base):
    """Mobile app release — tracks the latest APK version for self-hosted distribution.

    The deploy script uploads a new APK and inserts a row here. The mobile app
    checks /api/app/version (which reads the latest row) and shows an update
    banner if the server's version_code is greater than the installed one.

    Only the most recent row (highest version_code) is served. Previous rows
    are kept for history but not exposed to clients.
    """
    __tablename__ = "app_releases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[str] = mapped_column(String(20))  # e.g. "0.9.3"
    version_code: Mapped[int] = mapped_column(Integer, index=True)  # e.g. 15
    released_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    changelog: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    deployed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    def __repr__(self) -> str:
        return f"<AppRelease v{self.version} code={self.version_code}>"


class Voice(Base):
    """Voice metadata — tracks language and display name for TTS voice files.

    System voices have user_id=NULL. User-uploaded voices have user_id set.
    The physical file lives in ``data/voices/`` (system) or
    ``data/voices/user_{id}/`` (user). This table only stores metadata.
    """
    __tablename__ = "voices"
    __table_args__ = (
        UniqueConstraint("user_id", "filename", name="uq_voices_user_filename"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # NULL = system voice; set = user-uploaded voice
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    # BCP-47 language tag (e.g. "pt-BR", "en-US", "zh-CN")
    language: Mapped[str] = mapped_column(String(10), default="pt-BR")
    display_name: Mapped[str] = mapped_column(String(100), default="")
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    def __repr__(self) -> str:
        owner = f"user_{self.user_id}" if self.user_id else "system"
        return f"<Voice {owner}/{self.filename} lang={self.language}>"
