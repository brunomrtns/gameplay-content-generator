"""Tests for V5: gameplay_source_id fallback when the chosen source is exhausted.

Scenario:
  - User adds idea to queue with gameplay_source_id=X (specific source)
  - Jobs processed earlier consume all eligible clips from source X
  - When this job reaches gameplay_selection, source X has no eligible clips
  - V5 fallback: retriever retries without source restriction, finds clips
    from another source of the same game (preserving gameplay_preference)

Tests:
  - Fallback when specific source is exhausted (all clips used max_uses times)
  - Fallback when specific source doesn't exist
  - No fallback when specific source still has clips
  - Fallback preserves gameplay_preference (stays within chosen game)
"""

from __future__ import annotations

import random

import pytest

from gpcg.application.clip_usage_service import record_clip_usage
from gpcg.application.gameplay_retriever import GameplayRetriever
from gpcg.domain.creative_plan import (
    VIDEO_TYPE_GAME_RELATED,
    VideoCreativePlan,
)
from gpcg.domains.games.models import (
    AnalysisStatus,
    Game,
    GameplayAsset,
    GameplayEvent,
    GameplaySource,
)
from gpcg.infrastructure.database import init_db, session_scope


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Fresh SQLite DB with schema for each test."""
    db_path = tmp_path / "test_source_fallback.db"
    monkeypatch.setenv("GPCG_DB_PATH", str(db_path))
    from gpcg.config import get_settings
    get_settings.cache_clear()
    import gpcg.infrastructure.database as db_module
    db_module._engine = None
    db_module._SessionLocal = None
    init_db()
    yield db_path
    db_module._engine = None
    db_module._SessionLocal = None
    get_settings.cache_clear()


@pytest.fixture
def game_with_two_sources(fresh_db):
    """Create a game with TWO gameplay sources, both with analyzed events.

    Source A: will be the "chosen" source that gets exhausted
    Source B: will be the "fallback" source with available clips
    """
    from gpcg.application.gameplay_index_service import GameplayIndexService

    with session_scope() as session:
        game = Game(canonical_name="FallbackTestGame")
        session.add(game)
        session.flush()

        sources = {}
        for label, filename in [("A", "source_a.mp4"), ("B", "source_b.mp4")]:
            source = GameplaySource(
                game_id=game.id,
                user_id=1,  # owned by user 1
                file_path=f"/tmp/{filename}",
                filename=filename,
                file_hash=f"hash_{label}",
                file_size=0,
                duration=120.0,
                width=1920,
                height=1080,
                fps=30,
                has_audio=True,
                ingestion_status="ready",
            )
            session.add(source)
            session.flush()

            svc = GameplayIndexService()
            svc.set_analysis_status(session, source.id, AnalysisStatus.ready.value, config_hash="abc")
            svc.set_compatibility(session, source.id, game_related=True, general_topic=True)

            asset = GameplayAsset(
                source_id=source.id,
                label=f"clip_{label}",
                start_sec=0.0,
                end_sec=120.0,
                duration=120.0,
                used_count=0,
            )
            session.add(asset)
            session.flush()

            # Add events
            ev = GameplayEvent(
                source_id=source.id,
                start_time=10.0, end_time=25.0,
                event_type="COMBAT",
                description=f"Player fighting in {label}",
                interesting_score=0.85,
                visual_confidence=0.8,
                analysis_version="v1",
            )
            session.add(ev)
            session.flush()

            sources[label] = source.id

        return {
            "game_id": game.id,
            "source_a": sources["A"],
            "source_b": sources["B"],
        }


class TestSourceFallback:
    """V5: Fallback when gameplay_source_id is exhausted."""

    def test_fallback_when_specific_source_exhausted(self, game_with_two_sources):
        """When the chosen source has all clips used, fall back to another source."""
        retriever = GameplayRetriever()
        plan = VideoCreativePlan(
            video_type=VIDEO_TYPE_GAME_RELATED,
            gameplay_strategy="related",
            gameplay_query="fighting",
            success=True,
        )

        # Exhaust source A completely by recording usage of the full 0-120s range
        with session_scope() as session:
            record_clip_usage(
                session,
                video_id=999,  # fake video
                source_id=game_with_two_sources["source_a"],
                start_sec=0.0,
                end_sec=120.0,
                consumer_user_id=1,
            )

        # Now try to retrieve from source A specifically with max_uses=1
        with session_scope() as session:
            clips = retriever.retrieve(
                session, game_with_two_sources["game_id"],
                target_duration=10.0,
                creative_plan=plan,
                video_type="GAME_RELATED",
                user_id=1,
                max_uses=1,
                gameplay_preference_game_id=game_with_two_sources["game_id"],
                gameplay_source_id=game_with_two_sources["source_a"],
            )

        # V5: Should fall back to source B
        assert len(clips) > 0, "Expected fallback clips from source B"
        # All clips should be from source B (the fallback), not source A
        for clip in clips:
            assert clip.asset.source_id == game_with_two_sources["source_b"], \
                f"Expected source B ({game_with_two_sources['source_b']}), got {clip.asset.source_id}"

    def test_no_fallback_when_source_has_clips(self, game_with_two_sources):
        """When the chosen source still has eligible clips, use it (no fallback)."""
        retriever = GameplayRetriever()
        plan = VideoCreativePlan(
            video_type=VIDEO_TYPE_GAME_RELATED,
            gameplay_strategy="related",
            gameplay_query="fighting",
            success=True,
        )

        # Don't exhaust any source — both should have clips
        with session_scope() as session:
            clips = retriever.retrieve(
                session, game_with_two_sources["game_id"],
                target_duration=10.0,
                creative_plan=plan,
                video_type="GAME_RELATED",
                user_id=1,
                max_uses=1,
                gameplay_preference_game_id=game_with_two_sources["game_id"],
                gameplay_source_id=game_with_two_sources["source_a"],
            )

        assert len(clips) > 0
        # All clips should be from source A (the chosen one)
        for clip in clips:
            assert clip.asset.source_id == game_with_two_sources["source_a"]

    def test_fallback_when_specific_source_not_found(self, game_with_two_sources):
        """When the chosen source_id doesn't exist, fall back to any source."""
        retriever = GameplayRetriever()
        plan = VideoCreativePlan(
            video_type=VIDEO_TYPE_GAME_RELATED,
            gameplay_strategy="related",
            gameplay_query="fighting",
            success=True,
        )

        # Use a non-existent source_id
        with session_scope() as session:
            clips = retriever.retrieve(
                session, game_with_two_sources["game_id"],
                target_duration=10.0,
                creative_plan=plan,
                video_type="GAME_RELATED",
                user_id=1,
                max_uses=1,
                gameplay_preference_game_id=game_with_two_sources["game_id"],
                gameplay_source_id=99999,  # doesn't exist
            )

        # V5: Should fall back to any source of the game
        assert len(clips) > 0, "Expected fallback clips when source_id doesn't exist"
        valid_sources = {game_with_two_sources["source_a"], game_with_two_sources["source_b"]}
        for clip in clips:
            assert clip.asset.source_id in valid_sources

    def test_fallback_preserves_gameplay_preference(self, game_with_two_sources):
        """Fallback should stay within the chosen game (gameplay_preference)."""
        retriever = GameplayRetriever()
        plan = VideoCreativePlan(
            video_type=VIDEO_TYPE_GAME_RELATED,
            gameplay_strategy="related",
            gameplay_query="fighting",
            success=True,
        )

        # Exhaust source A completely
        with session_scope() as session:
            record_clip_usage(
                session,
                video_id=999,
                source_id=game_with_two_sources["source_a"],
                start_sec=0.0,
                end_sec=120.0,
                consumer_user_id=1,
            )

        # Retrieve with source A + gameplay_preference for the game
        with session_scope() as session:
            clips = retriever.retrieve(
                session, game_with_two_sources["game_id"],
                target_duration=10.0,
                creative_plan=plan,
                video_type="GAME_RELATED",
                user_id=1,
                max_uses=1,
                gameplay_preference_game_id=game_with_two_sources["game_id"],
                gameplay_source_id=game_with_two_sources["source_a"],
            )

        assert len(clips) > 0
        # All clips must be from the chosen game (source B is in the same game)
        for clip in clips:
            assert clip.asset.source_id == game_with_two_sources["source_b"]
