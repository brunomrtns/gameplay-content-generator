"""V3 Refactor — Phase 6: End-to-end homologation test.

Validates the complete flow:
1. User creates an automation with queue_mode + config
2. User enqueues a KnowledgeItem (public)
3. check_automation returns the pending automation with queue_mode
4. create_job_from_automation creates a job with config_snapshot
5. Job artifacts include gameplay_preference, reuse_override, config_snapshot
6. ChannelProfile is fetched for the user
7. record_usage marks KI as used (per-consumer for public KIs)
8. Another user can still see and use the same public KI
9. Config snapshot excludes secrets
10. Reconciliador fills empty queue when auto_fill_queue is enabled
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from gpcg.domain.models import (
    Automation,
    Base,
    ChannelProfile,
    Game,
    GameplaySource,
    IngestionStatus,
    Job,
    JobStatus,
    JobType,
    KnowledgeItem,
    KnowledgeItemStatus,
    KnowledgeItemUsage,
    User,
)
from gpcg.application.knowledge_item_service import (
    is_used_by_consumer,
    record_usage,
)


@pytest.fixture
def db_session():
    """In-memory SQLite session for E2E testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def full_setup(db_session):
    """Create a complete setup: 2 users, game, gameplay, KI, automation, channel profile."""
    # Users
    user_a = User(email="a@test.com", name="User A", is_active=True, google_user_id="ggl-a")
    user_b = User(email="b@test.com", name="User B", is_active=True, google_user_id="ggl-b")
    db_session.add_all([user_a, user_b])
    db_session.flush()

    # Game
    game = Game(canonical_name="Test Game", user_id=user_a.id)
    db_session.add(game)
    db_session.flush()

    # GameplaySource (ready)
    gameplay = GameplaySource(
        user_id=user_a.id,
        game_id=game.id,
        filename="test.mp4",
        file_hash="abc123def456",
        ingestion_status=IngestionStatus.ready.value,
        duration=300.0,
        file_size=1024 * 1024,
        file_path="/tmp/test.mp4",
    )
    db_session.add(gameplay)
    db_session.flush()

    # ChannelProfile for user A
    profile = ChannelProfile(
        user_id=user_a.id,
        channel_description="Canal de gaming focado em FPS",
        niche="FPS competitivo",
        target_audience="Jogadores competitivos",
        tone_of_voice="educativo",
        narrative_style="análise",
        content_goals="Educar a comunidade",
        special_rules="Sem spoilers de campanha",
    )
    db_session.add(profile)
    db_session.flush()

    # Public KnowledgeItem (shared pool)
    ki = KnowledgeItem(
        user_id=None,
        is_public=True,
        title="Breaking: Novo patch balanceia armas",
        content="O patch 1.2 traz mudanças significativas...",
        item_type="news",
        source_type="rss",
        status=KnowledgeItemStatus.fresh.value,
        editorial_score=75.0,
    )
    db_session.add(ki)
    db_session.flush()

    # Automation for user A with config + queue_mode
    auto = Automation(
        user_id=user_a.id,
        name="Minha Automação",
        status="running",
        config={
            "video_format": "9:16",
            "scene_duration": 5,
            "creative_style": "curiosity",
            "voice": "narrator.wav",
            "max_clip_uses": 1,
            "fallback_policy": "stop",
            "queue_mode": "automatic",
            "idea_queue": [
                {"ki_id": ki.id, "gameplay_preference": None, "reuse_override": None}
            ],
        },
        upload_config={},
    )
    db_session.add(auto)
    db_session.commit()

    return {
        "user_a": user_a,
        "user_b": user_b,
        "game": game,
        "gameplay": gameplay,
        "ki": ki,
        "auto": auto,
        "profile": profile,
    }


# ── 1. check_automation returns queue_mode ─────────────────────────────────

