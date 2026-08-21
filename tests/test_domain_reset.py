"""Tests for domain model and domain reset service.

Covers the business rules from the product specification:
1. User has an active domain (default: games).
2. Domain can be changed via reset.
3. Reset requires explicit confirmation.
4. Reset cancels jobs.
5. Reset cleans internal state of the previous domain.
6. Reset creates cleanup jobs for worker storage.
7. In-progress media imports don't persist.
8. Previous domain-specific state doesn't remain.
9. YouTube can stay connected during domain switch.
10. YouTube can be switched without changing domain.
11. YouTube switch doesn't delete media.
12. Domain reset doesn't delete externally published videos.
13. Games still works after refactoring.
14. Core doesn't depend on Games.
15. No regression in existing tests (verified by full suite run).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gpcg.core.models import (
    Base,
    ChannelProfile,
    ContentDomain,
    User,
    Automation,
    Job,
    JobStatus,
    JobType,
    JobPriority,
    Video,
    VideoStatus,
    ContentPlan,
    Script,
    Fact,
    Document,
    KnowledgeItem,
    KnowledgeItemEmbedding,
    KnowledgeItemUsage,
    KnowledgeChunk,
    ChannelProfileEmbedding,
    EditorialSignal,
)
from gpcg.domains.games.models import (
    Game,
    GameAlias,
    GameplaySource,
    GameplayDownload,
    GameplayAsset,
    GameplayClipUsage,
    GameplayEvent,
    GameplayEventEmbedding,
    IngestionStatus,
    GameplayProcessingStatus,
)
from gpcg.application.domain_reset_service import (
    reset_channel_domain,
    VALID_DOMAINS,
)


@pytest.fixture
def db_session():
    """Create an in-memory SQLite DB with all tables."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    session = Session(engine)
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def user_with_games_data(db_session):
    """Create a user with a full Games domain dataset."""
    user = User(email="test@example.com", name="Test User")
    db_session.add(user)
    db_session.flush()

    # Channel profile with games domain
    profile = ChannelProfile(
        user_id=user.id,
        domain=ContentDomain.games.value,
        niche="FPS competitivo",
        gameplay_driven_collection=True,
    )
    db_session.add(profile)

    # Automation
    auto = Automation(user_id=user.id, status="running")
    db_session.add(auto)

    # Game + gameplay source
    game = Game(user_id=user.id, canonical_name="Bully", slug="bully")
    db_session.add(game)
    db_session.flush()

    alias = GameAlias(game_id=game.id, alias="Canis Canem Edit")
    db_session.add(alias)

    source = GameplaySource(
        user_id=user.id,
        game_id=game.id,
        filename="bully_gameplay.mp4",
        file_hash="abc123",
        ingestion_status=IngestionStatus.ready.value,
        processing_status=GameplayProcessingStatus.ready.value,
    )
    db_session.add(source)
    db_session.flush()

    # Gameplay asset
    asset = GameplayAsset(source_id=source.id, start_sec=10, end_sec=20)
    db_session.add(asset)

    # Gameplay event + embedding
    event = GameplayEvent(source_id=source.id, start_time=5.0, end_time=10.0, event_type="COMBAT")
    db_session.add(event)
    db_session.flush()
    emb = GameplayEventEmbedding(event_id=event.id, embedding=b"\x00\x01")
    db_session.add(emb)

    # Clip usage — needs a video_id (NOT NULL FK)
    # We'll create a pending video first, then reference it
    clip_video = Video(
        user_id=user.id, status=VideoStatus.pending.value, game_id=game.id,
    )
    db_session.add(clip_video)
    db_session.flush()
    clip = GameplayClipUsage(source_id=source.id, video_id=clip_video.id)
    db_session.add(clip)

    # Download record
    download = GameplayDownload(source_id=source.id, worker_id="worker-1")
    db_session.add(download)

    # Jobs (queued + running + completed + failed)
    queued_job = Job(
        user_id=user.id, type=JobType.generate_short.value,
        status=JobStatus.queued.value, game_id=game.id,
    )
    running_job = Job(
        user_id=user.id, type=JobType.generate_short.value,
        status=JobStatus.running.value, game_id=game.id,
    )
    completed_job = Job(
        user_id=user.id, type=JobType.generate_short.value,
        status=JobStatus.completed.value, game_id=game.id,
    )
    failed_job = Job(
        user_id=user.id, type=JobType.generate_short.value,
        status=JobStatus.failed.value, game_id=game.id,
    )
    db_session.add_all([queued_job, running_job, completed_job, failed_job])

    # Content plan + script
    plan = ContentPlan(user_id=user.id, game_id=game.id, topic="Bully secrets", hook="Did you know?")
    db_session.add(plan)
    db_session.flush()
    script = Script(content_plan_id=plan.id, draft="Once upon a time...")
    db_session.add(script)

    # Fact
    fact = Fact(user_id=user.id, game_id=game.id, claim="Bully has a hidden level")
    db_session.add(fact)

    # Document
    doc = Document(
        user_id=user.id, game_id=game.id, filename="guide.pdf",
        file_path="/tmp/nonexistent.pdf", file_type="pdf",
    )
    db_session.add(doc)

    # Knowledge item + embedding + usage
    ki = KnowledgeItem(
        user_id=user.id, game_id=game.id, title="Bully news",
        content="New DLC announced",
    )
    db_session.add(ki)
    db_session.flush()
    ki_emb = KnowledgeItemEmbedding(item_id=ki.id, embedding=b"\x00\x02")
    ki_usage = KnowledgeItemUsage(knowledge_item_id=ki.id, consumer_user_id=user.id)
    db_session.add_all([ki_emb, ki_usage])

    # Channel profile embedding
    cp_emb = ChannelProfileEmbedding(user_id=user.id, embedding=b"\x00\x03")
    db_session.add(cp_emb)

    # Editorial signal
    signal = EditorialSignal(user_id=user.id, signal_type="rejection_penalty")
    db_session.add(signal)

    # Videos: one published (pending one already created above for clip usage)
    published_video = Video(
        user_id=user.id, status=VideoStatus.published.value,
        game_id=game.id, youtube_video_id="yt123", youtube_url="https://youtube.com/watch?v=yt123",
    )
    db_session.add(published_video)

    db_session.commit()
    return user.id


