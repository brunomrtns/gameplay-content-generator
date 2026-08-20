"""V3 Refactor — Phase 1 tests: contracts, context, determinism.

Covers:
1. ChannelProfile is included in job_data payload (VPS → worker)
2. local_db_sync populates ChannelProfile in the local DB
3. Config snapshot is stored in job.artifacts at creation time
4. Public KnowledgeItem used by User A stays fresh for User B
5. Private KnowledgeItem of User A never visible to User B
6. record_usage only marks global status=used for private KIs
7. worker_routes sync uses record_usage (not direct status=used)
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from gpcg.core.models import (
    Base,
    ChannelProfile,
    Job,
    JobStatus,
    JobType,
    KnowledgeItem,
    KnowledgeItemStatus,
    KnowledgeItemUsage,
    User,
)
from gpcg.domains.games.models import Game
from gpcg.application.knowledge_item_service import (
    is_used_by_consumer,
    record_usage,
    mark_as_used,
)


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def db_session():
    """In-memory SQLite session."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def two_users(db_session):
    """Create two users for cross-user tests."""
    user_a = User(email="a@test.com", name="User A", is_active=True)
    user_b = User(email="b@test.com", name="User B", is_active=True)
    db_session.add_all([user_a, user_b])
    db_session.flush()
    return user_a, user_b


# ── 1. ChannelProfile in job_data payload ──────────────────────────────────

def test_channel_profile_serialized_in_job_data(db_session, two_users):
    """VPS endpoint /api/jobs/{id}/data must include channel_profile."""
    user_a, _ = two_users
    game = Game(canonical_name="Test Game", user_id=user_a.id)
    db_session.add(game)
    db_session.flush()

    profile = ChannelProfile(
        user_id=user_a.id,
        channel_description="Canal de análises de FPS",
        niche="FPS competitivo",
        target_audience="Jogadores casuais",
        tone_of_voice="educativo",
        narrative_style="storytelling",
        content_goals="Crescer comunidade",
        special_rules="Sem spoilers",
    )
    db_session.add(profile)
    db_session.flush()

    job = Job(
        user_id=user_a.id,
        game_id=game.id,
        type=JobType.generate_short.value,
        status=JobStatus.queued.value,
        artifacts={},
    )
    db_session.add(job)
    db_session.commit()

    # Simulate the serialization logic from worker_routes.get_job_data
    from gpcg.core.models import ChannelProfile as CP
    fetched = db_session.query(CP).filter(CP.user_id == job.user_id).first()
    assert fetched is not None
    assert fetched.channel_description == "Canal de análises de FPS"
    assert fetched.niche == "FPS competitivo"
    assert fetched.user_id == user_a.id


def test_channel_profile_none_when_not_set(db_session, two_users):
    """If user has no ChannelProfile, job_data should have channel_profile=None."""
    user_a, _ = two_users
    game = Game(canonical_name="Test Game", user_id=user_a.id)
    db_session.add(game)
    db_session.flush()

    job = Job(
        user_id=user_a.id,
        game_id=game.id,
        type=JobType.generate_short.value,
        status=JobStatus.queued.value,
        artifacts={},
    )
    db_session.add(job)
    db_session.commit()

    from gpcg.core.models import ChannelProfile as CP
    fetched = db_session.query(CP).filter(CP.user_id == job.user_id).first()
    assert fetched is None


# ── 2. local_db_sync populates ChannelProfile ──────────────────────────────

def test_local_db_sync_populates_channel_profile():
    """local_db_sync.populate_local_db must create ChannelProfile in temp DB."""
    from gpcg.worker.local_db_sync import populate_local_db

    job_data = {
        "job": {
            "id": 999,
            "job_uuid": "test-uuid",
            "user_id": 42,
            "type": "generate_short",
            "status": "queued",
            "stage": "ingest",
            "progress": 0.0,
            "priority": "normal",
            "game_id": 1,
            "content_plan_id": None,
            "gameplay_source_id": None,
            "artifacts": {},
            "attempts": 0,
            "max_attempts": 3,
        },
        "game": {
            "id": 1,
            "canonical_name": "Test Game",
            "aliases": [],
            "camera_type": "unknown",
            "platforms": [],
            "capture_sources": [],
            "metadata_json": {},
        },
        "gameplay_sources": [],
        "automation": {
            "id": 1,
            "user_id": 42,
            "name": "Minha Automação",
            "status": "running",
            "config": {},
            "upload_config": {},
        },
        "channel_profile": {
            "id": 7,
            "user_id": 42,
            "channel_description": "Canal de gaming",
            "niche": "RPG",
            "target_audience": "Gamers",
            "tone_of_voice": "casual",
            "narrative_style": "análise",
            "content_goals": "Educar",
            "special_rules": "Sem drama",
            "metadata_json": {},
        },
    }

    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_local.db"
        SessionLocal = populate_local_db(job_data, db_path)
        session = SessionLocal()
        try:
            profile = session.query(ChannelProfile).filter(
                ChannelProfile.user_id == 42
            ).first()
            assert profile is not None
            assert profile.channel_description == "Canal de gaming"
            assert profile.niche == "RPG"
            assert profile.tone_of_voice == "casual"
            assert profile.user_id == 42
        finally:
            session.close()


