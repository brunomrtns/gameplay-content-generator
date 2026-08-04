"""Domain models — SQLAlchemy ORM for the gameplay-content-generator.

Entities:
  User            — platform user (auth, multi-tenant isolation)
  Automation       — per-user automation config (one per user)
  Game            — canonical game registry (name, aliases, platforms)
  GameplaySource  — original recording file from the inbox
  GameplayAsset   — a reusable clip (start/end) of a source
  Document        — uploaded reference doc (PDF/TXT/MD/DOCX) per game
  Fact            — extracted fact/curiosity from documents
  ContentPlan     — AI-decided plan for a video (topic, hook, tone, music_mood)
  Script          — draft → optimized → final narration text
  Job             — pipeline job with stage/progress/status/priority
  Video           — generated output video with QA report
  Worker          — compute worker (local PC with GPU) registered with the VPS
  ResearchCache   — (reserved for future web search; unused in MVP)

Architecture: Control Plane (VPS) + Compute Plane (Workers).
The VPS stores only metadata and orchestrates jobs. Workers do all heavy
processing (VLM, ASR, FFmpeg, rendering) on their own GPU/storage.
"""

from __future__ import annotations

import enum
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
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
    """Declarative base for all models."""


# ── Enums ─────────────────────────────────────────────────────────────────────


class IngestionStatus(str, enum.Enum):
    discovered = "discovered"
    probing = "probing"
    ready = "ready"
    duplicate = "duplicate"
    error = "error"
    needs_review = "needs_review"


class GameResolutionMethod(str, enum.Enum):
    deterministic = "deterministic"
    prior = "prior"
    vlm = "vlm"
    manual = "manual"
    unknown = "unknown"


class CameraType(str, enum.Enum):
    """Camera perspective used by a game.

    Drives how the GameplayAnalyzer extracts and analyzes frames:
    - third_person: player character visible on screen (typically center-low).
      Pipeline crops around the detected player and upscales for action
      recognition (on foot, bike, skate, vehicle, armed, fighting, etc.).
    - first_person: player's arms/weapon visible in lower corners; the center
      of the frame is what the player sees/aims at. Pipeline inspects lower
      corners for held items/weapons and the center for the target/view.
    - top_down: player seen from above (e.g., RTS, ARPG, old-school RPGs).
      Player is small but centered; crop + upscale similar to third_person.
    - isometric: fixed diagonal angle (e.g., CRPGs, tactics). Same strategy
      as top_down.
    - fixed: fixed/static camera (e.g., classic Resident Evil, point-and-click).
      Player position varies; rely on YOLO person detection to locate.
    - unknown: not yet specified. Pipeline falls back to a generic full-frame
      analysis (legacy behavior) and logs a warning.
    """

    third_person = "third_person"
    first_person = "first_person"
    top_down = "top_down"
    isometric = "isometric"
    fixed = "fixed"
    unknown = "unknown"


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


class ContentScope(str, enum.Enum):
    """Scope for content intelligence and gameplay selection (V2).

    - game: only the specific game
    - franchise: games in the same franchise (Game.franchise match)
    - developer: games by the same developer (Game.developer match)
    """
    game = "game"
    franchise = "franchise"
    developer = "developer"


class GameplayProcessingStatus(str, enum.Enum):
    """Lifecycle of a gameplay from upload to ready-to-use.

    Flow:
      UPLOADING → UPLOADED → WAITING_WORKER → DOWNLOADING → DOWNLOADED
      → MAPPING → MAPPED → READY
      (FAILED can occur at any step)

    The VPS stores the file only until DOWNLOADED is confirmed (checksum
    verified). After that, the temp file is deleted and only metadata remains.
    """
    uploading = "uploading"  # user is uploading the file
    uploaded = "uploaded"  # upload complete, no mapping job yet
    waiting_worker = "waiting_worker"  # mapping job queued, waiting for a worker
    downloading = "downloading"  # worker is downloading the file
    downloaded = "downloaded"  # worker confirmed download (checksum OK), VPS can delete temp
    mapping = "mapping"  # worker is running analysis (VLM + ASR)
    mapped = "mapped"  # analysis complete, events reported to VPS
    ready = "ready"  # ready to be used for video generation
    generating = "generating"  # a generation job is using this gameplay
    finished = "finished"  # a generation job completed using this gameplay
    failed = "failed"  # something went wrong