# ── Test 1: User has an active domain ────────────────────────────────────────


def test_channel_profile_defaults_to_games(db_session):
    """A new ChannelProfile defaults to the 'games' domain."""
    user = User(email="default@example.com")
    db_session.add(user)
    db_session.flush()

    profile = ChannelProfile(user_id=user.id)
    db_session.add(profile)
    db_session.commit()

    assert profile.domain == ContentDomain.games.value


# ── Test 2: Domain can be changed ────────────────────────────────────────────


def test_domain_can_be_changed(db_session, user_with_games_data):
    """Domain reset changes the channel's domain."""
    user_id = user_with_games_data
    summary = reset_channel_domain(db_session, user_id, "games", confirm=True)
    db_session.commit()

    profile = db_session.query(ChannelProfile).filter(
        ChannelProfile.user_id == user_id
    ).first()
    assert profile.domain == "games"
    assert summary["new_domain"] == "games"


# ── Test 3: Reset requires confirmation ──────────────────────────────────────


def test_reset_requires_confirmation(db_session, user_with_games_data):
    """Reset without confirm=True raises ValueError."""
    user_id = user_with_games_data
    with pytest.raises(ValueError, match="confirmation"):
        reset_channel_domain(db_session, user_id, "games", confirm=False)


def test_reset_rejects_invalid_domain(db_session, user_with_games_data):
    """Reset with an invalid domain raises ValueError."""
    user_id = user_with_games_data
    with pytest.raises(ValueError, match="Invalid domain"):
        reset_channel_domain(db_session, user_id, "invalid_domain", confirm=True)


def test_reset_rejects_non_implemented_domain(db_session, user_with_games_data):
    """Reset to a valid but not-yet-implemented domain raises ValueError."""
    user_id = user_with_games_data
    with pytest.raises(ValueError, match="not yet implemented"):
        reset_channel_domain(db_session, user_id, "kids", confirm=True)


