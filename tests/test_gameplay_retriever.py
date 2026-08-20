"""Tests for the GameplayRetriever — semantic clip selection.

Tests:
  - Semantic retrieval when plan has gameplay_query and index is ready
  - Fallback to random when no plan is provided
  - Fallback to random when plan strategy is background_filler
  - Fallback to random when no analyzed events exist
  - Compatibility filtering (game_related / general_topic)
  - Supplementing with random when semantic clips don't fill target
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from gpcg.application.gameplay_retriever import GameplayRetriever
from gpcg.domain.creative_plan import (
    VIDEO_TYPE_GAME_RELATED,
    VIDEO_TYPE_GENERAL_TOPIC,
    HumorPlan,
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
    db_path = tmp_path / "test_retriever.db"
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
def game_with_events(fresh_db):
    """Create a game with a gameplay source, asset, and analyzed events."""
    with session_scope() as session:
        game = Game(canonical_name="TestGame")
        session.add(game)
        session.flush()

        source = GameplaySource(
            game_id=game.id,
            file_path="/tmp/test_gameplay.mp4",
            filename="test_gameplay.mp4",
            file_hash="hash123",
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

        # Mark as analysis-ready with compatibility
        from gpcg.application.gameplay_index_service import GameplayIndexService
        svc = GameplayIndexService()
        svc.set_analysis_status(session, source.id, AnalysisStatus.ready.value, config_hash="abc")
        svc.set_compatibility(session, source.id, game_related=True, general_topic=True)

        # Create an asset for the source
        asset = GameplayAsset(
            source_id=source.id,
            label="clip1",
            start_sec=0.0,
            end_sec=120.0,
            duration=120.0,
            used_count=0,
        )
        session.add(asset)
        session.flush()

        # Create some gameplay events
        events = [
            GameplayEvent(
                source_id=source.id,
                start_time=10.0, end_time=20.0,
                event_type="COMBAT",
                description="Player fighting enemies in a dungeon",
                interesting_score=0.9,
                visual_confidence=0.85,
                analysis_version="v1",
            ),
            GameplayEvent(
                source_id=source.id,
                start_time=30.0, end_time=45.0,
                event_type="CHASE",
                description="Player being chased by a dragon",
                interesting_score=0.85,
                visual_confidence=0.8,
                analysis_version="v1",
            ),
            GameplayEvent(
                source_id=source.id,
                start_time=60.0, end_time=75.0,
                event_type="EXPLORATION",
                description="Player walking through a peaceful village",
                interesting_score=0.3,
                visual_confidence=0.7,
                analysis_version="v1",
            ),
        ]
        for ev in events:
            session.add(ev)
        session.flush()

        return {"game_id": game.id, "source_id": source.id, "asset_id": asset.id}


class TestGameplayRetriever:
    """GameplayRetriever semantic and fallback tests."""

    def test_semantic_retrieval_with_query(self, game_with_events):
        """When a plan with gameplay_query is provided, retrieve matching events."""
        retriever = GameplayRetriever()
        plan = VideoCreativePlan(
            video_type=VIDEO_TYPE_GAME_RELATED,
            gameplay_strategy="related",
            gameplay_query="fighting enemies",
            success=True,
        )

        with session_scope() as session:
            clips = retriever.retrieve(
                session, game_with_events["game_id"],
                target_duration=15.0,
                creative_plan=plan,
                video_type="GAME_RELATED",
            )

        assert len(clips) > 0
        # The COMBAT event (10-20s) should be selected as it matches "fighting"
        assert any(c.start_sec == 10.0 for c in clips)

    def test_semantic_retrieval_no_query_uses_interesting(self, game_with_events):
        """When plan has no query, get the most interesting events."""
        retriever = GameplayRetriever()
        plan = VideoCreativePlan(
            video_type=VIDEO_TYPE_GAME_RELATED,
            gameplay_strategy="related",
            gameplay_query="",  # no query
            success=True,
        )

        with session_scope() as session:
            clips = retriever.retrieve(
                session, game_with_events["game_id"],
                target_duration=15.0,
                creative_plan=plan,
                video_type="GAME_RELATED",
            )

        assert len(clips) > 0
        # V2: top events are shuffled for variety, so any of the top-scored
        # events (COMBAT 0.9, CHASE 0.85, or EXPLORATION 0.3) may come first.
        # But at least one of the two most interesting should be included.
        clip_starts = [c.start_sec for c in clips]
        assert 10.0 in clip_starts or 30.0 in clip_starts  # COMBAT or CHASE included

    def test_fallback_when_no_plan(self, game_with_events):
        """When no plan is provided, fall back to random selection."""
        retriever = GameplayRetriever()

        with session_scope() as session:
            clips = retriever.retrieve(
                session, game_with_events["game_id"],
                target_duration=10.0,
                creative_plan=None,
                video_type="GAME_RELATED",
            )

        # Should still get clips (from random fallback)
        assert len(clips) > 0

    def test_fallback_when_background_filler_strategy(self, game_with_events):
        """When plan strategy is background_filler, use random selection."""
        retriever = GameplayRetriever()
        plan = VideoCreativePlan(
            video_type=VIDEO_TYPE_GENERAL_TOPIC,
            gameplay_strategy="background_filler",
            gameplay_query="",
            success=True,
        )

        with session_scope() as session:
            clips = retriever.retrieve(
                session, game_with_events["game_id"],
                target_duration=10.0,
                creative_plan=plan,
                video_type="GENERAL_TOPIC",
            )

        # Should get clips from random fallback (not semantic)
        assert len(clips) > 0

    def test_fallback_when_no_analyzed_events(self, fresh_db):
        """When no source has analysis ready, fall back to random."""
        with session_scope() as session:
            game = Game(canonical_name="NoAnalysis")
            session.add(game)
            session.flush()

            source = GameplaySource(
                game_id=game.id,
                file_path="/tmp/no_analysis.mp4",
                filename="no_analysis.mp4",
                file_hash="hash456",
                file_size=0,
                duration=60.0,
                width=1920, height=1080, fps=30, has_audio=True,
                ingestion_status="ready",
                # No analysis status set → not ready
            )
            session.add(source)
            session.flush()

            asset = GameplayAsset(
                source_id=source.id, label="clip1",
                start_sec=0.0, end_sec=60.0, duration=60.0, used_count=0,
            )
            session.add(asset)
            session.flush()
            game_id = game.id

        retriever = GameplayRetriever()
        plan = VideoCreativePlan(
            video_type=VIDEO_TYPE_GAME_RELATED,
            gameplay_strategy="related",
            gameplay_query="anything",
            success=True,
        )

        with session_scope() as session:
            clips = retriever.retrieve(
                session, game_id, target_duration=10.0,
                creative_plan=plan, video_type="GAME_RELATED",
            )

        # Should fall back to random (asset exists)
        assert len(clips) > 0

    def test_compatibility_filter_game_related(self, fresh_db):
        """Source with game_related=False should be excluded for GAME_RELATED videos."""
        with session_scope() as session:
            game = Game(canonical_name="CompatTest")
            session.add(game)
            session.flush()

            source = GameplaySource(
                game_id=game.id,
                file_path="/tmp/compat.mp4",
                filename="compat.mp4",
                file_hash="hash789",
                file_size=0,
                duration=60.0,
                width=1920, height=1080, fps=30, has_audio=True,
                ingestion_status="ready",
            )
            session.add(source)
            session.flush()

            from gpcg.application.gameplay_index_service import GameplayIndexService
            svc = GameplayIndexService()
            svc.set_analysis_status(session, source.id, AnalysisStatus.ready.value, config_hash="abc")
            # Set as NOT game_related (only for general topic)
            svc.set_compatibility(session, source.id, game_related=False, general_topic=True)

            asset = GameplayAsset(
                source_id=source.id, label="clip1",
                start_sec=0.0, end_sec=60.0, duration=60.0, used_count=0,
            )
            session.add(asset)
            session.flush()

            # Add an event
            ev = GameplayEvent(
                source_id=source.id,
                start_time=5.0, end_time=15.0,
                event_type="COMBAT",
                description="fight",
                interesting_score=0.8,
                visual_confidence=0.8,
                analysis_version="v1",
            )
            session.add(ev)
            session.flush()
            game_id = game.id

        retriever = GameplayRetriever()
        plan = VideoCreativePlan(
            video_type=VIDEO_TYPE_GAME_RELATED,
            gameplay_strategy="related",
            gameplay_query="fight",
            success=True,
        )

        with session_scope() as session:
            clips = retriever.retrieve(
                session, game_id, target_duration=10.0,
                creative_plan=plan, video_type="GAME_RELATED",
            )

        # Should fall back to random (the source is not game_related compatible)
        # But the asset still exists, so random fallback should work
        assert len(clips) > 0
        # The semantic event at 5.0s should NOT be selected (compatibility filtered)
        assert not any(c.start_sec == 5.0 for c in clips)

    def test_supplement_with_random_when_semantic_insufficient(self, game_with_events):
        """When semantic clips don't fill target, supplement with random."""
        retriever = GameplayRetriever()
        plan = VideoCreativePlan(
            video_type=VIDEO_TYPE_GAME_RELATED,
            gameplay_strategy="related",
            gameplay_query="fighting",
            success=True,
        )

        with session_scope() as session:
            clips = retriever.retrieve(
                session, game_with_events["game_id"],
                target_duration=90.0,  # much more than available events
                creative_plan=plan,
                video_type="GAME_RELATED",
            )

        # Should have clips (semantic + supplement)
        assert len(clips) > 0
        total = sum(c.duration for c in clips)
        assert total >= 80.0  # close to target