class AnalysisStatus(str, enum.Enum):
    """Status of semantic gameplay analysis (separate from ingestion)."""
    pending = "pending"
    analyzing = "analyzing"
    indexing = "indexing"
    ready = "ready"
    failed = "failed"


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


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    canonical_name: Mapped[str] = mapped_column(String(200), index=True)
    aliases: Mapped[list] = mapped_column(JSON, default=list)  # deprecated — game_aliases is source of truth (V2)
    platforms: Mapped[list] = mapped_column(JSON, default=list)  # ["PS2", "PC"]
    capture_sources: Mapped[list] = mapped_column(JSON, default=list)  # ["OBS", "Yuzu"]
    # Camera perspective — drives the GameplayAnalyzer's frame extraction
    # strategy (player crop + upscale for third_person, lower-corner inspection
    # for first_person, etc.). Defaults to "unknown" (legacy full-frame analysis).
    # Set manually per game via API/CLI.
    camera_type: Mapped[str] = mapped_column(
        String(32), default=CameraType.unknown.value, server_default=CameraType.unknown.value
    )
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    # ── V2: Canonical identity + enrichment fields ──────────────────────────
    # Slug is the canonical unique identifier (slugify(canonical_name)).
    # Generated on creation, never changes (even if canonical_name is edited).
    slug: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # from Wikipedia
    release_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # from Wikidata
    # Canonical names from Wikidata (strings, not FKs — see D1 in ARCHITECTURE_V2.md)
    developer: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    publisher: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    franchise: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    genres: Mapped[list] = mapped_column(JSON, default=list)  # ["action", "adventure", "open-world"]
    themes: Mapped[list] = mapped_column(JSON, default=list)  # ["school", "rebellion"]
    lore_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # LLM-generated from Wikipedia
    external_ids: Mapped[dict] = mapped_column(JSON, default=dict)  # {"wikidata": "Q123", "steam": 123456}
    enriched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # NULL = not enriched
    enrichment_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # NULL = no error

    sources: Mapped[list["GameplaySource"]] = relationship(back_populates="game", cascade="all, delete-orphan")
    facts: Mapped[list["Fact"]] = relationship(back_populates="game", cascade="all, delete-orphan")
    documents: Mapped[list["Document"]] = relationship(back_populates="game", cascade="all, delete-orphan")
    content_plans: Mapped[list["ContentPlan"]] = relationship(
        back_populates="game", cascade="all, delete-orphan", foreign_keys="ContentPlan.game_id"
    )
    # V2: aliases as individual rows (indexable, with provenance)
    alias_rows: Mapped[list["GameAlias"]] = relationship(
        back_populates="game", cascade="all, delete-orphan", foreign_keys="GameAlias.game_id"
    )

    def __repr__(self) -> str:
        return f"<Game {self.canonical_name}>"

    @property
    def is_enriched(self) -> bool:
        """True when enriched_at is set AND no enrichment error (V2)."""
        return self.enriched_at is not None and self.enrichment_error is None

    @property
    def enrichment_state(self) -> str:
        """One of: 'pending', 'enriched', 'error' (V2)."""
        if self.enrichment_error is not None:
            return "error"
        if self.enriched_at is not None:
            return "enriched"
        return "pending"