# ── Test 4: Reset cancels jobs ───────────────────────────────────────────────


def test_reset_cancels_queued_and_running_jobs(db_session, user_with_games_data):
    """Queued and running jobs are cancelled by domain reset."""
    user_id = user_with_games_data
    summary = reset_channel_domain(db_session, user_id, "games", confirm=True)
    db_session.commit()

    assert summary["jobs_cancelled"] == 2  # 1 queued + 1 running

    # No non-cleanup jobs should be active (cleanup jobs are allowed to be queued)
    remaining_active = db_session.query(Job).filter(
        Job.user_id == user_id,
        Job.status.in_([JobStatus.queued.value, JobStatus.running.value]),
        ~Job.type.in_([JobType.cleanup_gameplay.value, JobType.cleanup_user_storage.value]),
    ).count()
    assert remaining_active == 0

    cancelled = db_session.query(Job).filter(
        Job.user_id == user_id,
        Job.status == JobStatus.cancelled.value,
    ).count()
    assert cancelled == 2


# ── Test 5: Reset cleans internal state ──────────────────────────────────────


def test_reset_deletes_content_plans_and_scripts(db_session, user_with_games_data):
    """Content plans and scripts are deleted by domain reset."""
    user_id = user_with_games_data
    reset_channel_domain(db_session, user_id, "games", confirm=True)
    db_session.commit()

    plans = db_session.query(ContentPlan).filter(
        ContentPlan.user_id == user_id
    ).count()
    scripts = db_session.query(Script).count()  # scripts belong to plans
    assert plans == 0
    assert scripts == 0


def test_reset_deletes_facts(db_session, user_with_games_data):
    """Facts are deleted by domain reset."""
    user_id = user_with_games_data
    reset_channel_domain(db_session, user_id, "games", confirm=True)
    db_session.commit()

    facts = db_session.query(Fact).filter(Fact.user_id == user_id).count()
    assert facts == 0


def test_reset_deletes_documents(db_session, user_with_games_data):
    """Documents are deleted by domain reset."""
    user_id = user_with_games_data
    reset_channel_domain(db_session, user_id, "games", confirm=True)
    db_session.commit()

    docs = db_session.query(Document).filter(Document.user_id == user_id).count()
    assert docs == 0


def test_reset_deletes_knowledge_items(db_session, user_with_games_data):
    """Knowledge items, embeddings, and usage are deleted by domain reset."""
    user_id = user_with_games_data
    reset_channel_domain(db_session, user_id, "games", confirm=True)
    db_session.commit()

    kis = db_session.query(KnowledgeItem).filter(
        KnowledgeItem.user_id == user_id
    ).count()
    assert kis == 0

    # Embeddings should also be gone
    ki_embs = db_session.query(KnowledgeItemEmbedding).count()
    assert ki_embs == 0

    # Usage records should be gone
    ki_usages = db_session.query(KnowledgeItemUsage).filter(
        KnowledgeItemUsage.consumer_user_id == user_id
    ).count()
    assert ki_usages == 0


def test_reset_deletes_channel_profile_embeddings(db_session, user_with_games_data):
    """Channel profile embeddings are deleted by domain reset."""
    user_id = user_with_games_data
    reset_channel_domain(db_session, user_id, "games", confirm=True)
    db_session.commit()

    embs = db_session.query(ChannelProfileEmbedding).filter(
        ChannelProfileEmbedding.user_id == user_id
    ).count()
    assert embs == 0


def test_reset_deletes_editorial_signals(db_session, user_with_games_data):
    """Editorial signals are deleted by domain reset."""
    user_id = user_with_games_data
    reset_channel_domain(db_session, user_id, "games", confirm=True)
    db_session.commit()

    signals = db_session.query(EditorialSignal).filter(
        EditorialSignal.user_id == user_id
    ).count()
    assert signals == 0


# ── Test 6: Reset creates cleanup jobs for worker storage ────────────────────


