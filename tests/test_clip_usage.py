"""Tests for clip usage tracking, user-scoped gameplay selection, and public fallback."""
import random
import pytest

from gpcg.application.clip_usage_service import (
    UsedRange,
    get_used_ranges,
    find_available_segment,
    record_clip_usage,
    release_clip_usage,
    is_range_available,
)
from gpcg.application.gameplay_selector import GameplaySelector
from gpcg.core.models import User, Video, VideoStatus
from gpcg.domains.games.models import (
    GameplayAsset,
    GameplayClipUsage,
    GameplaySource,
    Game,
)
from gpcg.infrastructure.database import session_scope, init_db
from gpcg.domain.game_repository import get_or_create


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("GPCG_DB_PATH", str(db_path))
    monkeypatch.setenv("GPCG_DATA_DIR", str(tmp_path))
    from gpcg.config import get_settings
    get_settings.cache_clear()
    from gpcg.infrastructure import database
    database._engine = None
    database._SessionLocal = None
    init_db()
    yield
    get_settings.cache_clear()
    database._engine = None
    database._SessionLocal = None


def _make_user(session, email="test@example.com", uid=1):
    user = session.get(User, uid)
    if user is None:
        user = User(id=uid, email=email, name="Test User")
        session.add(user)
        session.flush()
    return user


def _make_source(session, game_id, user_id=None, duration=120.0, is_public=False, source_id=None):
    src = GameplaySource(
        game_id=game_id,
        user_id=user_id,
        file_path=f"/tmp/test_{game_id}_{user_id}_{id(session)}.mp4",
        filename=f"test_{game_id}.mp4",
        file_hash=f"hash_{game_id}_{user_id}_{id(session)}_{duration}",
        duration=duration,
        width=1920,
        height=1080,
        ingestion_status="ready",
        is_public=is_public,
    )
    if source_id:
        src.id = source_id
    session.add(src)
    session.flush()
    return src


def _make_asset(session, source_id, start_sec, end_sec, label=None):
    asset = GameplayAsset(
        source_id=source_id,
        start_sec=start_sec,
        end_sec=end_sec,
        duration=end_sec - start_sec,
        label=label,
    )
    session.add(asset)
    session.flush()
    return asset


def _make_video(session, user_id=1, video_id=None):
    video = Video(
        user_id=user_id,
        file_path=f"/tmp/video_{id(session)}.mp4",
        status=VideoStatus.pending.value,
    )
    if video_id:
        video.id = video_id
    session.add(video)
    session.flush()
    return video


# ── Unit tests for find_available_segment ────────────────────────────────────


class TestFindAvailableSegment:
    def test_no_used_ranges_returns_segment(self):
        rng = random.Random(42)
        seg = find_available_segment(100.0, 10.0, [], rng=rng)
        assert seg is not None
        start, end = seg
        assert end - start == 10.0
        assert 0 <= start <= 90

    def test_avoids_used_range(self):
        rng = random.Random(42)
        used = [UsedRange(start_sec=0.0, end_sec=50.0)]
        seg = find_available_segment(100.0, 10.0, used, rng=rng)
        assert seg is not None
        start, end = seg
        assert start >= 50.0  # Must be after the used range

    def test_fits_in_gap_between_used_ranges(self):
        rng = random.Random(42)
        used = [
            UsedRange(start_sec=0.0, end_sec=20.0),
            UsedRange(start_sec=80.0, end_sec=100.0),
        ]
        seg = find_available_segment(100.0, 30.0, used, rng=rng)
        assert seg is not None
        start, end = seg
        assert start >= 20.0
        assert end <= 80.0

    def test_returns_none_when_no_gap_large_enough(self):
        rng = random.Random(42)
        used = [
            UsedRange(start_sec=0.0, end_sec=50.0),
            UsedRange(start_sec=55.0, end_sec=100.0),
        ]
        # Only gap is 50-55 (5s), need 10s
        seg = find_available_segment(100.0, 10.0, used, rng=rng)
        assert seg is None

    def test_returns_none_when_source_too_short(self):
        seg = find_available_segment(5.0, 10.0, [])
        assert seg is None


class TestIsRangeAvailable:
    def test_available_when_no_used_ranges(self):
        assert is_range_available([], 0.0, 10.0) is True

    def test_unavailable_when_overlapping(self):
        used = [UsedRange(start_sec=5.0, end_sec=15.0)]
        assert is_range_available(used, 0.0, 10.0) is False

    def test_available_when_non_overlapping(self):
        used = [UsedRange(start_sec=20.0, end_sec=30.0)]
        assert is_range_available(used, 0.0, 10.0) is True

    def test_available_within_tolerance(self):
        used = [UsedRange(start_sec=10.0, end_sec=20.0)]
        # Overlap of 0.5s is within tolerance=1.0
        assert is_range_available(used, 0.0, 10.5, tolerance=1.0) is True


# ── Integration tests for record/release clip usage ──────────────────────────


class TestClipUsageRecording:
    def test_record_and_retrieve(self):
        with session_scope() as s:
            game = get_or_create(s, "Bully")
            src = _make_source(s, game.id, duration=100.0)
            video = _make_video(s)
            record_clip_usage(s, video.id, src.id, 10.0, 25.0)
            s.flush()

        with session_scope() as s:
            ranges = get_used_ranges(s, src.id)
            assert len(ranges) == 1
            assert ranges[0].start_sec == 10.0
            assert ranges[0].end_sec == 25.0

    def test_release_clip_usage(self):
        with session_scope() as s:
            game = get_or_create(s, "Bully")
            src = _make_source(s, game.id, duration=100.0)
            video = _make_video(s)
            record_clip_usage(s, video.id, src.id, 10.0, 25.0)
            record_clip_usage(s, video.id, src.id, 30.0, 45.0)
            s.flush()
            count = release_clip_usage(s, video.id)
            assert count == 2

        with session_scope() as s:
            ranges = get_used_ranges(s, src.id)
            assert len(ranges) == 0


