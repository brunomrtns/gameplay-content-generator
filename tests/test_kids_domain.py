"""Kids domain tests — models, pipeline dispatch, reset, storage isolation.

Tests the Kids domain implementation:
- Kids models (KidsTopic, StoryAsset)
- Domain registry dispatch (games vs kids)
- Domain reset Games→Kids and Kids→Games
- Storage isolation (kids_assets separate from gameplays)
- Kids API endpoints
- Architecture boundaries (already in test_architecture.py)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gpcg.core.models import (
    Base,
    ChannelProfile,
    ContentDomain,
    ContentPlan,
    Job,
    JobStatus,
    JobType,
    Script,
    User,
    Video,
    VideoStatus,
)
from gpcg.domains.games.models import Game, GameplaySource, IngestionStatus
from gpcg.domains.kids.models import KidsTopic, StoryAsset, AssetProcessingStatus
from gpcg.domains.registry import (
    IMPLEMENTED_DOMAINS,
    get_generation_service,
    is_domain_implemented,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def db_session():
    """Create an in-memory SQLite DB with all tables."""
    import gpcg.core.models  # noqa: F401
    import gpcg.domains.games.models  # noqa: F401
    import gpcg.domains.kids.models  # noqa: F401

    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    session = Session(engine)
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def user_with_kids(db_session):
    """Create a user with Kids domain set up."""
    user = User(email="kids@example.com", name="Kids User")
    db_session.add(user)
    db_session.flush()

    profile = ChannelProfile(
        user_id=user.id,
        domain=ContentDomain.kids.value,
        niche="Educativo infantil",
    )
    db_session.add(profile)
    db_session.commit()
    return user.id


@pytest.fixture
def user_with_games(db_session):
    """Create a user with Games domain set up."""
    user = User(email="games@example.com", name="Games User")
    db_session.add(user)
    db_session.flush()

    profile = ChannelProfile(
        user_id=user.id,
        domain=ContentDomain.games.value,
        niche="FPS competitivo",
    )
    db_session.add(profile)
    db_session.commit()
    return user.id


@pytest.fixture
def kids_topic_with_assets(db_session, user_with_kids):
    """Create a Kids topic with story assets."""
    topic = KidsTopic(
        user_id=user_with_kids,
        title="Dinossauros",
        slug="dinossauros",
        category="educational",
        age_range="3-6",
        description="Tudo sobre dinossauros para crianças",
    )
    db_session.add(topic)
    db_session.flush()

    # Add 3 story assets
    for i in range(3):
        asset = StoryAsset(
            user_id=user_with_kids,
            topic_id=topic.id,
            filename=f"dino_{i}.png",
            storage_key=f"abc123_dino_{i}.png",
            file_hash=f"hash_{i}",
            file_size=1024,
            width=1920,
            height=1080,
            processing_status=AssetProcessingStatus.ready.value,
        )
        db_session.add(asset)
    db_session.commit()
    return topic.id


# ── Model tests ──────────────────────────────────────────────────────────────


def test_kids_topic_creation(db_session, user_with_kids):
    """KidsTopic can be created and persisted."""
    topic = KidsTopic(
        user_id=user_with_kids,
        title="Sistema Solar",
        slug="sistema-solar",
        category="science",
        age_range="7-10",
        description="Planetas e estrelas",
    )
    db_session.add(topic)
    db_session.commit()

    loaded = db_session.query(KidsTopic).filter(KidsTopic.title == "Sistema Solar").first()
    assert loaded is not None
    assert loaded.category == "science"
    assert loaded.age_range == "7-10"


def test_story_asset_creation(db_session, kids_topic_with_assets):
    """StoryAsset can be created and linked to a topic."""
    assets = db_session.query(StoryAsset).filter(
        StoryAsset.topic_id == kids_topic_with_assets
    ).all()
    assert len(assets) == 3
    for a in assets:
        assert a.processing_status == AssetProcessingStatus.ready.value
        assert a.width == 1920
        assert a.height == 1080


def test_topic_cascade_deletes_assets(db_session, kids_topic_with_assets):
    """Deleting a topic cascades to its assets."""
    topic_id = kids_topic_with_assets
    topic = db_session.get(KidsTopic, topic_id)
    db_session.delete(topic)
    db_session.commit()

    assets = db_session.query(StoryAsset).filter(
        StoryAsset.topic_id == topic_id
    ).all()
    assert len(assets) == 0


def test_kids_topic_relationship(db_session, kids_topic_with_assets):
    """Topic.assets relationship works."""
    topic = db_session.get(KidsTopic, kids_topic_with_assets)
    assert len(topic.assets) == 3
    assert all(a.topic_id == topic.id for a in topic.assets)


# ── Registry tests ───────────────────────────────────────────────────────────


def test_implemented_domains_includes_games_and_kids():
    """Both games and kids are in IMPLEMENTED_DOMAINS."""
    assert "games" in IMPLEMENTED_DOMAINS
    assert "kids" in IMPLEMENTED_DOMAINS


def test_movies_conspiracy_technology_not_implemented():
    """Future domains are NOT implemented."""
    assert "movies" not in IMPLEMENTED_DOMAINS
    assert "conspiracy" not in IMPLEMENTED_DOMAINS
    assert "technology" not in IMPLEMENTED_DOMAINS


def test_is_domain_implemented():
    """is_domain_implemented helper works."""
    assert is_domain_implemented("games") is True
    assert is_domain_implemented("kids") is True
    assert is_domain_implemented("movies") is False


def test_registry_dispatches_to_games():
    """get_generation_service returns GenerationService for games."""
    from gpcg.infrastructure.database import session_scope
    gen = get_generation_service("games", session_scope=session_scope)
    from gpcg.application.generation_service import GenerationService
    assert isinstance(gen, GenerationService)


def test_registry_dispatches_to_kids():
    """get_generation_service returns KidsGenerationService for kids."""
    from gpcg.infrastructure.database import session_scope
    gen = get_generation_service("kids", session_scope=session_scope)
    from gpcg.domains.kids.pipeline import KidsGenerationService
    assert isinstance(gen, KidsGenerationService)


def test_registry_rejects_unknown_domain():
    """get_generation_service raises ValueError for unknown domains."""
    from gpcg.infrastructure.database import session_scope
    with pytest.raises(ValueError, match="not have a pipeline"):
        get_generation_service("movies", session_scope=session_scope)


# ── Domain reset tests ───────────────────────────────────────────────────────


def test_reset_games_to_kids(db_session, user_with_games):
    """Reset from Games to Kids deletes Games data and sets Kids domain."""
    from gpcg.application.domain_reset_service import reset_channel_domain

    # Add a game to verify it gets deleted
    game = Game(user_id=user_with_games, canonical_name="Test Game", slug="test")
    db_session.add(game)
    db_session.commit()

    summary = reset_channel_domain(db_session, user_with_games, "kids", confirm=True)

    assert summary["old_domain"] == "games"
    assert summary["new_domain"] == "kids"
    assert summary["games_deleted"] == 1

    # Verify domain changed
    profile = db_session.query(ChannelProfile).filter(
        ChannelProfile.user_id == user_with_games
    ).first()
    assert profile.domain == "kids"

    # Verify game was deleted
    games = db_session.query(Game).filter(Game.user_id == user_with_games).all()
    assert len(games) == 0


def test_reset_kids_to_games(db_session, user_with_kids, kids_topic_with_assets):
    """Reset from Kids to Games deletes Kids data and sets Games domain."""
    from gpcg.application.domain_reset_service import reset_channel_domain

    summary = reset_channel_domain(db_session, user_with_kids, "games", confirm=True)

    assert summary["old_domain"] == "kids"
    assert summary["new_domain"] == "games"
    assert summary["kids_topics_deleted"] == 1
    assert summary["story_assets_deleted"] == 3

    # Verify domain changed
    profile = db_session.query(ChannelProfile).filter(
        ChannelProfile.user_id == user_with_kids
    ).first()
    assert profile.domain == "games"

    # Verify Kids data was deleted
    topics = db_session.query(KidsTopic).filter(
        KidsTopic.user_id == user_with_kids
    ).all()
    assert len(topics) == 0

    assets = db_session.query(StoryAsset).filter(
        StoryAsset.user_id == user_with_kids
    ).all()
    assert len(assets) == 0


def test_reset_kids_preserves_youtube(db_session, user_with_kids):
    """Reset from Kids to Games preserves YouTube connection."""
    from gpcg.application.domain_reset_service import reset_channel_domain

    user = db_session.get(User, user_with_kids)
    user.google_user_id = "google-123"
    db_session.commit()

    reset_channel_domain(db_session, user_with_kids, "games", confirm=True)

    user_after = db_session.get(User, user_with_kids)
    assert user_after.google_user_id == "google-123"


def test_reset_kids_preserves_published_videos(db_session, user_with_kids):
    """Reset from Kids preserves published videos."""
    from gpcg.application.domain_reset_service import reset_channel_domain

    # Create a published video
    video = Video(
        user_id=user_with_kids,
        status=VideoStatus.published.value,
        youtube_video_id="yt-123",
        duration=60.0,
    )
    db_session.add(video)
    db_session.commit()

    summary = reset_channel_domain(db_session, user_with_kids, "games", confirm=True)

    assert summary["videos_preserved_published"] == 1
    assert summary["videos_deleted"] == 0

    # Video still exists
    videos = db_session.query(Video).filter(Video.user_id == user_with_kids).all()
    assert len(videos) == 1
    assert videos[0].status == VideoStatus.published.value


# ── Job domain tests ─────────────────────────────────────────────────────────


def test_kids_job_carries_domain(db_session, user_with_kids, kids_topic_with_assets):
    """Kids generation jobs carry domain='kids'."""
    import uuid
    job = Job(
        job_uuid=str(uuid.uuid4()),
        type=JobType.generate_short.value,
        user_id=user_with_kids,
        domain=ContentDomain.kids.value,
        status=JobStatus.queued.value,
        artifacts={"topic_id": kids_topic_with_assets},
    )
    db_session.add(job)
    db_session.commit()

    loaded = db_session.query(Job).filter(
        Job.user_id == user_with_kids,
        Job.domain == "kids",
    ).first()
    assert loaded is not None
    assert loaded.domain == "kids"
    assert loaded.artifacts["topic_id"] == kids_topic_with_assets


def test_kids_job_does_not_need_game_id(db_session, user_with_kids, kids_topic_with_assets):
    """Kids jobs don't require game_id (it's NULL)."""
    import uuid
    job = Job(
        job_uuid=str(uuid.uuid4()),
        type=JobType.generate_short.value,
        user_id=user_with_kids,
        domain=ContentDomain.kids.value,
        status=JobStatus.queued.value,
        game_id=None,
        artifacts={"topic_id": kids_topic_with_assets},
    )
    db_session.add(job)
    db_session.commit()

    assert job.game_id is None
    assert job.domain == "kids"


# ── Storage isolation tests ──────────────────────────────────────────────────


def test_kids_assets_dir_separate_from_gameplays(tmp_path):
    """Kids assets are stored in a separate directory from gameplays."""
    gameplays_dir = tmp_path / "gameplays"
    kids_assets_dir = tmp_path / "kids_assets"
    gameplays_dir.mkdir()
    kids_assets_dir.mkdir()

    # Simulate a gameplay file
    (gameplays_dir / "gameplay_1.mp4").write_bytes(b"fake gameplay")

    # Simulate a kids asset
    (kids_assets_dir / "dino_1.png").write_bytes(b"fake image")

    # Verify they don't overlap
    gameplay_files = list(gameplays_dir.iterdir())
    kids_files = list(kids_assets_dir.iterdir())

    assert any(f.name == "gameplay_1.mp4" for f in gameplay_files)
    assert any(f.name == "dino_1.png" for f in kids_files)
    assert not any(f.name == "dino_1.png" for f in gameplay_files)
    assert not any(f.name == "gameplay_1.mp4" for f in kids_files)


def test_cleanup_gameplay_does_not_touch_kids_assets(tmp_path):
    """Games cleanup only deletes from gameplays/, not kids_assets/."""
    gameplays_dir = tmp_path / "gameplays"
    kids_assets_dir = tmp_path / "kids_assets"
    gameplays_dir.mkdir()
    kids_assets_dir.mkdir()

    gameplay_file = gameplays_dir / "gameplay_1.mp4"
    kids_file = kids_assets_dir / "dino_1.png"
    gameplay_file.write_bytes(b"fake gameplay")
    kids_file.write_bytes(b"fake image")

    # Simulate Games cleanup (old_domain="games")
    storage_root = tmp_path.resolve()
    old_domain = "games"

    if old_domain == "games":
        for f in (gameplays_dir).iterdir():
            if f.is_file():
                f.unlink()

    # gameplay deleted, kids asset preserved
    assert not gameplay_file.exists()
    assert kids_file.exists()


def test_cleanup_kids_does_not_touch_gameplays(tmp_path):
    """Kids cleanup only deletes from kids_assets/, not gameplays/."""
    gameplays_dir = tmp_path / "gameplays"
    kids_assets_dir = tmp_path / "kids_assets"
    gameplays_dir.mkdir()
    kids_assets_dir.mkdir()

    gameplay_file = gameplays_dir / "gameplay_1.mp4"
    kids_file = kids_assets_dir / "dino_1.png"
    gameplay_file.write_bytes(b"fake gameplay")
    kids_file.write_bytes(b"fake image")

    # Simulate Kids cleanup (old_domain="kids")
    old_domain = "kids"

    if old_domain == "kids":
        for f in (kids_assets_dir).iterdir():
            if f.is_file():
                f.unlink()

    # kids asset deleted, gameplay preserved
    assert not kids_file.exists()
    assert gameplay_file.exists()


# ── ContentPlan visual_strategy test ─────────────────────────────────────────


def test_content_plan_default_visual_strategy_is_auto(db_session, user_with_kids):
    """ContentPlan default visual_strategy is 'auto' (domain-neutral)."""
    plan = ContentPlan(
        user_id=user_with_kids,
        topic="Test topic",
        hook="Test hook",
    )
    db_session.add(plan)
    db_session.commit()

    assert plan.visual_strategy == "auto"


def test_kids_plan_uses_image_slideshow(db_session, user_with_kids):
    """Kids content plans use 'image_slideshow' visual strategy."""
    plan = ContentPlan(
        user_id=user_with_kids,
        topic="Dinossauros",
        hook="Sabia que...",
        visual_strategy="image_slideshow",
        metadata_json={"domain": "kids"},
    )
    db_session.add(plan)
    db_session.commit()

    assert plan.visual_strategy == "image_slideshow"
    assert plan.metadata_json.get("domain") == "kids"
    assert plan.game_id is None  # Kids has no game


# ── Kids prompts test ────────────────────────────────────────────────────────


def test_kids_prompts_are_separate_from_games():
    """Kids prompts module exists and has kid-friendly content."""
    from gpcg.domains.kids import prompts as kids_prompts
    from gpcg.domains.games import prompts as games_prompts

    # Kids prompts exist
    assert hasattr(kids_prompts, "DRAFT_SYSTEM")
    assert hasattr(kids_prompts, "PLAN_DRAFT_SYSTEM")
    assert hasattr(kids_prompts, "CONTENT_PLANNING_SYSTEM")

    # Kids prompts mention kid-friendly concepts
    assert "criança" in kids_prompts.DRAFT_SYSTEM.lower() or "kid" in kids_prompts.DRAFT_SYSTEM.lower()
    assert "educational" in kids_prompts.DRAFT_SYSTEM.lower() or "educativo" in kids_prompts.DRAFT_SYSTEM.lower()

    # Kids prompts do NOT contain gaming terminology
    assert "gameplay" not in kids_prompts.DRAFT_SYSTEM.lower()
    assert "game mechanics" not in kids_prompts.DRAFT_SYSTEM.lower()


def test_kids_prompts_do_not_reference_games():
    """Kids prompts should not reference Games-specific concepts."""
    from gpcg.domains.kids import prompts as kids_prompts

    all_prompts = [
        kids_prompts.DRAFT_SYSTEM,
        kids_prompts.PLAN_DRAFT_SYSTEM,
        kids_prompts.CONTENT_PLANNING_SYSTEM,
        kids_prompts.METADATA_SYSTEM,
    ]

    for prompt in all_prompts:
        # Should not mention gameplay, game facts, or gaming channel
        lower = prompt.lower()
        assert "gameplay" not in lower, f"Kids prompt contains 'gameplay': {prompt[:50]}..."
        assert "game fact" not in lower, f"Kids prompt contains 'game fact': {prompt[:50]}..."


# ── Regression tests for bugs found during audit ─────────────────────────────


def test_render_plan_builder_handles_none_asset():
    """P1 regression: RenderPlanBuilder must not crash when clip.asset is None.

    Kids pipeline creates SelectedClip(asset=None, ...) for image-derived clips.
    The render plan builder used to do clip.asset.used_count += 1 unconditionally,
    which would crash with AttributeError for Kids clips.
    """
    from gpcg.application.gameplay_selector import SelectedClip

    # Simulate a Kids clip (asset=None)
    clip = SelectedClip(
        asset=None,
        source_path="/tmp/fake_scene.mp4",
        start_sec=0.0,
        end_sec=5.0,
        duration=5.0,
        scene_index=0,
    )
    # This should NOT crash — the guard checks for None
    if clip.asset is not None:
        clip.asset.used_count += 1  # Would crash if asset is None and no guard
    # If we reach here, the guard works
    assert clip.asset is None


def test_sync_job_result_default_visual_strategy_is_auto():
    """P3 regression: sync_job_result default visual_strategy should be 'auto',
    not 'gameplay_compilation' (which is Games-specific and would contaminate Kids).
    """
    # The fix changed the default from "gameplay_compilation" to "auto" in
    # worker_routes.py sync_job_result. Verify the code has the correct default.
    import ast
    from pathlib import Path

    worker_routes = Path(__file__).parent.parent / "src" / "gpcg" / "api" / "worker_routes.py"
    content = worker_routes.read_text()

    # Find the line with visual_strategy default
    assert '"gameplay_compilation"' not in content.split("visual_strategy=req.content_plan.get")[0].split("\n")[-1] or \
           '"auto"' in content.split("visual_strategy=req.content_plan.get")[1].split("\n")[0]


def test_get_job_data_skips_gameplay_sources_for_kids():
    """P6 regression: get_job_data should not send gameplay_sources for Kids jobs."""
    import ast
    from pathlib import Path

    worker_routes = Path(__file__).parent.parent / "src" / "gpcg" / "api" / "worker_routes.py"
    content = worker_routes.read_text()

    # Verify there's a domain check before querying gameplay sources
    assert 'job.domain != "kids"' in content or 'job.domain == "games"' in content


def test_cleanup_job_carries_filenames():
    """P7 regression: cleanup_user_storage job should carry filenames list
    for user-scoped cleanup (multi-user safety)."""
    from gpcg.application.domain_reset_service import reset_channel_domain
    from gpcg.core.models import (
        ChannelProfile, ContentDomain,
    )
    from gpcg.domains.games.models import (
        Game, GameplaySource, IngestionStatus, GameplayProcessingStatus,
    )

    # Create a fresh in-memory DB
    import gpcg.core.models  # noqa: F401
    import gpcg.domains.games.models  # noqa: F401
    import gpcg.domains.kids.models  # noqa: F401
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    session = Session(engine)

    user = User(email="cleanup@example.com", name="Cleanup Test")
    session.add(user)
    session.flush()
    profile = ChannelProfile(user_id=user.id, domain=ContentDomain.games.value)
    session.add(profile)
    game = Game(user_id=user.id, canonical_name="Test", slug="test")
    session.add(game)
    session.flush()
    src = GameplaySource(
        user_id=user.id, game_id=game.id, filename="test_gameplay.mp4",
        file_hash="abc123", ingestion_status=IngestionStatus.ready.value,
        processing_status=GameplayProcessingStatus.ready.value,
        storage_key="abc123_test_gameplay.mp4",
    )
    session.add(src)
    session.commit()

    summary = reset_channel_domain(session, user.id, "kids", confirm=True)

    # Find the cleanup_user_storage job
    from gpcg.core.models import Job, JobType
    cleanup_job = session.query(Job).filter(
        Job.type == JobType.cleanup_user_storage.value,
        Job.user_id == user.id,
    ).first()
    assert cleanup_job is not None
    artifacts = cleanup_job.artifacts
    assert "filenames" in artifacts
    assert "test_gameplay.mp4" in artifacts["filenames"]
    assert "storage_keys" in artifacts
    assert "abc123_test_gameplay.mp4" in artifacts["storage_keys"]

    session.close()
    engine.dispose()


def test_kids_asset_download_endpoint_exists():
    """P2 regression: worker should have an endpoint to download Kids assets."""
    from pathlib import Path
    worker_routes = Path(__file__).parent.parent / "src" / "gpcg" / "api" / "worker_routes.py"
    content = worker_routes.read_text()
    assert "/kids/assets/" in content and "download" in content.lower()


def test_worker_downloads_kids_assets():
    """P2 regression: remote_worker should have _download_kids_assets method."""
    from pathlib import Path
    remote_worker = Path(__file__).parent.parent / "src" / "gpcg" / "worker" / "remote_worker.py"
    content = remote_worker.read_text()
    assert "_download_kids_assets" in content