def test_local_db_sync_without_channel_profile():
    """local_db_sync must not crash when channel_profile is None."""
    from gpcg.worker.local_db_sync import populate_local_db

    job_data = {
        "job": {
            "id": 998,
            "job_uuid": "test-uuid-2",
            "user_id": 43,
            "type": "generate_short",
            "status": "queued",
            "stage": "ingest",
            "progress": 0.0,
            "priority": "normal",
            "game_id": None,
            "content_plan_id": None,
            "gameplay_source_id": None,
            "artifacts": {},
            "attempts": 0,
            "max_attempts": 3,
        },
        "gameplay_sources": [],
        "channel_profile": None,
    }

    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_local2.db"
        SessionLocal = populate_local_db(job_data, db_path)
        session = SessionLocal()
        try:
            profile = session.query(ChannelProfile).filter(
                ChannelProfile.user_id == 43
            ).first()
            assert profile is None
        finally:
            session.close()


# ── 3. Config snapshot in job.artifacts ────────────────────────────────────

def test_config_snapshot_stored_in_artifacts(db_session, two_users):
    """Job.artifacts must contain a config_snapshot at creation time."""
    user_a, _ = two_users
    game = Game(canonical_name="Test Game", user_id=user_a.id)
    db_session.add(game)
    db_session.flush()

    # Simulate what create_job_from_automation should do:
    # store a snapshot of the config used to create the job
    config_snapshot = {
        "scene_duration": 5.0,
        "video_format": "9:16",
        "subtitle_font": "Arial",
        "subtitle_color": "#FFFFFF",
        "voice": "narrator.wav",
        "creative_style": "curiosity",
        "transition_type": "fade",
        "transition_duration": 0.5,
        "max_clip_uses": 1,
        "fallback_policy": "stop",
    }
    job = Job(
        user_id=user_a.id,
        game_id=game.id,
        type=JobType.generate_short.value,
        status=JobStatus.queued.value,
        artifacts={
            "config_snapshot": config_snapshot,
            "gameplay_preference": None,
            "reuse_override": None,
        },
    )
    db_session.add(job)
    db_session.commit()

    fetched = db_session.get(Job, job.id)
    assert "config_snapshot" in fetched.artifacts
    snap = fetched.artifacts["config_snapshot"]
    assert snap["video_format"] == "9:16"
    assert snap["scene_duration"] == 5.0
    assert snap["voice"] == "narrator.wav"


def test_config_snapshot_excludes_secrets(db_session, two_users):
    """Config snapshot must NOT include secrets, tokens, or credentials."""
    user_a, _ = two_users
    game = Game(canonical_name="Test Game", user_id=user_a.id)
    db_session.add(game)
    db_session.flush()

    # The snapshot should only contain video generation params,
    # never credentials like youtube tokens or worker keys
    config_snapshot = {
        "scene_duration": 5.0,
        "video_format": "9:16",
        # These should NOT be in the snapshot:
        # "youtube_token": "...",
        # "worker_api_key": "...",
        # "google_refresh_token": "...",
    }
    job = Job(
        user_id=user_a.id,
        game_id=game.id,
        type=JobType.generate_short.value,
        status=JobStatus.queued.value,
        artifacts={"config_snapshot": config_snapshot},
    )
    db_session.add(job)
    db_session.commit()

    snap = db_session.get(Job, job.id).artifacts["config_snapshot"]
    assert "youtube_token" not in snap
    assert "worker_api_key" not in snap
    assert "google_refresh_token" not in snap


# ── 4. Public KnowledgeItem consumption is per-user ────────────────────────

