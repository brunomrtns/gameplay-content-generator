"""Tests for the Kids media library refactor.

Covers:
- StoryAsset with nullable topic_id (channel library, not topic-owned)
- AssetClipUsage model
- KidsMediaRetriever semantic selection
- Library upload endpoint (POST /kids/assets/upload)
- Library listing endpoint (GET /kids/assets)
- Asset patch endpoint (PATCH /kids/assets/{id})
- Removal of /kids/generate endpoint
- GenerationService domain branch (Kids vs Games)
"""

from __future__ import annotations

import io
import pytest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from gpcg.core.models import (
    Base,
    ChannelProfile,
    ContentDomain,
    User,
    Automation,
    Job,
    JobStatus,
    JobType,
)
from gpcg.domains.kids.models import (
    KidsTopic,
    StoryAsset,
    AssetProcessingStatus,
    AssetMediaKind,
    AssetClipUsage,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def db_session():
    """In-memory SQLite DB with all tables."""
    import gpcg.core.models  # noqa: F401
    import gpcg.domains.games.models  # noqa: F401
    import gpcg.domains.kids.models  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = Session(engine)
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def user_with_kids(db_session):
    """Create a user with Kids domain."""
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
def client(db_session, user_with_kids):
    """FastAPI TestClient with mocked auth and DB."""
    from fastapi.testclient import TestClient
    from gpcg.api.app import create_app

    user = db_session.query(User).filter(User.id == user_with_kids).first()

    with patch("gpcg.api.app.init_db", return_value=None):
        app = create_app()

    from gpcg.infrastructure.auth import get_current_user
    from gpcg.infrastructure.database import get_db

    def override_auth():
        return user

    def override_db():
        yield db_session

    app.dependency_overrides[get_current_user] = override_auth
    app.dependency_overrides[get_db] = override_db

    c = TestClient(app)
    yield c
    app.dependency_overrides.clear()


# ── Model tests ──────────────────────────────────────────────────────────────


class TestStoryAssetModel:
    """StoryAsset model tests — nullable topic_id, tags, description, is_public."""

    def test_asset_without_topic_id(self, db_session, user_with_kids):
        """StoryAsset can be created without topic_id (channel library)."""
        asset = StoryAsset(
            user_id=user_with_kids,
            topic_id=None,  # library asset
            filename="test.png",
            storage_key="test/test.png",
            processing_status=AssetProcessingStatus.ready.value,
        )
        db_session.add(asset)
        db_session.commit()
        assert asset.id is not None
        assert asset.topic_id is None
        assert asset.tags == []
        assert asset.description == ""
        assert asset.is_public is False

    def test_asset_with_tags_and_description(self, db_session, user_with_kids):
        """StoryAsset can have tags and description for semantic selection."""
        asset = StoryAsset(
            user_id=user_with_kids,
            topic_id=None,
            filename="dino.png",
            storage_key="test/dino.png",
            processing_status=AssetProcessingStatus.ready.value,
            tags=["dinosaur", "nature", "green"],
            description="A green dinosaur in a forest",
            is_public=True,
        )
        db_session.add(asset)
        db_session.commit()
        assert asset.tags == ["dinosaur", "nature", "green"]
        assert asset.description == "A green dinosaur in a forest"
        assert asset.is_public is True

    def test_asset_with_topic_id_still_works(self, db_session, user_with_kids):
        """StoryAsset can still be linked to a topic (optional association)."""
        topic = KidsTopic(
            user_id=user_with_kids,
            title="Dinosaurs",
            slug="dinosaurs",
            category="animals",
            age_range="3-6",
        )
        db_session.add(topic)
        db_session.flush()
        asset = StoryAsset(
            user_id=user_with_kids,
            topic_id=topic.id,
            filename="dino.png",
            storage_key="test/dino.png",
            processing_status=AssetProcessingStatus.ready.value,
        )
        db_session.add(asset)
        db_session.commit()
        assert asset.topic_id == topic.id


class TestAssetClipUsageModel:
    """AssetClipUsage model tests."""

    def test_create_clip_usage(self, db_session, user_with_kids):
        """AssetClipUsage can track which video used which asset segment."""
        from gpcg.core.models import Video

        asset = StoryAsset(
            user_id=user_with_kids,
            topic_id=None,
            filename="test.mp4",
            storage_key="test/test.mp4",
            media_kind=AssetMediaKind.video.value,
            duration=60.0,
            processing_status=AssetProcessingStatus.ready.value,
        )
        db_session.add(asset)
        db_session.flush()

        video = Video(
            user_id=user_with_kids,
            status="completed",
        )
        db_session.add(video)
        db_session.flush()

        usage = AssetClipUsage(
            video_id=video.id,
            asset_id=asset.id,
            consumer_user_id=user_with_kids,
            start_sec=10.0,
            end_sec=20.0,
            duration=10.0,
        )
        db_session.add(usage)
        db_session.commit()
        assert usage.id is not None
        assert usage.video_id == video.id
        assert usage.asset_id == asset.id


# ── KidsMediaRetriever tests ─────────────────────────────────────────────────


class TestKidsMediaRetriever:
    """KidsMediaRetriever semantic selection tests."""

    def _make_asset(self, session, user_id, filename, *, tags=None, description="",
                    media_kind=AssetMediaKind.image.value, duration=0.0,
                    topic_id=None, used_count=0):
        """Helper to create a ready StoryAsset."""
        metadata = {"used_count": used_count} if used_count else {}
        asset = StoryAsset(
            user_id=user_id,
            topic_id=topic_id,
            filename=filename,
            storage_key=f"test/{filename}",
            media_kind=media_kind,
            duration=duration,
            processing_status=AssetProcessingStatus.ready.value,
            tags=tags or [],
            description=description,
            metadata_json=metadata,
        )
        session.add(asset)
        session.flush()
        return asset

    def test_retrieve_empty_library_returns_empty(self, db_session, user_with_kids):
        """Retriever returns empty list when no assets exist."""
        from gpcg.application.kids_media_retriever import KidsMediaRetriever
        retriever = KidsMediaRetriever()
        clips = retriever.retrieve(db_session, user_with_kids, target_duration=30.0)
        assert clips == []

    def test_retrieve_no_ready_assets_returns_empty(self, db_session, user_with_kids):
        """Retriever returns empty list when assets are not ready."""
        asset = StoryAsset(
            user_id=user_with_kids,
            topic_id=None,
            filename="test.png",
            storage_key="test/test.png",
            processing_status=AssetProcessingStatus.queued.value,
        )
        db_session.add(asset)
        db_session.commit()

        from gpcg.application.kids_media_retriever import KidsMediaRetriever
        retriever = KidsMediaRetriever()
        clips = retriever.retrieve(db_session, user_with_kids, target_duration=30.0)
        assert clips == []

    def test_retrieve_semantic_tag_match(self, db_session, user_with_kids):
        """Retriever prioritizes assets whose tags match the query."""
        self._make_asset(db_session, user_with_kids, "dino.png",
                         tags=["dinosaur", "nature"])
        self._make_asset(db_session, user_with_kids, "space.png",
                         tags=["space", "stars"])
        db_session.commit()

        from gpcg.application.kids_media_retriever import KidsMediaRetriever
        retriever = KidsMediaRetriever()
        clips = retriever.retrieve(
            db_session, user_with_kids, target_duration=5.0,
            topic_title="dinosaur adventure",
        )
        assert len(clips) > 0
        # The dinosaur asset should be selected first (tag match)
        assert clips[0].asset.filename == "dino.png"
        assert "semantic" in clips[0].selection_reason

    def test_retrieve_topic_scoped_bonus(self, db_session, user_with_kids):
        """Assets linked to the topic get priority."""
        topic = KidsTopic(
            user_id=user_with_kids, title="Dinosaurs", slug="dinosaurs",
            category="animals", age_range="3-6",
        )
        db_session.add(topic)
        db_session.flush()

        # Asset linked to topic (no tag match)
        self._make_asset(db_session, user_with_kids, "topic_asset.png",
                         topic_id=topic.id, tags=["random"])
        # Asset not linked to topic (no tag match)
        self._make_asset(db_session, user_with_kids, "library_asset.png",
                         tags=["other"])
        db_session.commit()

        from gpcg.application.kids_media_retriever import KidsMediaRetriever
        retriever = KidsMediaRetriever()
        clips = retriever.retrieve(
            db_session, user_with_kids, target_duration=5.0,
            topic_id=topic.id, topic_title="something unrelated",
        )
        assert len(clips) > 0
        # Topic-scoped asset should be selected first
        assert clips[0].asset.filename == "topic_asset.png"
        assert clips[0].selection_reason == "topic_scoped"

    def test_retrieve_random_fallback(self, db_session, user_with_kids):
        """When no semantic match, retriever falls back to random selection."""
        self._make_asset(db_session, user_with_kids, "asset1.png", tags=["foo"])
        self._make_asset(db_session, user_with_kids, "asset2.png", tags=["bar"])
        db_session.commit()

        from gpcg.application.kids_media_retriever import KidsMediaRetriever
        import random
        retriever = KidsMediaRetriever()
        clips = retriever.retrieve(
            db_session, user_with_kids, target_duration=5.0,
            topic_title="completely unrelated topic",
            rng=random.Random(42),
        )
        assert len(clips) > 0
        # Should still select something (random fallback)
        assert clips[0].selection_reason in ("random_fallback", "semantic_tag_match",
                                              "semantic_description_match")

    def test_retrieve_fills_target_duration(self, db_session, user_with_kids):
        """Retriever fills the target duration with image clips."""
        # 3 images, each will be displayed for 5s
        for i in range(3):
            self._make_asset(db_session, user_with_kids, f"img{i}.png",
                             tags=["nature"])
        db_session.commit()

        from gpcg.application.kids_media_retriever import KidsMediaRetriever
        retriever = KidsMediaRetriever()
        clips = retriever.retrieve(
            db_session, user_with_kids, target_duration=12.0,
            topic_title="nature",
        )
        total = sum(c.duration for c in clips)
        assert total >= 10.0  # should fill at least 10s of 12s target

    def test_retrieve_video_segment_selection(self, db_session, user_with_kids):
        """Video assets get temporal segments (start_sec, end_sec)."""
        self._make_asset(db_session, user_with_kids, "video.mp4",
                         media_kind=AssetMediaKind.video.value, duration=30.0,
                         tags=["nature"])
        db_session.commit()

        from gpcg.application.kids_media_retriever import KidsMediaRetriever
        retriever = KidsMediaRetriever()
        clips = retriever.retrieve(
            db_session, user_with_kids, target_duration=10.0,
            topic_title="nature",
        )
        assert len(clips) > 0
        clip = clips[0]
        assert clip.asset.media_kind == AssetMediaKind.video.value
        assert clip.start_sec >= 0
        assert clip.end_sec > clip.start_sec
        assert clip.duration > 0

    def test_retrieve_respects_clip_usage(self, db_session, user_with_kids):
        """Video segments avoid already-used ranges."""
        from gpcg.core.models import Video

        asset = self._make_asset(db_session, user_with_kids, "video.mp4",
                                  media_kind=AssetMediaKind.video.value, duration=30.0,
                                  tags=["nature"])
        db_session.flush()

        video = Video(user_id=user_with_kids, status="completed")
        db_session.add(video)
        db_session.flush()

        # Mark 0-15s as used
        usage = AssetClipUsage(
            video_id=video.id, asset_id=asset.id,
            consumer_user_id=user_with_kids,
            start_sec=0.0, end_sec=15.0, duration=15.0,
        )
        db_session.add(usage)
        db_session.commit()

        from gpcg.application.kids_media_retriever import KidsMediaRetriever
        retriever = KidsMediaRetriever()
        clips = retriever.retrieve(
            db_session, user_with_kids, target_duration=10.0,
            topic_title="nature",
        )
        assert len(clips) > 0
        clip = clips[0]
        # Should select from the unused 15-30s range
        assert clip.start_sec >= 15.0

    def test_retrieve_public_fallback(self, db_session, user_with_kids):
        """When user has no assets, retriever can fall back to public assets."""
        # Create a public asset from a different user
        other_user = User(email="other@example.com")
        db_session.add(other_user)
        db_session.flush()
        self._make_asset(db_session, other_user.id, "public.png",
                         tags=["nature"], description="public asset")
        asset = db_session.query(StoryAsset).filter(
            StoryAsset.filename == "public.png"
        ).first()
        asset.is_public = True
        db_session.commit()

        from gpcg.application.kids_media_retriever import KidsMediaRetriever
        retriever = KidsMediaRetriever()
        clips = retriever.retrieve(
            db_session, user_with_kids, target_duration=5.0,
            topic_title="nature", accept_public=True,
        )
        assert len(clips) > 0
        assert clips[0].asset.filename == "public.png"


# ── API tests ────────────────────────────────────────────────────────────────


class TestKidsMediaLibraryAPI:
    """Kids media library API endpoint tests."""

    def test_upload_library_image(self, client, db_session, user_with_kids):
        """POST /kids/assets/upload creates a library asset without topic_id."""
        # Create a small PNG in memory
        from PIL import Image
        img = Image.new("RGB", (100, 100), color="red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        resp = client.post(
            "/api/kids/assets/upload",
            files={"file": ("test.png", buf, "image/png")},
            data={"tags": "nature,forest", "description": "A forest scene"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["media_kind"] == "image"
        assert data["processing_status"] == "ready"
        assert data["topic_id"] is None  # library asset
        assert "nature" in data["tags"]
        assert "forest" in data["tags"]
        assert data["description"] == "A forest scene"

    def test_upload_library_image_with_topic(self, client, db_session, user_with_kids):
        """POST /kids/assets/upload with topic_id links asset to topic."""
        # Create a topic first
        topic = KidsTopic(
            user_id=user_with_kids, title="Forest", slug="forest",
            category="nature", age_range="3-6",
        )
        db_session.add(topic)
        db_session.commit()

        from PIL import Image
        img = Image.new("RGB", (100, 100), color="green")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        resp = client.post(
            "/api/kids/assets/upload",
            files={"file": ("forest.png", buf, "image/png")},
            data={"topic_id": str(topic.id), "tags": "forest"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["topic_id"] == topic.id

    def test_upload_dedup(self, client, db_session, user_with_kids):
        """Uploading the same file twice returns 409."""
        from PIL import Image
        img = Image.new("RGB", (100, 100), color="blue")
        buf1 = io.BytesIO()
        img.save(buf1, format="PNG")
        buf1.seek(0)
        buf2 = io.BytesIO()
        img.save(buf2, format="PNG")
        buf2.seek(0)

        resp1 = client.post(
            "/api/kids/assets/upload",
            files={"file": ("dup.png", buf1, "image/png")},
        )
        assert resp1.status_code == 200

        resp2 = client.post(
            "/api/kids/assets/upload",
            files={"file": ("dup.png", buf2, "image/png")},
        )
        assert resp2.status_code == 409

    def test_list_library_assets(self, client, db_session, user_with_kids):
        """GET /kids/assets lists all library assets."""
        for i in range(3):
            asset = StoryAsset(
                user_id=user_with_kids, topic_id=None,
                filename=f"img{i}.png", storage_key=f"test/img{i}.png",
                processing_status=AssetProcessingStatus.ready.value,
                tags=[f"tag{i}"],
            )
            db_session.add(asset)
        db_session.commit()

        resp = client.get("/api/kids/assets")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["assets"]) == 3
        # Check tags are returned
        assert data["assets"][0]["tags"] is not None

    def test_list_library_assets_filter_by_kind(self, client, db_session, user_with_kids):
        """GET /kids/assets?media_kind=image filters by media kind."""
        db_session.add(StoryAsset(
            user_id=user_with_kids, topic_id=None,
            filename="img.png", storage_key="test/img.png",
            media_kind=AssetMediaKind.image.value,
            processing_status=AssetProcessingStatus.ready.value,
        ))
        db_session.add(StoryAsset(
            user_id=user_with_kids, topic_id=None,
            filename="vid.mp4", storage_key="test/vid.mp4",
            media_kind=AssetMediaKind.video.value,
            processing_status=AssetProcessingStatus.queued.value,
        ))
        db_session.commit()

        resp = client.get("/api/kids/assets?media_kind=image")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["assets"]) == 1
        assert data["assets"][0]["media_kind"] == "image"

    def test_patch_asset_tags(self, client, db_session, user_with_kids):
        """PATCH /kids/assets/{id} updates tags."""
        asset = StoryAsset(
            user_id=user_with_kids, topic_id=None,
            filename="img.png", storage_key="test/img.png",
            processing_status=AssetProcessingStatus.ready.value,
        )
        db_session.add(asset)
        db_session.commit()

        resp = client.patch(f"/api/kids/assets/{asset.id}", json={
            "tags": ["new_tag", "another"],
            "description": "Updated description",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["tags"] == ["new_tag", "another"]
        assert data["description"] == "Updated description"

    def test_patch_asset_visibility(self, client, db_session, user_with_kids):
        """PATCH /kids/assets/{id} can toggle is_public."""
        asset = StoryAsset(
            user_id=user_with_kids, topic_id=None,
            filename="img.png", storage_key="test/img.png",
            processing_status=AssetProcessingStatus.ready.value,
        )
        db_session.add(asset)
        db_session.commit()

        resp = client.patch(f"/api/kids/assets/{asset.id}", json={
            "is_public": True,
        })
        assert resp.status_code == 200
        assert resp.json()["is_public"] is True

    def test_patch_asset_unlink_topic(self, client, db_session, user_with_kids):
        """PATCH /kids/assets/{id} with topic_id=0 unlinks from topic."""
        topic = KidsTopic(
            user_id=user_with_kids, title="T", slug="t",
            category="nature", age_range="3-6",
        )
        db_session.add(topic)
        db_session.flush()
        asset = StoryAsset(
            user_id=user_with_kids, topic_id=topic.id,
            filename="img.png", storage_key="test/img.png",
            processing_status=AssetProcessingStatus.ready.value,
        )
        db_session.add(asset)
        db_session.commit()

        resp = client.patch(f"/api/kids/assets/{asset.id}", json={
            "topic_id": 0,  # unlink
        })
        assert resp.status_code == 200
        assert resp.json()["topic_id"] is None

    def test_generate_endpoint_removed(self, client, db_session, user_with_kids):
        """POST /kids/generate returns 405 (endpoint removed)."""
        resp = client.post("/api/kids/generate", json={"topic_id": 1})
        assert resp.status_code == 405  # Method Not Allowed

    def test_delete_asset(self, client, db_session, user_with_kids):
        """DELETE /kids/assets/{id} removes the asset."""
        asset = StoryAsset(
            user_id=user_with_kids, topic_id=None,
            filename="img.png", storage_key="test/img.png",
            processing_status=AssetProcessingStatus.ready.value,
        )
        db_session.add(asset)
        db_session.commit()
        asset_id = asset.id

        resp = client.delete(f"/api/kids/assets/{asset_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_legacy_topic_upload_still_works(self, client, db_session, user_with_kids):
        """POST /kids/topics/{id}/assets (legacy) still works for backward compat."""
        topic = KidsTopic(
            user_id=user_with_kids, title="T", slug="t",
            category="nature", age_range="3-6",
        )
        db_session.add(topic)
        db_session.commit()

        from PIL import Image
        img = Image.new("RGB", (50, 50), color="red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        resp = client.post(
            f"/api/kids/topics/{topic.id}/assets",
            files={"file": ("test.png", buf, "image/png")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["topic_id"] == topic.id or data.get("processing_status") == "ready"


# ── Ownership isolation tests ────────────────────────────────────────────────


class TestKidsMediaOwnership:
    """Verify ownership isolation — users can't see/modify other users' assets."""

    def test_list_only_own_assets(self, db_session):
        """GET /kids/assets only returns the current user's assets."""
        user1 = User(email="u1@example.com")
        user2 = User(email="u2@example.com")
        db_session.add_all([user1, user2])
        db_session.flush()

        for u in [user1, user2]:
            profile = ChannelProfile(
                user_id=u.id, domain=ContentDomain.kids.value, niche="test"
            )
            db_session.add(profile)

        db_session.add(StoryAsset(
            user_id=user1.id, topic_id=None,
            filename="u1.png", storage_key="test/u1.png",
            processing_status=AssetProcessingStatus.ready.value,
        ))
        db_session.add(StoryAsset(
            user_id=user2.id, topic_id=None,
            filename="u2.png", storage_key="test/u2.png",
            processing_status=AssetProcessingStatus.ready.value,
        ))
        db_session.commit()

        from gpcg.application.kids_media_retriever import KidsMediaRetriever
        retriever = KidsMediaRetriever()
        clips = retriever.retrieve(db_session, user1.id, target_duration=5.0)
        assert len(clips) > 0
        assert all(c.asset.user_id == user1.id for c in clips)