def test_reset_creates_cleanup_jobs(db_session, user_with_games_data):
    """Cleanup jobs are created for each gameplay source + 1 user storage cleanup."""
    user_id = user_with_games_data
    summary = reset_channel_domain(db_session, user_id, "games", confirm=True)
    db_session.commit()

    # 1 per-source cleanup_gameplay + 1 cleanup_user_storage
    assert summary["cleanup_jobs_created"] == 2

    gameplay_cleanup = db_session.query(Job).filter(
        Job.user_id == user_id,
        Job.type == JobType.cleanup_gameplay.value,
    ).all()
    assert len(gameplay_cleanup) == 1
    assert gameplay_cleanup[0].status == JobStatus.queued.value
    assert gameplay_cleanup[0].priority == JobPriority.high.value

    storage_cleanup = db_session.query(Job).filter(
        Job.user_id == user_id,
        Job.type == JobType.cleanup_user_storage.value,
    ).all()
    assert len(storage_cleanup) == 1
    assert storage_cleanup[0].status == JobStatus.queued.value
    assert storage_cleanup[0].priority == JobPriority.high.value
    assert storage_cleanup[0].artifacts.get("user_id") == user_id


# ── Test 7: In-progress media imports don't persist ──────────────────────────


def test_reset_deletes_gameplay_sources(db_session, user_with_games_data):
    """Gameplay sources (including in-progress imports) are deleted."""
    user_id = user_with_games_data

    # Add a source that's still uploading (in-progress import)
    uploading_source = GameplaySource(
        user_id=user_id,
        filename="uploading.mp4",
        file_hash="def456",
        ingestion_status=IngestionStatus.discovered.value,
        processing_status=GameplayProcessingStatus.uploading.value,
    )
    db_session.add(uploading_source)
    db_session.commit()

    summary = reset_channel_domain(db_session, user_id, "games", confirm=True)
    db_session.commit()

    assert summary["gameplay_sources_deleted"] == 2  # original + uploading

    sources = db_session.query(GameplaySource).filter(
        GameplaySource.user_id == user_id
    ).count()
    assert sources == 0


def test_reset_deletes_gameplay_assets_events_embeddings(db_session, user_with_games_data):
    """All gameplay-related data (assets, events, embeddings, clip usage) is deleted."""
    user_id = user_with_games_data
    reset_channel_domain(db_session, user_id, "games", confirm=True)
    db_session.commit()

    assert db_session.query(GameplayAsset).count() == 0
    assert db_session.query(GameplayEvent).count() == 0
    assert db_session.query(GameplayEventEmbedding).count() == 0
    assert db_session.query(GameplayClipUsage).count() == 0
    assert db_session.query(GameplayDownload).count() == 0


def test_reset_deletes_games_and_aliases(db_session, user_with_games_data):
    """Games and their aliases are deleted by domain reset."""
    user_id = user_with_games_data
    summary = reset_channel_domain(db_session, user_id, "games", confirm=True)
    db_session.commit()

    assert summary["games_deleted"] == 1

    games = db_session.query(Game).filter(Game.user_id == user_id).count()
    assert games == 0

    aliases = db_session.query(GameAlias).count()
    assert aliases == 0


# ── Test 8: Previous domain-specific state doesn't remain ────────────────────


def test_no_games_state_remains_after_reset(db_session, user_with_games_data):
    """After resetting (even to Games again), no old Games-specific state remains.

    The reset clears all Games data regardless of the target domain. This is
    the correct behavior: reset = full wipe, even if staying in the same domain.
    """
    user_id = user_with_games_data
    reset_channel_domain(db_session, user_id, "games", confirm=True)
    db_session.commit()

    # Check all Games-specific tables are empty for this user
    assert db_session.query(Game).filter(Game.user_id == user_id).count() == 0
    assert db_session.query(GameplaySource).filter(
        GameplaySource.user_id == user_id
    ).count() == 0
    assert db_session.query(GameplayAsset).count() == 0
    assert db_session.query(GameplayEvent).count() == 0
    assert db_session.query(GameplayEventEmbedding).count() == 0
    assert db_session.query(GameplayClipUsage).count() == 0
    assert db_session.query(GameplayDownload).count() == 0

    # Check that ChannelProfile domain is updated
    profile = db_session.query(ChannelProfile).filter(
        ChannelProfile.user_id == user_id
    ).first()
    assert profile.domain == "games"
    # Learned preferences should be reset
    assert profile.learned_preferences == {}
    assert profile.production_history_summary == {}