class GameAlias(Base):
    """Individual alias for a Game, indexable and with provenance (V2).

    Replaces the JSON `aliases` column on Game as the source of truth for
    alias lookups. The JSON column is kept for backward compatibility but
    is no longer the primary lookup mechanism.

    Provenance (`source`):
    - "manual": user added via UI/API
    - "resolver": added by the GameResolver when a filename differs from canonical
    - "wikidata": added during enrichment (alternative names from Wikidata)
    """
    __tablename__ = "game_aliases"
    __table_args__ = (
        UniqueConstraint("game_id", "alias", name="uq_game_alias_per_game"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True)
    alias: Mapped[str] = mapped_column(String(200), nullable=False)
    alias_type: Mapped[str] = mapped_column(String(30), default="alternative")  # alternative, abbreviation, etc.
    source: Mapped[str] = mapped_column(String(50), default="manual")  # manual, resolver, wikidata
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    game: Mapped["Game"] = relationship(back_populates="alias_rows", foreign_keys=[game_id])

    def __repr__(self) -> str:
        return f"<GameAlias '{self.alias}' → game={self.game_id}>"


class GameplaySource(Base):
    __tablename__ = "gameplay_sources"
    __table_args__ = (UniqueConstraint("file_hash", "user_id", name="uq_sources_user_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    game_id: Mapped[Optional[int]] = mapped_column(ForeignKey("games.id"), nullable=True, index=True)

    # Legacy physical path (kept for backward compat with existing local data).
    # New uploads use storage_key instead — the physical path is resolved by
    # the storage layer, not stored in the DB.
    file_path: Mapped[str] = mapped_column(String(1024), default="")
    # Opaque storage key (e.g., "gameplays/user_1/abc12345_Bully.mp4").
    # The storage layer resolves this to a physical path. Allows swapping
    # filesystem/S3/MinIO without changing the DB.
    storage_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, index=True)
    # One-time token authorizing a worker to download the temp file from VPS.
    # Generated when a mapping job is claimed; invalidated after download confirmed.
    upload_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    filename: Mapped[str] = mapped_column(String(255), index=True)
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)

    capture_source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    recorded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Media metadata (from FFprobe)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    fps: Mapped[float] = mapped_column(Float, default=0.0)
    codec: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False)

    # Ingestion state (legacy: discovered/probing/ready/duplicate/error/needs_review)
    ingestion_status: Mapped[str] = mapped_column(String(30), default=IngestionStatus.discovered.value, index=True)
    resolution_method: Mapped[str] = mapped_column(String(30), default=GameResolutionMethod.unknown.value)
    resolution_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    resolution_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Control Plane + Compute Plane: processing lifecycle
    # Tracks the gameplay from upload through mapping to ready-to-use.
    # The VPS only stores the temp file until DOWNLOADED is confirmed.
    processing_status: Mapped[str] = mapped_column(
        String(30), default=GameplayProcessingStatus.uploaded.value, index=True
    )
    downloaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    downloaded_by_worker: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Flexible metadata: compatibility flags, analysis status, etc.
    # {"compatibility": {"game_related": bool, "general_topic": bool},
    #  "analysis": {"status": "pending|analyzing|ready|failed", "version": str, ...}}
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    # V2: Public gameplay — when True, this gameplay is available to ALL users
    # as a fallback (only used when the user's own gameplays for the game are
    # exhausted). Default: False (private).
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    game: Mapped[Optional["Game"]] = relationship(back_populates="sources")
    assets: Mapped[list["GameplayAsset"]] = relationship(back_populates="source", cascade="all, delete-orphan")
    events: Mapped[list["GameplayEvent"]] = relationship(back_populates="source", cascade="all, delete-orphan")
    clip_usages: Mapped[list["GameplayClipUsage"]] = relationship(back_populates="source", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<GameplaySource {self.filename}>"

    @property
    def compatibility(self) -> dict:
        """Read gameplay compatibility flags from metadata_json.
        Returns {"game_related": bool, "general_topic": bool}.
        Defaults to both True (compatible with everything).
        """
        return self.metadata_json.get("compatibility", {
            "game_related": True,
            "general_topic": True,
        })

    @property
    def analysis_info(self) -> dict:
        """Read analysis status/metadata from metadata_json.
        Returns {"status": str, "version": str, ...}.
        """
        return self.metadata_json.get("analysis", {
            "status": AnalysisStatus.pending.value,
            "version": "",
            "vision_model": "",
            "config_hash": "",
            "event_count": 0,
            "analyzed_at": None,
            "error": None,
        })

    @property
    def analysis_status(self) -> str:
        return self.analysis_info.get("status", AnalysisStatus.pending.value)

    @property
    def is_analysis_ready(self) -> bool:
        return self.analysis_status == AnalysisStatus.ready.value


class GameplayAsset(Base):
    """A reusable clip (start→end) of a gameplay source. MVP: manually defined."""
    __tablename__ = "gameplay_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("gameplay_sources.id"), index=True)
    label: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    start_sec: Mapped[float] = mapped_column(Float, default=0.0)
    end_sec: Mapped[float] = mapped_column(Float, default=0.0)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    source: Mapped["GameplaySource"] = relationship(back_populates="assets")

    @property
    def clip_path(self) -> str:
        """Path to the extracted clip file (lazily created by render pipeline)."""
        return f"asset_{self.id}.mp4"

    def __repr__(self) -> str:
        return f"<GameplayAsset #{self.id} [{self.start_sec:.1f}-{self.end_sec:.1f}]>"


class GameplayClipUsage(Base):
    """Tracks which specific time ranges of a gameplay source have been used
    in a video. This prevents reusing the same gameplay segment across videos.

    When a video is deleted with the "release clips" option, the corresponding
    usage records are removed, making those segments available again.

    REFACTORY_V2: ``consumer_user_id`` is the user who consumed this segment
    (the video owner). This allows per-consumer usage history for public
    gameplays — user A using a public gameplay segment doesn't block user B
    from using the same segment.
    """
    __tablename__ = "gameplay_clip_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("gameplay_sources.id"), index=True)
    # REFACTORY_V2: consumer who used this segment (denormalized from Video.user_id
    # for efficient per-consumer queries without JOIN).
    consumer_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    start_sec: Mapped[float] = mapped_column(Float, default=0.0)
    end_sec: Mapped[float] = mapped_column(Float, default=0.0)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    source: Mapped["GameplaySource"] = relationship(back_populates="clip_usages")

    def __repr__(self) -> str:
        return f"<GameplayClipUsage video={self.video_id} source={self.source_id} consumer={self.consumer_user_id} [{self.start_sec:.1f}-{self.end_sec:.1f}]>"


class GameplayEvent(Base):
    """A semantically-identified event in a gameplay recording.

    Produced by the GameplayAnalyzer (coarse → adaptive refine → ASR → merge).
    Each event represents a temporally-bounded happening with:
    - event_type: COMBAT, CHASE, DIALOGUE, CUTSCENE, EXPLORATION, etc.
    - visual_confidence: how confident the VLM is (0-1)
    - interesting_score: editorial usefulness (0-1), separate from confidence
    - transcript: ASR text overlapping this event (if audio available)

    The index is built once during ingestion and queried during video
    generation by the GameplayRetriever for semantic clip matching.
    """
    __tablename__ = "gameplay_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("gameplay_sources.id"), index=True)

    start_time: Mapped[float] = mapped_column(Float, default=0.0)
    end_time: Mapped[float] = mapped_column(Float, default=0.0)

    # Event classification. May be prefixed with POSSIBLE_ when ambiguous.
    # Examples: COMBAT, CHASE, DIALOGUE, CUTSCENE, EXPLORATION, VEHICLE,
    # MENU, LOADING, POSSIBLE_COMBAT, UNKNOWN, etc.
    event_type: Mapped[str] = mapped_column(String(50), default="unknown", index=True)
    description: Mapped[str] = mapped_column(Text, default="")

    # Structured metadata from VLM analysis
    characters: Mapped[list] = mapped_column(JSON, default=list)  # ["Jimmy", "teacher"]
    location: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    actions: Mapped[list] = mapped_column(JSON, default=list)  # ["running", "fighting"]
    tags: Mapped[list] = mapped_column(JSON, default=list)  # free-form tags

    # ASR transcript overlapping this event's time range (empty if no audio)
    transcript: Mapped[str] = mapped_column(Text, default="")

    # Confidence scores (0-1). These are SEPARATE concepts:
    # - visual_confidence: how sure the VLM is about what it sees
    # - interesting_score: how useful this event is for editorial/video editing
    # High confidence + low interesting = "we know it's boring walking"
    # High confidence + high interesting = "we know it's an exciting chase"
    # Low confidence + high interesting = "might be interesting but unclear"
    visual_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    interesting_score: Mapped[float] = mapped_column(Float, default=0.0)

    # Analysis provenance for versioning/reprocessing
    analysis_version: Mapped[str] = mapped_column(String(20), default="v1")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    source: Mapped["GameplaySource"] = relationship(back_populates="events")

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    @property
    def is_confident(self) -> bool:
        """True if visual_confidence >= 0.7 (observed with high confidence)."""
        return self.visual_confidence >= 0.7

    @property
    def is_possible(self) -> bool:
        """True if event_type starts with POSSIBLE_ (ambiguous inference)."""
        return self.event_type.startswith("POSSIBLE_")

    def __repr__(self) -> str:
        return f"<GameplayEvent [{self.start_time:.1f}-{self.end_time:.1f}] {self.event_type}>"


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
    topic: Mapped[str] = mapped_column(Text)
    hook: Mapped[str] = mapped_column(Text)
    tone: Mapped[str] = mapped_column(String(50), default="curious")
    energy: Mapped[float] = mapped_column(Float, default=0.7)
    music_mood: Mapped[str] = mapped_column(String(50), default="neutral")
    visual_strategy: Mapped[str] = mapped_column(String(50), default="gameplay_compilation")
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

    This is the semantic context that tells the AI WHAT kind of channel the
    user is building and HOW videos should be narrated. It flows into every
    LLM call in the pipeline (content planning, editorial planning, script
    generation) so that generated videos are personalized to the channel
    rather than generic.

    One per user (unique user_id).
    """
    __tablename__ = "channel_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)

    # Free-form channel description — the "elevator pitch" of the channel.
    # Example: "Meu canal é focado em análises de partidas competitivas de FPS.
    # Quero vídeos com tom educativo, destacando estratégias, erros dos
    # jogadores e momentos decisivos."
    channel_description: Mapped[str] = mapped_column(Text, default="")

    # Structured fields (optional but recommended)
    niche: Mapped[str] = mapped_column(String(200), default="")          # e.g. "FPS competitivo"
    target_audience: Mapped[str] = mapped_column(String(300), default="")  # e.g. "Jogadores casuais que querem melhorar"
    tone_of_voice: Mapped[str] = mapped_column(String(100), default="")   # e.g. "educativo, analítico"
    narrative_style: Mapped[str] = mapped_column(String(100), default="")  # e.g. "storytelling", "análise direta"
    content_goals: Mapped[str] = mapped_column(Text, default="")          # what the channel wants to achieve
    special_rules: Mapped[str] = mapped_column(Text, default="")          # specific rules/preferences for the AI

    # Additional metadata (extensible)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    user: Mapped["User"] = relationship()

    def __repr__(self) -> str:
        return f"<ChannelProfile user={self.user_id} niche={self.niche!r}>"

    def to_prompt_context(self) -> str:
        """Build a natural-language context block for LLM prompts.

        This is the text that gets injected into system/user prompts across
        the pipeline so the AI knows the channel's identity and direction.
        """
        parts = []
        if self.channel_description:
            parts.append(f"Descrição do canal: {self.channel_description}")
        if self.niche:
            parts.append(f"Nicho: {self.niche}")
        if self.target_audience:
            parts.append(f"Público-alvo: {self.target_audience}")
        if self.tone_of_voice:
            parts.append(f"Tom de voz: {self.tone_of_voice}")
        if self.narrative_style:
            parts.append(f"Estilo de narrativa: {self.narrative_style}")
        if self.content_goals:
            parts.append(f"Objetivos: {self.content_goals}")
        if self.special_rules:
            parts.append(f"Regras especiais: {self.special_rules}")
        return "\n".join(parts) if parts else ""

    def to_stage_context(self, stage: str) -> str:
        """Return channel context relevant to a specific pipeline stage.

        Stages:
        - content_planning: niche, content_goals, target_audience
        - story_finding: tone_of_voice, narrative_style
        - editorial_planning: niche, target_audience, tone_of_voice, special_rules
        - script: full context (use to_prompt_context())
        """
        parts = []
        if stage == "content_planning":
            if self.niche:
                parts.append(f"Nicho do canal: {self.niche}")
            if self.content_goals:
                parts.append(f"Objetivos de conteúdo: {self.content_goals}")
            if self.target_audience:
                parts.append(f"Público-alvo: {self.target_audience}")
        elif stage == "story_finding":
            if self.tone_of_voice:
                parts.append(f"Tom de voz: {self.tone_of_voice}")
            if self.narrative_style:
                parts.append(f"Estilo narrativo: {self.narrative_style}")
        elif stage == "editorial_planning":
            if self.niche:
                parts.append(f"Nicho do canal: {self.niche}")
            if self.target_audience:
                parts.append(f"Público-alvo: {self.target_audience}")
            if self.tone_of_voice:
                parts.append(f"Tom de voz: {self.tone_of_voice}")
            if self.special_rules:
                parts.append(f"Regras especiais: {self.special_rules}")
        else:
            # Full context for script and other stages
            return self.to_prompt_context()

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


class GameplayEventEmbedding(Base):
    """Embedding vector for a GameplayEvent, in a separate table (D4).

    Generated during mapping (worker local, GPU) after the VLM produces
    event descriptions. Model: nomic-embed-text (Ollama).
    """
    __tablename__ = "gameplay_event_embeddings"

    event_id: Mapped[int] = mapped_column(
        ForeignKey("gameplay_events.id", ondelete="CASCADE"), primary_key=True
    )
    embedding: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    def __repr__(self) -> str:
        return f"<GameplayEventEmbedding event={self.event_id} model={self.model}>"
