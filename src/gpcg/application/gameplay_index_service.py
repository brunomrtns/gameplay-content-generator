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
from gpcg.domains.games.models import AnalysisStatus, GameplayEvent, GameplaySource
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
        """Semantic search over events using embeddings, with text fallback.

        V3: When GameplayEvent embeddings are available, uses cosine similarity
        against the query embedding (generated via nomic-embed-text). Falls back
        to ILIKE text search when embeddings are not available or LLM is offline.

        The cascaded pipeline produces rich tags like ``on_skate``, ``on_bike``,
        ``combat``, ``riding`` — the text fallback searches these tags plus
        description, transcript, location, and actions.
        """
        # V3: Try semantic search first
        semantic_results = self._search_events_semantic(
            session, source_id, query, min_confidence=min_confidence, limit=limit
        )
        if semantic_results is not None:
            return semantic_results

        # Fallback: text search (ILIKE)
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

    def _search_events_semantic(
        self,
        session: Session,
        source_id: int,
        query: str,
        *,
        min_confidence: float = 0.3,
        limit: int = 20,
        min_similarity: float = 0.3,
    ) -> Optional[list[GameplayEvent]]:
        """Semantic search using embeddings. Returns None if unavailable.

        Generates a query embedding via Ollama, then computes cosine similarity
        against all event embeddings for this source. Returns events sorted by
        similarity (descending), filtered by min_similarity and min_confidence.

        Returns None (not empty list) when embeddings are not available or
        the LLM is offline, so the caller can fall back to text search.
        """
        from gpcg.application.embedding_service import (
            get_gameplay_event_embedding,
            cosine_similarity,
        )
        from gpcg.infrastructure.llm import LLMClient, LLMError

        # Get all events for this source with confidence filter
        stmt = (
            select(GameplayEvent)
            .where(GameplayEvent.source_id == source_id)
            .where(GameplayEvent.visual_confidence >= min_confidence)
        )
        events = list(session.execute(stmt).scalars().all())
        if not events:
            return None

        # Check if any events have embeddings
        embeddings: dict[int, list[float]] = {}
        for ev in events:
            vec = get_gameplay_event_embedding(session, ev.id)
            if vec:
                embeddings[ev.id] = vec

        if not embeddings:
            # No embeddings available — signal fallback
            return None

        # Generate query embedding
        try:
            from gpcg.application.embedding_service import EMBEDDING_MODEL
            llm = LLMClient()
            query_vec = llm.embed(query, model=EMBEDDING_MODEL)
            if not query_vec:
                return None
        except (LLMError, Exception) as e:
            log.info(f"semantic search: LLM unavailable ({e}), falling back to text search")
            return None

        # Score events by cosine similarity
        scored: list[tuple[float, GameplayEvent]] = []
        for ev in events:
            vec = embeddings.get(ev.id)
            if vec:
                sim = cosine_similarity(query_vec, vec)
                if sim >= min_similarity:
                    scored.append((sim, ev))

        # Sort by similarity (desc), then by interesting_score as tiebreaker
        scored.sort(key=lambda x: (x[0], x[1].interesting_score), reverse=True)

        result = [ev for _, ev in scored[:limit]]
        log.info(
            f"semantic search: query='{query[:40]}' source={source_id} "
            f"found {len(result)} events (from {len(events)} total, "
            f"{len(embeddings)} embedded)"
        )
        return result

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