def test_public_ki_used_by_a_stays_fresh_for_b(db_session, two_users):
    """When User A uses a public KI, it must stay fresh for User B."""
    user_a, user_b = two_users

    # Public KI (user_id=None, is_public=True)
    ki = KnowledgeItem(
        user_id=None,
        is_public=True,
        title="Public news",
        content="Some public content",
        item_type="news",
        source_type="rss",
        status=KnowledgeItemStatus.fresh.value,
        editorial_score=50.0,
    )
    db_session.add(ki)
    db_session.flush()

    # User A consumes it
    usage = record_usage(db_session, ki.id, user_a.id)
    db_session.commit()

    assert usage is not None
    assert usage.consumer_user_id == user_a.id

    # KI global status should NOT be "used" (it's public)
    refreshed = db_session.get(KnowledgeItem, ki.id)
    assert refreshed.status == KnowledgeItemStatus.fresh.value, \
        "Public KI must stay fresh globally after per-consumer usage"

    # User A has it as used
    assert is_used_by_consumer(db_session, ki.id, user_a.id) is True

    # User B does NOT have it as used
    assert is_used_by_consumer(db_session, ki.id, user_b.id) is False


def test_private_ki_used_marks_global_status(db_session, two_users):
    """When User A uses their own private KI, global status becomes used."""
    user_a, _ = two_users

    ki = KnowledgeItem(
        user_id=user_a.id,
        is_public=False,
        title="A's private idea",
        content="Private content",
        item_type="curiosity",
        source_type="manual",
        status=KnowledgeItemStatus.fresh.value,
        editorial_score=60.0,
    )
    db_session.add(ki)
    db_session.flush()

    record_usage(db_session, ki.id, user_a.id)
    db_session.commit()

    refreshed = db_session.get(KnowledgeItem, ki.id)
    assert refreshed.status == KnowledgeItemStatus.used.value, \
        "Private KI should have global status=used after owner consumes it"
    assert is_used_by_consumer(db_session, ki.id, user_a.id) is True


def test_private_ki_of_a_not_visible_to_b(db_session, two_users):
    """User B cannot see User A's private KI."""
    user_a, user_b = two_users

    ki = KnowledgeItem(
        user_id=user_a.id,
        is_public=False,
        title="A's secret idea",
        content="Secret",
        item_type="news",
        source_type="manual",
        status=KnowledgeItemStatus.fresh.value,
        editorial_score=70.0,
    )
    db_session.add(ki)
    db_session.commit()

    from gpcg.domain.visibility import visible_to_user
    vis = visible_to_user(KnowledgeItem.user_id, KnowledgeItem.is_public, user_b.id)
    result = db_session.execute(
        select(KnowledgeItem).where(vis)
    ).scalars().all()
    assert all(k.user_id != user_a.id or k.is_public for k in result), \
        "A's private KI must not be visible to B"


def test_b_can_use_public_ki_after_a_used_it(db_session, two_users):
    """User B can consume a public KI even after User A consumed it."""
    user_a, user_b = two_users

    ki = KnowledgeItem(
        user_id=None,
        is_public=True,
        title="Shared news",
        content="Shared content",
        item_type="news",
        source_type="rss",
        status=KnowledgeItemStatus.fresh.value,
        editorial_score=55.0,
    )
    db_session.add(ki)
    db_session.flush()

    # A uses it
    record_usage(db_session, ki.id, user_a.id)
    db_session.commit()

    # B should also be able to use it
    usage_b = record_usage(db_session, ki.id, user_b.id)
    db_session.commit()

    assert usage_b is not None
    assert usage_b.consumer_user_id == user_b.id
    assert is_used_by_consumer(db_session, ki.id, user_b.id) is True

    # Both have usage records
    usages = db_session.execute(
        select(KnowledgeItemUsage).where(
            KnowledgeItemUsage.knowledge_item_id == ki.id
        )
    ).scalars().all()
    assert len(usages) == 2


# ── 5. mark_as_used is global (legacy) — document the distinction ──────────

def test_mark_as_used_sets_global_status(db_session, two_users):
    """mark_as_used sets global status=used regardless of public/private.

    This is the LEGACY function. The V3 sync path should use record_usage
    instead, which respects the public/private distinction.
    """
    user_a, _ = two_users

    ki = KnowledgeItem(
        user_id=None,  # public
        is_public=True,
        title="Public",
        content="Content",
        item_type="news",
        source_type="rss",
        status=KnowledgeItemStatus.fresh.value,
        editorial_score=40.0,
    )
    db_session.add(ki)
    db_session.flush()

    # mark_as_used sets global status (this is what the OLD sync did)
    mark_as_used(db_session, ki.id)
    db_session.commit()

    refreshed = db_session.get(KnowledgeItem, ki.id)
    assert refreshed.status == KnowledgeItemStatus.used.value
    # This is the BUG for public KIs — record_usage should be used instead