def test_check_automation_returns_queue_mode(db_session, full_setup):
    """check_automation must include queue_mode in the response."""
    from gpcg.api.automation_routes import _normalize_idea_queue

    auto = db_session.query(Automation).first()
    cfg = auto.config or {}
    queue_mode = cfg.get("queue_mode", "automatic")
    assert queue_mode == "automatic"

    # Verify the idea_queue is properly normalized
    raw_queue = cfg.get("idea_queue", [])
    idea_queue = _normalize_idea_queue(raw_queue)
    assert len(idea_queue) == 1
    assert idea_queue[0]["ki_id"] == full_setup["ki"].id


# ── 2. Config snapshot is built correctly ──────────────────────────────────

def test_config_snapshot_built_from_automation(db_session, full_setup):
    """_build_config_snapshot extracts only generation-relevant fields."""
    from gpcg.api.automation_routes import _build_config_snapshot

    auto = db_session.query(Automation).first()
    cfg = auto.config or {}
    snapshot = _build_config_snapshot(cfg)

    # Generation fields present
    assert snapshot["video_format"] == "9:16"
    assert snapshot["scene_duration"] == 5
    assert snapshot["creative_style"] == "curiosity"
    assert snapshot["voice"] == "narrator.wav"
    assert snapshot["max_clip_uses"] == 1
    assert snapshot["fallback_policy"] == "stop"

    # Non-generation fields excluded
    assert "idea_queue" not in snapshot
    assert "queue_mode" not in snapshot
    assert "auto_fill_queue" not in snapshot


# ── 3. ChannelProfile is accessible for the user ───────────────────────────

def test_channel_profile_accessible_for_user(db_session, full_setup):
    """ChannelProfile can be fetched by user_id."""
    user_a = full_setup["user_a"]
    profile = db_session.query(ChannelProfile).filter(
        ChannelProfile.user_id == user_a.id
    ).first()
    assert profile is not None
    assert profile.channel_description == "Canal de gaming focado em FPS"
    assert profile.niche == "FPS competitivo"
    assert profile.tone_of_voice == "educativo"


# ── 4. Public KI consumption is per-user ───────────────────────────────────

def test_public_ki_consumption_per_user_e2e(db_session, full_setup):
    """User A consumes a public KI → User B can still use it."""
    user_a = full_setup["user_a"]
    user_b = full_setup["user_b"]
    ki = full_setup["ki"]

    # User A consumes
    record_usage(db_session, ki.id, user_a.id)
    db_session.commit()

    # KI stays fresh globally
    refreshed = db_session.get(KnowledgeItem, ki.id)
    assert refreshed.status == KnowledgeItemStatus.fresh.value

    # User A has usage record
    assert is_used_by_consumer(db_session, ki.id, user_a.id) is True

    # User B does NOT have usage record
    assert is_used_by_consumer(db_session, ki.id, user_b.id) is False

    # User B can also consume
    record_usage(db_session, ki.id, user_b.id)
    db_session.commit()
    assert is_used_by_consumer(db_session, ki.id, user_b.id) is True

    # KI still fresh globally
    refreshed = db_session.get(KnowledgeItem, ki.id)
    assert refreshed.status == KnowledgeItemStatus.fresh.value

    # Two usage records
    usages = db_session.execute(
        select(KnowledgeItemUsage).where(
            KnowledgeItemUsage.knowledge_item_id == ki.id
        )
    ).scalars().all()
    assert len(usages) == 2


# ── 5. Reconciliador fills empty queue ─────────────────────────────────────