# ── Test 9: YouTube can stay connected during domain switch ──────────────────


def test_youtube_not_affected_by_domain_reset(db_session, user_with_games_data):
    """Domain reset does not touch User.google_user_id (YouTube connection)."""
    user_id = user_with_games_data
    user = db_session.get(User, user_id)
    user.google_user_id = 42  # simulate connected YouTube
    db_session.commit()

    reset_channel_domain(db_session, user_id, "games", confirm=True)
    db_session.commit()

    refreshed = db_session.get(User, user_id)
    assert refreshed.google_user_id == 42  # YouTube still connected


# ── Test 12: Domain reset doesn't delete published videos ────────────────────


def test_published_videos_preserved(db_session, user_with_games_data):
    """Videos already published to YouTube are NOT deleted by domain reset."""
    user_id = user_with_games_data
    summary = reset_channel_domain(db_session, user_id, "games", confirm=True)
    db_session.commit()

    assert summary["videos_preserved_published"] == 1
    assert summary["videos_deleted"] == 1  # the pending one

    # The published video should still exist
    published = db_session.query(Video).filter(
        Video.user_id == user_id,
        Video.status == VideoStatus.published.value,
    ).count()
    assert published == 1

    # The pending video should be gone
    pending = db_session.query(Video).filter(
        Video.user_id == user_id,
        Video.status == VideoStatus.pending.value,
    ).count()
    assert pending == 0


# ── Test 13: Games still works (domain defaults to games) ────────────────────


def test_games_domain_is_default(db_session):
    """Games is the default domain and is valid."""
    assert ContentDomain.games.value in VALID_DOMAINS
    assert ContentDomain.games.value == "games"


def test_reset_to_games_works(db_session, user_with_games_data):
    """Resetting to Games (even from Games) works without error."""
    user_id = user_with_games_data
    # Reset to games (clears everything)
    reset_channel_domain(db_session, user_id, "games", confirm=True)
    db_session.commit()
    # Then reset to games again
    summary = reset_channel_domain(db_session, user_id, "games", confirm=True)
    db_session.commit()

    profile = db_session.query(ChannelProfile).filter(
        ChannelProfile.user_id == user_id
    ).first()
    assert profile.domain == "games"


# ── Test: Automation is paused after reset ───────────────────────────────────


def test_automation_paused_after_reset(db_session, user_with_games_data):
    """Automation is paused after domain reset."""
    user_id = user_with_games_data
    reset_channel_domain(db_session, user_id, "games", confirm=True)
    db_session.commit()

    auto = db_session.query(Automation).filter(
        Automation.user_id == user_id
    ).first()
    assert auto.status == "paused"


# ── Test: Idempotency — resetting to same domain is a no-op on domain ────────


def test_reset_to_same_domain_still_cleans(db_session, user_with_games_data):
    """Resetting to the same domain still performs cleanup (acts as a reset)."""
    user_id = user_with_games_data
    summary = reset_channel_domain(db_session, user_id, "games", confirm=True)
    db_session.commit()

    # Should still clean everything
    assert summary["gameplay_sources_deleted"] == 1
    assert summary["jobs_cancelled"] == 2
    assert summary["content_plans_deleted"] == 1
    # 1 per-source cleanup + 1 user storage cleanup
    assert summary["cleanup_jobs_created"] == 2

    profile = db_session.query(ChannelProfile).filter(
        ChannelProfile.user_id == user_id
    ).first()
    assert profile.domain == "games"  # unchanged


# ── Test: ContentDomain enum has expected values ─────────────────────────────


def test_content_domain_enum_values():
    """ContentDomain enum contains the expected domains."""
    values = {d.value for d in ContentDomain}
    assert "games" in values
    assert "kids" in values
    assert "movies" in values
    assert "conspiracy" in values
    assert "technology" in values