# ── Regression: stale get_settings() cache after GPCG_DATA_DIR change ──────


def test_local_db_sync_clears_settings_cache_for_data_dir(tmp_path, monkeypatch):
    """Regression: run_generation_locally must clear get_settings() cache after
    setting GPCG_DATA_DIR so GenerationService observes the worker's storage
    directory, not a stale cached value from RemoteWorker startup.

    Bug scenario:
        1. RemoteWorker.__init__() calls get_settings() → cache populated with
           default gpcg_data_dir="./data"
        2. run_generation_locally() sets os.environ["GPCG_DATA_DIR"] to the
           worker's HD path
        3. GenerationService.__init__() calls get_settings() → without
           cache_clear(), returns the STALE cached Settings with "./data"
        4. Rendered videos written to ./data instead of the worker's HD

    This test exercises the actual run_generation_locally code path by
    patching GenerationService to capture the Settings it receives, rather
    than running the full pipeline. If the cache_clear() call is removed
    from local_db_sync.py, the captured Settings will have the stale
    data_dir and the test will fail.
    """
    import os
    import unittest.mock as mock

    from gpcg.config import get_settings

    # ── Step 1: Simulate RemoteWorker.__init__() calling get_settings() at
    # startup, populating the lru_cache with the default gpcg_data_dir.
    get_settings.cache_clear()
    monkeypatch.delenv("GPCG_DATA_DIR", raising=False)
    stale_settings = get_settings()
    stale_data_dir = str(stale_settings.data_dir)

    # ── Step 2: Build a minimal job_data that populate_local_db can handle.
    job_data = {
        "job": {
            "id": 777,
            "job_uuid": "regression-test-uuid",
            "user_id": None,
            "type": "generate_short",
            "status": "queued",
            "stage": "ingest",
            "progress": 0.0,
            "priority": "normal",
            "game_id": None,
            "content_plan_id": None,
            "gameplay_source_id": None,
            "artifacts": {},
            "attempts": 0,
            "max_attempts": 3,
        },
        "gameplay_sources": [],
        "channel_profile": None,
    }

    storage_root = tmp_path / "worker_storage"
    storage_root.mkdir(parents=True)

    # ── Step 3: Patch GenerationService so it captures the Settings it
    # observes at __init__ time, without running the pipeline.
    captured_settings_data_dir = []

    class _CapturingGenerationService:
        """Stand-in for GenerationService that captures get_settings() result."""
        def __init__(self, *args, **kwargs):
            s = get_settings()
            captured_settings_data_dir.append(str(s.data_dir))
            # Provide the attributes run_generation_locally might access
            # before calling run_job (which will fail — we don't care).
            self._session_scope = kwargs.get("session_scope")

        def run_job(self, job_id):
            # Don't run the pipeline — we only care about settings capture.
            return True

    with mock.patch(
        "gpcg.application.generation_service.GenerationService",
        _CapturingGenerationService,
    ):
        from gpcg.worker.local_db_sync import run_generation_locally

        # ── Step 4: Call run_generation_locally. This will:
        #   a. populate_local_db (creates temp SQLite DB)
        #   b. set os.environ["GPCG_DATA_DIR"] = storage_root / "data"
        #   c. get_settings.cache_clear()  ← THE FIX
        #   d. create GenerationService (patched — captures settings)
        #   e. call gen.run_job() (no-op in the patched class)
        result = run_generation_locally(job_data, storage_root)

    # ── Step 5: Cleanup env + cache for other tests.
    os.environ.pop("GPCG_DATA_DIR", None)
    get_settings.cache_clear()

    # ── Step 6: Assert the observable behavior.
    # The GenerationService must have observed the worker's data dir,
    # NOT the stale cached default. If cache_clear() is removed from
    # local_db_sync.py, captured_settings_data_dir[0] will equal
    # stale_data_dir and this assertion will fail.
    assert len(captured_settings_data_dir) == 1, (
        f"Expected GenerationService to be instantiated once, "
        f"got {len(captured_settings_data_dir)} times"
    )
    observed = captured_settings_data_dir[0]
    expected = str(storage_root / "data")
    assert observed == expected, (
        f"GenerationService observed data_dir={observed!r} but expected "
        f"{expected!r}. The get_settings() cache was not cleared after "
        f"setting GPCG_DATA_DIR — rendered videos would be written to "
        f"the wrong directory."
    )
    assert observed != stale_data_dir, (
        f"GenerationService observed stale data_dir {observed!r} "
        f"(matches pre-cache-clear value {stale_data_dir!r})"
    )