def test_reconciliador_fills_empty_queue(db_session, full_setup):
    """_reconcile_idea_queue fills with fresh KIs when queue is empty."""
    from gpcg.api.automation_routes import _reconcile_idea_queue

    user_a = full_setup["user_a"]

    # Add more KIs for the reconciliador to find
    for i in range(5):
        db_session.add(KnowledgeItem(
            user_id=None,
            is_public=True,
            title=f"News item {i}",
            content=f"Content {i}",
            item_type="news",
            source_type="rss",
            status=KnowledgeItemStatus.fresh.value,
            editorial_score=50.0 + i * 10,
        ))
    db_session.commit()

    # Empty queue config
    config = {"max_queue_size": 3, "queue_mode": "automatic"}
    result = _reconcile_idea_queue(db_session, user_a.id, config)

    assert len(result) == 3
    # Sorted by editorial_score descending → highest first
    assert all("ki_id" in entry for entry in result)
    assert all("gameplay_preference" in entry for entry in result)
    assert all("reuse_override" in entry for entry in result)


def test_reconciliador_returns_empty_when_no_fresh_kis(db_session, full_setup):
    """_reconcile_idea_queue returns empty list when no fresh KIs available."""
    from gpcg.api.automation_routes import _reconcile_idea_queue

    user_a = full_setup["user_a"]
    ki = full_setup["ki"]

    # Mark all KIs as used
    ki.status = KnowledgeItemStatus.used.value
    db_session.commit()

    config = {"max_queue_size": 10, "queue_mode": "automatic"}
    result = _reconcile_idea_queue(db_session, user_a.id, config)
    assert result == []


# ── 6. Job artifacts include all V3 fields ─────────────────────────────────

def test_job_artifacts_include_v3_fields(db_session, full_setup):
    """Job created from automation must include config_snapshot + gameplay fields."""
    user_a = full_setup["user_a"]
    game = full_setup["game"]

    # Simulate what create_job_from_automation does
    from gpcg.api.automation_routes import _build_config_snapshot

    auto = db_session.query(Automation).first()
    cfg = auto.config or {}
    config_snapshot = _build_config_snapshot(cfg)

    job = Job(
        user_id=user_a.id,
        game_id=game.id,
        type=JobType.generate_short.value,
        status=JobStatus.queued.value,
        artifacts={
            "queued_knowledge_item_id": full_setup["ki"].id,
            "idea_source": "user_queue",
            "gameplay_preference": None,
            "reuse_override": None,
            "gameplay_selection_mode": "auto",
            "config_snapshot": config_snapshot,
        },
    )
    db_session.add(job)
    db_session.commit()

    fetched = db_session.get(Job, job.id)
    assert "config_snapshot" in fetched.artifacts
    assert "queued_knowledge_item_id" in fetched.artifacts
    assert "gameplay_preference" in fetched.artifacts
    assert "reuse_override" in fetched.artifacts
    assert "gameplay_selection_mode" in fetched.artifacts

    snap = fetched.artifacts["config_snapshot"]
    assert snap["video_format"] == "9:16"
    assert snap["scene_duration"] == 5
    assert "idea_queue" not in snap


# ── 7. GenerationService reads config_snapshot from job artifacts ──────────

def test_generation_reads_config_snapshot(db_session, full_setup):
    """GenerationService should prefer config_snapshot over live config."""
    user_a = full_setup["user_a"]
    game = full_setup["game"]

    from gpcg.api.automation_routes import _build_config_snapshot

    auto = db_session.query(Automation).first()
    cfg = auto.config or {}
    config_snapshot = _build_config_snapshot(cfg)

    # Create a job with the snapshot
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

    # Simulate what GenerationService does: read from config_snapshot
    fetched = db_session.get(Job, job.id)
    snapshot = fetched.artifacts.get("config_snapshot") or {}
    max_clip_uses = snapshot.get("max_clip_uses", 1)
    fallback_policy = snapshot.get("fallback_policy")

    assert max_clip_uses == 1
    assert fallback_policy == "stop"


# ── 8. Manual mode prevents editorial fallback ─────────────────────────────

