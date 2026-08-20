"""Ingestion service — discovers, probes, and registers gameplay recordings.

Idempotent: re-scanning the inbox never duplicates records (keyed on file_hash).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.domain.filename_parser import parse_filename
from gpcg.domain.game_repository import get_or_create
from gpcg.domain.game_resolver import ResolutionResult, resolve
from gpcg.domains.games.models import GameResolutionMethod, GameplaySource, IngestionStatus
from gpcg.infrastructure.database import session_scope
from gpcg.infrastructure.llm import LLMClient, get_llm
from gpcg.infrastructure.media import (
    MediaError,
    file_hash,
    is_file_stable,
    probe,
)
from gpcg.logging import get_logger

log = get_logger(__name__)

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".flv", ".webm", ".ts", ".m4v"}


class IngestionService:
    """Watches the inbox and ingests new gameplay recordings."""

    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self.settings = get_settings()
        self.llm = llm  # may be None to skip VLM (L3)

    def scan_once(self, user_id: Optional[int] = None) -> int:
        """Scan the inbox directory once. Returns count of newly ingested files.

        Args:
            user_id: If provided, attributes ingested sources to this user
                     and dedups only within this user's sources.
        """
        inbox = self.settings.inbox_dir
        if not inbox.exists():
            log.warning(f"inbox dir does not exist: {inbox}")
            return 0

        count = 0
        for entry in sorted(inbox.iterdir()):
            if entry.is_dir():
                continue
            if entry.suffix.lower() not in VIDEO_EXTS:
                continue
            try:
                if self._ingest_file(entry, user_id=user_id):
                    count += 1
            except Exception as e:
                log.error(f"failed to ingest {entry.name}: {e}")
        return count

    def _ingest_file(self, path: Path, user_id: Optional[int] = None) -> bool:
        """Ingest a single file. Returns True if newly ingested, False if skipped.

        Args:
            user_id: If provided, dedups only within this user's sources
                     and attributes the new source to this user.
        """
        # Check size threshold
        min_bytes = self.settings.gpcg_inbox_min_size_mb * 1024 * 1024
        try:
            size = path.stat().st_size
        except OSError:
            return False
        if size < min_bytes:
            log.debug(f"skipping (too small): {path.name}")
            return False

        # Check file is stable (recording finished)
        if not is_file_stable(path, stable_seconds=self.settings.gpcg_inbox_stable_seconds):
            log.debug(f"skipping (not stable yet): {path.name}")
            return False

        # Compute hash
        try:
            fhash = file_hash(path)
        except OSError as e:
            log.error(f"cannot hash {path.name}: {e}")
            return False

        # Idempotency: skip if already ingested (dedup per user when user_id is set)
        with session_scope() as session:
            dedup_query = select(GameplaySource).where(GameplaySource.file_hash == fhash)
            if user_id is not None:
                dedup_query = dedup_query.where(GameplaySource.user_id == user_id)
            existing = session.execute(dedup_query).scalar_one_or_none()
            if existing:
                log.debug(f"already ingested: {path.name} (source #{existing.id})")
                return False

            # Probe media
            try:
                info = probe(path)
            except MediaError as e:
                log.error(f"probe failed for {path.name}: {e}")
                self._record_error(session, path, fhash, str(e))
                return False

            # Parse filename
            parsed = parse_filename(path.name)

            # Resolve game (L1 → L2 → L3)
            llm = self.llm or get_llm() if self._ollama_available() else None
            try:
                result = resolve(path, path.name, session, llm=llm)
            except Exception as e:
                log.error(f"game resolution failed for {path.name}: {e}")
                result = ResolutionResult(
                    game_name=None,
                    method=GameResolutionMethod.unknown.value,
                    confidence=0.0,
                    capture_source=parsed.capture_source,
                    notes=f"resolution error: {e}",
                )

            # Get or create game
            game_id = None
            if result.game_name and not result.needs_review:
                game = get_or_create(
                    session,
                    result.game_name,
                    capture_sources=[result.capture_source] if result.capture_source else None,
                )
                game_id = game.id

            # Determine ingestion status
            if result.needs_review:
                status = IngestionStatus.needs_review.value
            else:
                status = IngestionStatus.ready.value

            source = GameplaySource(
                game_id=game_id,
                user_id=user_id,
                file_path=str(path),
                filename=path.name,
                file_hash=fhash,
                file_size=info.file_size,
                capture_source=parsed.capture_source,
                recorded_at=parsed.recorded_at,
                duration=info.duration,
                width=info.width,
                height=info.height,
                fps=info.fps,
                codec=info.codec,
                has_audio=info.has_audio,
                ingestion_status=status,
                resolution_method=result.method,
                resolution_confidence=result.confidence,
                resolution_notes=result.notes,
            )
            session.add(source)
            session.flush()
            log.info(
                f"ingested: {path.name} → game='{result.game_name}' "
                f"method={result.method} conf={result.confidence:.2f} status={status}"
            )
            return True

    def _record_error(self, session: Session, path: Path, fhash: str, error: str) -> None:
        source = GameplaySource(
            file_path=str(path),
            filename=path.name,
            file_hash=fhash,
            file_size=path.stat().st_size if path.exists() else 0,
            ingestion_status=IngestionStatus.error.value,
            resolution_notes=error,
        )
        session.add(source)
        session.flush()

    def _ollama_available(self) -> bool:
        """Quick check if Ollama is reachable (for VLM L3)."""
        import httpx

        try:
            httpx.get(f"{self.settings.ollama_host}/api/tags", timeout=3)
            return True
        except Exception:
            return False