# ── Integration tests for user-scoped gameplay selection ─────────────────────


class TestUserScopedSelection:
    def test_select_filters_by_user_id(self):
        """GameplaySelector should only select from the specified user's sources."""
        with session_scope() as s:
            game = get_or_create(s, "Bully")
            user1 = _make_user(s, "user1@test.com", uid=1)
            user2 = _make_user(s, "user2@test.com", uid=2)
            src1 = _make_source(s, game.id, user_id=user1.id, duration=100.0)
            src2 = _make_source(s, game.id, user_id=user2.id, duration=100.0)
            _make_asset(s, src1.id, 0, 30)
            _make_asset(s, src2.id, 0, 30)
            s.flush()

        selector = GameplaySelector()
        rng = random.Random(42)
        with session_scope() as s:
            # User 1 should only get clips from src1
            clips = selector.select(s, game.id, target_duration=20.0, rng=rng, user_id=1)
            assert len(clips) > 0
            for clip in clips:
                assert clip.asset.source_id == src1.id

    def test_select_returns_empty_for_user_without_gameplay(self):
        """User with no gameplay should get empty list (no fallback)."""
        with session_scope() as s:
            game = get_or_create(s, "Bully")
            user1 = _make_user(s, "user1@test.com", uid=1)
            user2 = _make_user(s, "user2@test.com", uid=2)
            src1 = _make_source(s, game.id, user_id=user1.id, duration=100.0)
            _make_asset(s, src1.id, 0, 30)
            s.flush()

        selector = GameplaySelector()
        rng = random.Random(42)
        with session_scope() as s:
            clips = selector.select(s, game.id, target_duration=20.0, rng=rng, user_id=2)
            assert clips == []

    def test_public_fallback_when_user_has_no_gameplay(self):
        """When accept_public=True and user has no gameplay, fall back to public sources."""
        with session_scope() as s:
            game = get_or_create(s, "Bully")
            user1 = _make_user(s, "user1@test.com", uid=1)
            user2 = _make_user(s, "user2@test.com", uid=2)
            # user1's source is public
            src1 = _make_source(s, game.id, user_id=user1.id, duration=100.0, is_public=True)
            _make_asset(s, src1.id, 0, 30)
            s.flush()

        selector = GameplaySelector()
        rng = random.Random(42)
        with session_scope() as s:
            # user2 has no gameplay, but accept_public=True should use user1's public source
            clips = selector.select(
                s, game.id, target_duration=20.0, rng=rng,
                user_id=2, accept_public=True,
            )
            assert len(clips) > 0
            for clip in clips:
                assert clip.asset.source_id == src1.id

    def test_no_public_fallback_when_disabled(self):
        """When accept_public=False, should NOT fall back to public sources."""
        with session_scope() as s:
            game = get_or_create(s, "Bully")
            user1 = _make_user(s, "user1@test.com", uid=1)
            user2 = _make_user(s, "user2@test.com", uid=2)
            src1 = _make_source(s, game.id, user_id=user1.id, duration=100.0, is_public=True)
            _make_asset(s, src1.id, 0, 30)
            s.flush()

        selector = GameplaySelector()
        rng = random.Random(42)
        with session_scope() as s:
            clips = selector.select(
                s, game.id, target_duration=20.0, rng=rng,
                user_id=2, accept_public=False,
            )
            assert clips == []

    def test_private_sources_not_used_as_fallback(self):
        """Private sources from other users should NOT be used as fallback."""
        with session_scope() as s:
            game = get_or_create(s, "Bully")
            user1 = _make_user(s, "user1@test.com", uid=1)
            user2 = _make_user(s, "user2@test.com", uid=2)
            # user1's source is PRIVATE
            src1 = _make_source(s, game.id, user_id=user1.id, duration=100.0, is_public=False)
            _make_asset(s, src1.id, 0, 30)
            s.flush()

        selector = GameplaySelector()
        rng = random.Random(42)
        with session_scope() as s:
            clips = selector.select(
                s, game.id, target_duration=20.0, rng=rng,
                user_id=2, accept_public=True,
            )
            assert clips == []


class TestClipUsageAvoidance:
    def test_selector_avoids_used_ranges(self):
        """GameplaySelector should avoid time ranges already used in previous videos."""
        with session_scope() as s:
            game = get_or_create(s, "Bully")
            user = _make_user(s, uid=1)
            src = _make_source(s, game.id, user_id=1, duration=100.0)
            _make_asset(s, src.id, 0, 100.0)
            video = _make_video(s)
            # Mark first 50s as used
            record_clip_usage(s, video.id, src.id, 0.0, 50.0, consumer_user_id=1)
            s.flush()

        selector = GameplaySelector()
        rng = random.Random(42)
        with session_scope() as s:
            clips = selector.select(s, game.id, target_duration=20.0, rng=rng, user_id=1)
            assert len(clips) > 0
            # All clips should be in the second half (after 50s)
            for clip in clips:
                assert clip.start_sec >= 50.0, f"clip at {clip.start_sec} overlaps used range"