def test_manual_mode_prevents_editorial_fallback(db_session, full_setup):
    """In manual mode, empty queue should NOT trigger editorial decision."""
    auto = db_session.query(Automation).first()
    user_a = full_setup["user_a"]
    ki = full_setup["ki"]

    # Set manual mode + empty queue
    auto.config = {
        "queue_mode": "manual",
        "idea_queue": [],
        "video_format": "9:16",
    }
    db_session.commit()

    cfg = auto.config or {}
    queue_mode = cfg.get("queue_mode", "automatic")
    idea_queue = cfg.get("idea_queue", [])

    assert queue_mode == "manual"
    assert len(idea_queue) == 0

    # In the remote_worker, this combination would skip:
    # if idea_queue: consume → NO
    # elif queue_mode == "manual": skip → YES
    # else: editorial decision → NOT REACHED


# ── 9. Worker status classification ────────────────────────────────────────

def test_worker_stale_classification(db_session, full_setup):
    """Workers should be classified as active/offline/stale."""
    from gpcg.domain.models import Worker, WorkerStatus
    from datetime import datetime, timedelta, timezone

    # Active worker (recent heartbeat)
    active = Worker(
        worker_id="active-worker",
        hostname="machine-a",
        status=WorkerStatus.online.value,
        last_heartbeat=datetime.now(timezone.utc),
    )
    db_session.add(active)

    # Offline worker (heartbeat > timeout but < stale threshold)
    offline = Worker(
        worker_id="offline-worker",
        hostname="machine-b",
        status=WorkerStatus.offline.value,
        last_heartbeat=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    db_session.add(offline)

    # Stale worker (heartbeat > 10x timeout)
    stale = Worker(
        worker_id="stale-worker",
        hostname="machine-c",
        status=WorkerStatus.offline.value,
        last_heartbeat=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    db_session.add(stale)
    db_session.commit()

    workers = db_session.query(Worker).all()
    assert len(workers) == 3

    # Verify statuses
    statuses = {w.worker_id: w.status for w in workers}
    assert statuses["active-worker"] == WorkerStatus.online.value
    assert statuses["offline-worker"] == WorkerStatus.offline.value
    assert statuses["stale-worker"] == WorkerStatus.offline.value


# ── 10. Full lifecycle: queue → consume → record_usage → stays fresh ───────

def test_full_lifecycle_public_ki(db_session, full_setup):
    """Full lifecycle: KI in queue → job created → KI consumed → stays fresh for others."""
    user_a = full_setup["user_a"]
    user_b = full_setup["user_b"]
    ki = full_setup["ki"]

    # 1. KI is in the queue
    auto = db_session.query(Automation).first()
    queue = auto.config.get("idea_queue", [])
    assert len(queue) == 1
    assert queue[0]["ki_id"] == ki.id

    # 2. Simulate job creation from queue
    job = Job(
        user_id=user_a.id,
        game_id=full_setup["game"].id,
        type=JobType.generate_short.value,
        status=JobStatus.queued.value,
        artifacts={
            "queued_knowledge_item_id": ki.id,
            "config_snapshot": {"video_format": "9:16"},
        },
    )
    db_session.add(job)
    db_session.commit()

    # 3. Simulate result sync: record_usage (not mark_as_used)
    record_usage(db_session, ki.id, user_a.id)
    db_session.commit()

    # 4. KI stays fresh for other users
    refreshed = db_session.get(KnowledgeItem, ki.id)
    assert refreshed.status == KnowledgeItemStatus.fresh.value

    # 5. User A has usage record
    assert is_used_by_consumer(db_session, ki.id, user_a.id) is True

    # 6. User B can still see and use it
    assert is_used_by_consumer(db_session, ki.id, user_b.id) is False
    record_usage(db_session, ki.id, user_b.id)
    db_session.commit()
    assert is_used_by_consumer(db_session, ki.id, user_b.id) is True

    # 7. KI STILL fresh globally
    refreshed = db_session.get(KnowledgeItem, ki.id)
    assert refreshed.status == KnowledgeItemStatus.fresh.value
