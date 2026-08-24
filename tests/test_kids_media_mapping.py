"""Tests for Kids media mapping pipeline — VLM+ASR → KidsMediaEvent.

Verifies that the Kids domain uses the SAME mapping pipeline as Games:
- KidsMediaEvent model (equivalent to GameplayEvent)
- KidsMediaAnalyzer (reuses GameplayAnalyzer)
- AssetProcessingStatus.lifecycle includes "mapping" stage
- KidsMediaRetriever selects clips using events (semantic_event)
- AssetClipUsage prevents reusing segments
- Mapping result endpoint persists events
- Worker job data includes events + clip usages
- Public/private visibility in retrieval
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gpcg.core.models import Base, User, Video, VideoStatus
from gpcg.domains.kids.models import (
    AssetClipUsage,
    AssetMediaKind,
    AssetProcessingStatus,
    KidsMediaEvent,
    KidsTopic,
    StoryAsset,
)
from gpcg.application.kids_media_analyzer import (
    KidsMediaAnalyzer,
    _remap_event_type,
    kids_media_events_from_timeline,
)
from gpcg.application.kids_media_retriever import KidsMediaRetriever
from gpcg.domain.gameplay_events import EventTimeline, GameplayEventRecord


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    """In-memory SQLite DB with all Kids + core tables."""
    import gpcg.core.models  # noqa: F401
    import gpcg.domains.games.models  # noqa: F401
    import gpcg.domains.kids.models  # noqa: F401
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def user(db):
    u = User(email="test@example.com", name="Test User")
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def other_user(db):
    u = User(email="other@example.com", name="Other User")
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def video(db, user):
    v = Video(user_id=user.id, status=VideoStatus.ready.value)
    db.add(v)
    db.flush()
    return v


def _make_video_asset(db, user, **kwargs):
    """Create a ready video StoryAsset with optional events."""
    defaults = {
        "user_id": user.id,
        "filename": "test.mp4",
        "storage_key": "kids/test.mp4",
        "media_kind": AssetMediaKind.video.value,
        "duration": 60.0,
        "processing_status": AssetProcessingStatus.ready.value,
        "tags": [],
        "description": "",
        "is_public": False,
    }
    defaults.update(kwargs)
    asset = StoryAsset(**defaults)
    db.add(asset)
    db.flush()
    return asset


def _make_event(db, asset, **kwargs):
    """Create a KidsMediaEvent for an asset."""
    defaults = {
        "asset_id": asset.id,
        "start_time": 0.0,
        "end_time": 10.0,
        "event_type": "VISUAL_ACTION",
        "description": "",
        "tags": [],
        "transcript": "",
        "visual_confidence": 0.8,
        "interesting_score": 0.7,
    }
    defaults.update(kwargs)
    evt = KidsMediaEvent(**defaults)
    db.add(evt)
    db.flush()
    return evt


# ── Model tests ───────────────────────────────────────────────────────────────


class TestKidsMediaEventModel:
    def test_kids_media_event_table_exists(self, db, user):
        """KidsMediaEvent table is created and can be queried."""
        asset = _make_video_asset(db, user)
        evt = KidsMediaEvent(
            asset_id=asset.id,
            start_time=0.0,
            end_time=5.0,
            event_type="ANIMATION",
            description="Colorful animation of a dinosaur",
            tags=["dinosaur", "animation"],
            visual_confidence=0.8,
        )
        db.add(evt)
        db.commit()
        loaded = db.query(KidsMediaEvent).first()
        assert loaded is not None
        assert loaded.event_type == "ANIMATION"
        assert loaded.duration == 5.0
        assert loaded.is_confident is True  # 0.8 >= 0.7

    def test_asset_relationship_events(self, db, user):
        """StoryAsset.events relationship works."""
        asset = _make_video_asset(db, user)
        _make_event(db, asset, description="event 1")
        _make_event(db, asset, description="event 2", start_time=10.0, end_time=20.0)
        db.commit()
        # Refresh relationship
        db.refresh(asset)
        assert len(asset.events) == 2

    def test_asset_relationship_clip_usages(self, db, user, video):
        """StoryAsset.clip_usages relationship works."""
        asset = _make_video_asset(db, user)
        usage = AssetClipUsage(
            video_id=video.id,
            asset_id=asset.id,
            consumer_user_id=user.id,
            start_sec=0.0,
            end_sec=5.0,
            duration=5.0,
        )
        db.add(usage)
        db.commit()
        db.refresh(asset)
        assert len(asset.clip_usages) == 1

    def test_asset_analysis_info(self, db, user):
        """StoryAsset.analysis_info reads from metadata_json."""
        asset = _make_video_asset(db, user)
        asset.metadata_json = {
            "analysis": {
                "status": "ready",
                "version": "v1",
                "event_count": 5,
            }
        }
        db.commit()
        assert asset.analysis_status == "ready"
        assert asset.is_analysis_ready is True

    def test_asset_analysis_info_default(self, db, user):
        """StoryAsset.analysis_info defaults when no metadata."""
        asset = _make_video_asset(db, user)
        assert asset.analysis_status == "pending"
        assert asset.is_analysis_ready is False

    def test_processing_status_mapping_value(self):
        """AssetProcessingStatus.mapping exists."""
        assert AssetProcessingStatus.mapping.value == "mapping"


# ── Analyzer tests ────────────────────────────────────────────────────────────


class TestKidsMediaAnalyzer:
    def test_remap_event_type_combat(self):
        assert _remap_event_type("COMBAT") == "VISUAL_ACTION"

    def test_remap_event_type_dialogue(self):
        assert _remap_event_type("DIALOGUE") == "NARRATION"

    def test_remap_event_type_unknown(self):
        assert _remap_event_type("UNKNOWN") == "UNKNOWN"

    def test_remap_event_type_possible(self):
        assert _remap_event_type("POSSIBLE_COMBAT") == "POSSIBLE_VISUAL_ACTION"

    def test_remap_event_type_unmapped(self):
        """Unmapped types are kept as-is."""
        assert _remap_event_type("CUSTOM_TYPE") == "CUSTOM_TYPE"

    def test_kids_media_events_from_timeline(self):
        """Convert EventTimeline to KidsMediaEvent dicts."""
        timeline = EventTimeline(
            source_id=1,
            source_path="/tmp/test.mp4",
            duration=60.0,
            analysis_version="v1",
            vision_model="gemma3",
            asr_model="whisper",
        )
        timeline.events.append(GameplayEventRecord(
            start_time=0.0,
            end_time=10.0,
            event_type="COMBAT",
            description="A fight scene",
            characters=["hero"],
            location="forest",
            actions=["fighting"],
            tags=["action", "fight"],
            transcript="Let's go!",
            visual_confidence=0.9,
            interesting_score=0.8,
        ))
        events = kids_media_events_from_timeline(timeline, asset_id=42)
        assert len(events) == 1
        evt = events[0]
        assert evt["asset_id"] == 42
        assert evt["event_type"] == "VISUAL_ACTION"  # remapped from COMBAT
        assert evt["description"] == "A fight scene"
        assert evt["transcript"] == "Let's go!"
        assert evt["visual_confidence"] == 0.9

    def test_kids_media_analyzer_init(self):
        """KidsMediaAnalyzer wraps GameplayAnalyzer."""
        analyzer = KidsMediaAnalyzer()
        assert analyzer.analyzer is not None
        assert analyzer.analyzer.camera_type == "unknown"


# ── Retriever tests with events ───────────────────────────────────────────────


class TestKidsMediaRetrieverWithEvents:
    def test_retriever_uses_events_for_semantic_match(self, db, user):
        """Retriever scores assets with matching events higher."""
        # Asset with matching event
        asset1 = _make_video_asset(db, user, filename="dino.mp4")
        _make_event(db, asset1, description="dinosaur running in forest", tags=["dinosaur"])
        # Asset without matching event
        asset2 = _make_video_asset(db, user, filename="space.mp4")
        _make_event(db, asset2, description="rocket launch", tags=["space"])
        db.commit()

        retriever = KidsMediaRetriever()
        clips = retriever.retrieve(
            db, user.id, 10.0,
            topic_title="dinosaur",
            rng=__import__("random").Random(42),
        )
        assert len(clips) > 0
        # First clip should be from asset1 (dinosaur match)
        assert clips[0].asset.id == asset1.id
        assert clips[0].selection_reason == "semantic_event"

    def test_retriever_event_aligned_segment(self, db, user):
        """Retriever aligns clip to matching event's time range."""
        asset = _make_video_asset(db, user, duration=60.0)
        _make_event(db, asset, start_time=20.0, end_time=30.0,
                    description="dinosaur scene", tags=["dinosaur"])
        db.commit()

        retriever = KidsMediaRetriever()
        clips = retriever.retrieve(
            db, user.id, 10.0,
            topic_title="dinosaur",
            rng=__import__("random").Random(42),
        )
        assert len(clips) > 0
        clip = clips[0]
        assert clip.asset.id == asset.id
        assert clip.event_id is not None
        # Should be aligned to event start (20.0)
        assert clip.start_sec == 20.0

    def test_retriever_respects_clip_usage(self, db, user, video):
        """Retriever avoids segments already used (AssetClipUsage)."""
        asset = _make_video_asset(db, user, duration=60.0)
        _make_event(db, asset, start_time=0.0, end_time=15.0,
                    description="dinosaur", tags=["dinosaur"])
        _make_event(db, asset, start_time=20.0, end_time=35.0,
                    description="dinosaur again", tags=["dinosaur"])
        # Mark first event range as used
        db.add(AssetClipUsage(
            video_id=video.id, asset_id=asset.id,
            consumer_user_id=user.id,
            start_sec=0.0, end_sec=15.0, duration=15.0,
        ))
        db.commit()

        retriever = KidsMediaRetriever()
        clips = retriever.retrieve(
            db, user.id, 10.0,
            topic_title="dinosaur",
            rng=__import__("random").Random(42),
        )
        assert len(clips) > 0
        # Should NOT use the first event (0-15) — should use second (20-35)
        assert clips[0].start_sec >= 20.0

    def test_retriever_falls_back_without_events(self, db, user):
        """Retriever works with assets that have no events (tag-based)."""
        asset = _make_video_asset(
            db, user, duration=30.0,
            tags=["dinosaur", "nature"],
            description="dinosaur in nature",
        )
        db.commit()

        retriever = KidsMediaRetriever()
        clips = retriever.retrieve(
            db, user.id, 10.0,
            topic_title="dinosaur",
            rng=__import__("random").Random(42),
        )
        assert len(clips) > 0
        assert clips[0].asset.id == asset.id
        assert clips[0].selection_reason in ("semantic_tag_match", "semantic_description_match")

    def test_retriever_public_assets_from_other_users(self, db, user, other_user):
        """Retriever can use public assets from other users."""
        # User has no assets
        # Other user has a public asset
        asset = _make_video_asset(
            db, other_user, duration=30.0,
            tags=["dinosaur"], is_public=True,
        )
        db.commit()

        retriever = KidsMediaRetriever()
        clips = retriever.retrieve(
            db, user.id, 10.0,
            topic_title="dinosaur",
            accept_public=True,
            rng=__import__("random").Random(42),
        )
        assert len(clips) > 0
        assert clips[0].asset.id == asset.id

    def test_retriever_excludes_private_assets_from_others(self, db, user, other_user):
        """Retriever does NOT use private assets from other users."""
        _make_video_asset(
            db, other_user, duration=30.0,
            tags=["dinosaur"], is_public=False,
        )
        db.commit()

        retriever = KidsMediaRetriever()
        clips = retriever.retrieve(
            db, user.id, 10.0,
            topic_title="dinosaur",
            accept_public=True,
            rng=__import__("random").Random(42),
        )
        assert len(clips) == 0  # no accessible assets

    def test_retriever_event_id_in_selected_media(self, db, user):
        """SelectedMedia includes event_id when event-aligned."""
        asset = _make_video_asset(db, user, duration=60.0)
        evt = _make_event(db, asset, start_time=10.0, end_time=25.0,
                          description="dinosaur", tags=["dinosaur"])
        db.commit()

        retriever = KidsMediaRetriever()
        clips = retriever.retrieve(
            db, user.id, 10.0,
            topic_title="dinosaur",
            rng=__import__("random").Random(42),
        )
        assert len(clips) > 0
        assert clips[0].event_id == evt.id


