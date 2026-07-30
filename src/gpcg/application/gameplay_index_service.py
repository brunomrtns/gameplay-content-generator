"""Gameplay index service — persistence and management of the semantic index.

Handles:
  - Persisting EventTimeline → GameplayEvent rows
  - Updating GameplaySource.metadata_json with analysis status
  - Querying events by type, time range, interesting score, compatibility
  - Reprocessing (delete old events, re-analyze with new config)
  - Status tracking (pending → analyzing → ready / failed)

This service is the bridge between the GameplayAnalyzer (which produces
EventTimeline) and the GameplayRetriever (which queries events during
video generation).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from gpcg.config import get_settings
from gpcg.domain.gameplay_events import EventTimeline, GameplayEventRecord
from gpcg.domain.models import AnalysisStatus, GameplayEvent, GameplaySource
from gpcg.logging import get_logger

log = get_logger(__name__)


class GameplayIndexService:
    """CRUD + queries for the gameplay semantic index."""

    def __init__(self) -> None:
        self.settings = get_settings()

    # ── Status management ────────────────────────────────────────────────

    def set_analysis_status(
        self,
        session: Session,
        source_id: int,
        status: str,
        *,
        version: str = "",
        vision_model: str = "",
        config_hash: str = "",
        event_count: int = 0,
        error: Optional[str] = None,
    ) -> None:
        """Update the analysis status in GameplaySource.metadata_json."""
        source = session.get(GameplaySource, source_id)
        if source is None:
            raise ValueError(f"GameplaySource #{source_id} not found")

        meta = dict(source.metadata_json or {})
        analysis = meta.get("analysis", {})
        analysis.update({
            "status": status,
            "version": version or analysis.get("version", ""),
            "vision_model": vision_model or analysis.get("vision_model", ""),
            "config_hash": config_hash or analysis.get("config_hash", ""),
            "event_count": event_count,
            "analyzed_at": datetime.now(timezone.utc).isoformat() if status == AnalysisStatus.ready.value else analysis.get("analyzed_at"),
            "error": error,
        })
        meta["analysis"] = analysis
        source.metadata_json = meta
        flag_modified(source, "metadata_json")
        session.flush()

    def get_analysis_status(self, session: Session, source_id: int) -> str:
        """Get the current analysis status for a source."""
        source = session.get(GameplaySource, source_id)
        if source is None:
            return AnalysisStatus.pending.value
        return source.analysis_status

    def is_ready(self, session: Session, source_id: int) -> bool:
        """Check if a source's semantic index is ready for retrieval."""
        return self.get_analysis_status(session, source_id) == AnalysisStatus.ready.value

    # ── Compatibility management ─────────────────────────────────────────

    def set_compatibility(
        self,
        session: Session,
        source_id: int,
        game_related: bool,
        general_topic: bool,
    ) -> None:
        """Set gameplay compatibility flags.

        game_related: can be used in videos about this specific game
        general_topic: can be used as background for general-topic videos
        """
        source = session.get(GameplaySource, source_id)
        if source is None:
            raise ValueError(f"GameplaySource #{source_id} not found")

        meta = dict(source.metadata_json or {})
        meta["compatibility"] = {
            "game_related": game_related,
            "general_topic": general_topic,
        }
        source.metadata_json = meta
        flag_modified(source, "metadata_json")
        session.flush()

    def get_compatibility(self, session: Session, source_id: int) -> dict:
        """Get compatibility flags for a source."""
        source = session.get(GameplaySource, source_id)
        if source is None:
            return {"game_related": True, "general_topic": True}
        return source.compatibility

    # ── Persistence ──────────────────────────────────────────────────────

    def persist_timeline(
        self,
        session: Session,
        timeline: EventTimeline,
        source_id: Optional[int] = None,
    ) -> int:
        """Persist an EventTimeline as GameplayEvent rows.

        Deletes any existing events for this source (reprocessing).
        Updates the source's analysis status to READY.

        Returns the number of events persisted.
        """
        sid = source_id or timeline.source_id
        if sid <= 0:
            raise ValueError("source_id required to persist timeline")

        # Delete old events (reprocessing support)
        old_events = session.execute(
            select(GameplayEvent).where(GameplayEvent.source_id == sid)
        ).scalars().all()
        for old in old_events:
            session.delete(old)
        session.flush()

        # Insert new events
        for record in timeline.events:
            ev = GameplayEvent(
                source_id=sid,
                start_time=record.start_time,
                end_time=record.end_time,
                event_type=record.event_type,
                description=record.description,
                characters=record.characters,
                location=record.location or None,
                actions=record.actions,
                tags=record.tags,
                transcript=record.transcript,
                visual_confidence=record.visual_confidence,
                interesting_score=record.interesting_score,
                analysis_version=timeline.analysis_version,
                metadata_json=record.metadata,
            )
            session.add(ev)

        # Update source analysis status
        self.set_analysis_status(
            session, sid,
            status=AnalysisStatus.ready.value,
            version=timeline.analysis_version,
            vision_model=timeline.vision_model,
            config_hash=timeline.config_hash,
            event_count=len(timeline.events),
        )

        session.flush()
        log.info(f"persisted {len(timeline.events)} events for source #{sid}")
        return len(timeline.events)

    # ── Queries ──────────────────────────────────────────────────────────

    def get_events(
        self,
        session: Session,
        source_id: int,
        *,
        min_confidence: float = 0.0,
        min_interesting: float = 0.0,
        event_type: Optional[str] = None,
    ) -> list[GameplayEvent]:
        """Query events for a source with optional filters."""
        stmt = select(GameplayEvent).where(GameplayEvent.source_id == source_id)
        if min_confidence > 0:
            stmt = stmt.where(GameplayEvent.visual_confidence >= min_confidence)
        if min_interesting > 0:
            stmt = stmt.where(GameplayEvent.interesting_score >= min_interesting)
        if event_type:
            et = event_type.upper()
            # Match both COMBAT and POSSIBLE_COMBAT
            stmt = stmt.where(
                (GameplayEvent.event_type == et)
                | (GameplayEvent.event_type == f"POSSIBLE_{et}")
            )
        stmt = stmt.order_by(GameplayEvent.start_time)
        return list(session.execute(stmt).scalars().all())

    def get_interesting_events(
        self,
        session: Session,
        source_id: int,
        *,
        min_interesting: Optional[float] = None,
        limit: int = 50,
    ) -> list[GameplayEvent]:
        """Get the most editorially interesting events for a source."""
        threshold = min_interesting if min_interesting is not None else self.settings.gpcg_gameplay_interesting_threshold
        stmt = (
            select(GameplayEvent)
            .where(GameplayEvent.source_id == source_id)
            .where(GameplayEvent.interesting_score >= threshold)
            .order_by(GameplayEvent.interesting_score.desc())
            .limit(limit)
        )
        return list(session.execute(stmt).scalars().all())

    def search_events(
        self,
        session: Session,
        source_id: int,
        query: str,
        *,
        min_confidence: float = 0.3,
        limit: int = 20,
    ) -> list[GameplayEvent]:
        """Text search over event descriptions, transcripts, locations, tags, and actions.

        Searches description, transcript, location (LIKE) plus tags and actions
        (JSON arrays — matched as substring on the JSON text). This is a basic
        text search. A future enhancement could use embeddings (nomic-embed-text)
        for semantic search.

        The cascaded pipeline produces rich tags like ``on_skate``, ``on_bike``,
        ``combat``, ``riding`` — searching these tags is the most reliable way
        to find specific player actions.
        """
        pattern = f"%{query}%"
        stmt = (
            select(GameplayEvent)
            .where(GameplayEvent.source_id == source_id)
            .where(GameplayEvent.visual_confidence >= min_confidence)
            .where(
                GameplayEvent.description.ilike(pattern)
                | GameplayEvent.transcript.ilike(pattern)
                | GameplayEvent.location.ilike(pattern)
                | GameplayEvent.tags.ilike(pattern)
                | GameplayEvent.actions.ilike(pattern)
                | GameplayEvent.event_type.ilike(pattern)
            )
            .order_by(GameplayEvent.interesting_score.desc())
            .limit(limit)
        )
        return list(session.execute(stmt).scalars().all())

    def get_compatible_sources(
        self,
        session: Session,
        *,
        game_id: Optional[int] = None,
        video_type: str = "GAME_RELATED",
        require_ready: bool = False,
    ) -> list[GameplaySource]:
        """Find gameplay sources compatible with a video type.

        Args:
            game_id: for GAME_RELATED, prefer sources of this game
            video_type: "GAME_RELATED" or "GENERAL_TOPIC"
            require_ready: only return sources with analysis status READY
        """
        stmt = select(GameplaySource).where(
            GameplaySource.ingestion_status == "ready"
        )
        if game_id is not None and video_type == "GAME_RELATED":
            stmt = stmt.where(GameplaySource.game_id == game_id)
        sources = list(session.execute(stmt).scalars().all())

        # Filter by compatibility (stored in metadata_json)
        result = []
        for src in sources:
            compat = src.compatibility
            if video_type == "GAME_RELATED" and not compat.get("game_related", True):
                continue
            if video_type == "GENERAL_TOPIC" and not compat.get("general_topic", True):
                continue
            if require_ready and not src.is_analysis_ready:
                continue
            result.append(src)

        return result

    # ── Reprocessing ─────────────────────────────────────────────────────

    def needs_reprocessing(
        self,
        session: Session,
        source_id: int,
        current_config_hash: str,
    ) -> bool:
        """Check if a source needs reprocessing (config hash changed)."""
        source = session.get(GameplaySource, source_id)
        if source is None:
            return False
        info = source.analysis_info
        if info.get("status") != AnalysisStatus.ready.value:
            return True  # not ready → needs (re)processing
        return info.get("config_hash", "") != current_config_hash

    def save_analysis_json(self, timeline: EventTimeline, path: Optional[Path] = None) -> Path:
        """Save timeline as JSON for MVP verification / inspection."""
        if path is None:
            path = self.settings.gameplay_analysis_dir / f"source_{timeline.source_id}_analysis.json"
        else:
            path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(timeline.to_json(indent=2), encoding="utf-8")
        log.info(f"analysis JSON saved: {path}")
        return path
