"""Games domain models — game-specific SQLAlchemy ORM for GPCG.

These models are the Games-specific layer of the platform: games, gameplay
sources, gameplay events, gameplay assets, clip usage tracking, etc.

They share the same ``Base`` as core models (from ``gpcg.core.models``) so
that SQLAlchemy FKs and relationships resolve correctly across the boundary.

Both ``gpcg.core.models`` and ``gpcg.domains.games.models`` must be imported
before any ``create_all()`` or query is executed. ``database.init_db()``
handles this.
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
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from gpcg.core.models import Base, _utcnow


# ── Enums ─────────────────────────────────────────────────────────────────────


class IngestionStatus(str, enum.Enum):
    discovered = "discovered"
    probing = "probing"
    ready = "ready"
    duplicate = "duplicate"
    error = "error"
    needs_review = "needs_review"
    deleted = "deleted"


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


class ContentScope(str, enum.Enum):
    """Scope for content intelligence and gameplay selection (V2).

    - game: only the specific game
    - franchise: games in the same franchise (Game.franchise match)
    - developer: games by the same developer (Game.developer match)
    - general: general curiosity (not tied to a specific game)
    """
    game = "game"
    franchise = "franchise"
    developer = "developer"
    general = "general"


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


# ── Models ────────────────────────────────────────────────────────────────────


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

    # V3: User-controlled availability toggle. When False, the gameplay is
    # excluded from generation/automation selection but kept in the library
    # for future use. Default: True (available).
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

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


class GameplayDownload(Base):
    """Tracks which workers have downloaded a gameplay file.

    In multi-worker mode, the VPS keeps the temp file until ALL registered
    workers with 'mapping' or 'generation' capability have confirmed download
    (or until retention expiry). This table tracks per-worker download state.

    Backward compat: GameplaySource.downloaded_by_worker (string) is still
    set to the FIRST worker that confirms, but this table is the source of
    truth for multi-worker tracking.
    """
    __tablename__ = "gameplay_downloads"
    __table_args__ = (
        UniqueConstraint("source_id", "worker_id", name="uq_download_worker"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("gameplay_sources.id"), nullable=False, index=True
    )
    worker_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    checksum_verified: Mapped[bool] = mapped_column(Boolean, default=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    def __repr__(self) -> str:
        return f"<GameplayDownload source={self.source_id} worker={self.worker_id}>"


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