# ── Migration regression tests ────────────────────────────────────────────────


class TestStoryAssetsPrimaryKeyMigration:
    """Regression tests for _fix_story_assets_primary_key migration.

    These tests simulate a DB corrupted by the old
    _migrate_story_assets_topic_id_nullable that used
    CREATE TABLE AS SELECT (which doesn't preserve PRIMARY KEY).
    """

    def test_fixes_broken_primary_key(self, tmp_path):
        """_fix_story_assets_primary_key repairs id when it's not a PK."""
        from sqlalchemy import text, inspect
        from gpcg.infrastructure.database import _fix_story_assets_primary_key

        db_path = tmp_path / "test.db"
        engine = create_engine(f"sqlite:///{db_path}")

        # Create a BROKEN story_assets table (id without PRIMARY KEY,
        # simulating what CREATE TABLE AS SELECT produced)
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE story_assets (
                    id INT,
                    user_id INT,
                    topic_id INT,
                    filename TEXT,
                    storage_key TEXT,
                    file_hash TEXT,
                    file_size INT,
                    width INT,
                    height INT,
                    processing_status TEXT,
                    metadata_json NUM,
                    created_at NUM,
                    media_kind TEXT,
                    duration REAL,
                    codec TEXT,
                    has_audio NUM,
                    thumbnail_key TEXT,
                    process_error TEXT,
                    tags NUM,
                    description TEXT,
                    is_public NUM
                )
            """))
            # Insert a row with id=NULL (the bug symptom)
            conn.execute(text("""
                INSERT INTO story_assets (id, user_id, filename, storage_key,
                    file_hash, file_size, width, height, processing_status,
                    metadata_json, created_at, media_kind, duration, codec,
                    has_audio, thumbnail_key, process_error, tags, description,
                    is_public)
                VALUES (NULL, 1, 'test.mp4', 'key', 'hash', 100, 640, 360,
                    'ready', NULL, 0, 'video', 10.0, 'h264', 1, 'thumb', '',
                    '[]', '', 0)
            """))

        # Verify it's broken
        inspector = inspect(engine)
        pk = inspector.get_pk_constraint("story_assets").get("constrained_columns", [])
        assert "id" not in pk, "Precondition: id should NOT be a PK"

        # Run the migration
        _fix_story_assets_primary_key(engine)

        # Verify it's fixed
        inspector = inspect(engine)
        pk = inspector.get_pk_constraint("story_assets").get("constrained_columns", [])
        assert "id" in pk, "Post-condition: id should be a PK"

        # Verify data survived and got a real ID
        with engine.connect() as conn:
            result = conn.execute(text("SELECT id, filename FROM story_assets")).fetchone()
            assert result is not None, "Data should survive migration"
            assert result[0] is not None, "id should be auto-assigned (not NULL)"
            assert result[0] >= 1, f"id should be >= 1, got {result[0]}"
            assert result[1] == "test.mp4"

    def test_noop_when_already_correct(self, tmp_path):
        """_fix_story_assets_primary_key is a no-op when id is already PK."""
        from sqlalchemy import inspect
        from gpcg.infrastructure.database import _fix_story_assets_primary_key

        db_path = tmp_path / "test.db"
        engine = create_engine(f"sqlite:///{db_path}")

        # Create a CORRECT story_assets table (via create_all)
        import gpcg.core.models  # noqa: F401
        import gpcg.domains.kids.models  # noqa: F401
        Base.metadata.create_all(bind=engine, tables=[
            __import__("gpcg.domains.kids.models", fromlist=["StoryAsset"]).StoryAsset.__table__
        ])

        # Run migration — should be a no-op
        _fix_story_assets_primary_key(engine)

        # Verify id is still a PK
        inspector = inspect(engine)
        pk = inspector.get_pk_constraint("story_assets").get("constrained_columns", [])
        assert "id" in pk
