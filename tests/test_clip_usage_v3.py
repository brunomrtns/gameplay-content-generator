"""Tests for V3 configurable reuse policy, gameplay preference, and overlap-based eligibility.

Covers all scenarios from spec item #22:
- max_uses=1, 2, unlimited
- Overlap detection (significant vs minor)
- Per-consumer tracking
- Event-aware selection
- Auditability fields
"""
import random
import pytest

from gpcg.application.clip_usage_service import (
    UsedRange,
    count_overlapping_uses,
    is_range_eligible,
    is_range_available,
    find_available_segment,
    estimate_availability,
)
from gpcg.application.gameplay_selector import GameplaySelector, SelectedClip
from gpcg.core.models import User, Video, VideoStatus
from gpcg.domains.games.models import (
    GameplayAsset,
    GameplayClipUsage,
    GameplayEvent,
    GameplaySource,
)
from gpcg.infrastructure.database import session_scope, init_db
from gpcg.domain.game_repository import get_or_create


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_v3.db"
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


def _make_source(session, game_id, user_id=None, duration=300.0, is_public=False):
    src = GameplaySource(
        game_id=game_id,
        user_id=user_id,
        file_path=f"/tmp/test_v3_{game_id}_{user_id}_{id(session)}.mp4",
        filename=f"test_v3_{game_id}.mp4",
        file_hash=f"hash_v3_{game_id}_{user_id}_{id(session)}",
        duration=duration,
        width=1920,
        height=1080,
        ingestion_status="ready",
        is_public=is_public,
    )
    session.add(src)
    session.flush()
    return src


def _make_asset(session, source_id, start_sec=0.0, end_sec=300.0):
    asset = GameplayAsset(
        source_id=source_id,
        start_sec=start_sec,
        end_sec=end_sec,
        duration=end_sec - start_sec,
    )
    session.add(asset)
    session.flush()
    return asset


def _make_event(session, source_id, start, end, event_type="COMBAT", score=0.8):
    ev = GameplayEvent(
        source_id=source_id,
        start_time=start,
        end_time=end,
        event_type=event_type,
        interesting_score=score,
        visual_confidence=0.9,
    )
    session.add(ev)
    session.flush()
    return ev


# ── Unit tests: count_overlapping_uses ───────────────────────────────────────


class TestCountOverlappingUses:
    def test_no_overlap(self):
        used = [UsedRange(100, 120)]
        assert count_overlapping_uses(used, 200, 220) == 0

    def test_significant_overlap(self):
        used = [UsedRange(100, 120)]
        # candidate 105-125 overlaps 15s with used 100-120
        assert count_overlapping_uses(used, 105, 125) == 1

    def test_minor_overlap_below_tolerance(self):
        used = [UsedRange(100, 120)]
        # candidate 119.5-140 overlaps 0.5s with used 100-120 (< 1s tolerance)
        assert count_overlapping_uses(used, 119.5, 140) == 0

    def test_multiple_overlaps(self):
        used = [UsedRange(100, 120), UsedRange(110, 130)]
        # candidate 115-125 overlaps both
        assert count_overlapping_uses(used, 115, 125) == 2

    def test_exact_same_range(self):
        used = [UsedRange(100, 120)]
        assert count_overlapping_uses(used, 100, 120) == 1

    def test_empty_used(self):
        assert count_overlapping_uses([], 100, 120) == 0


# ── Unit tests: is_range_eligible with max_uses ──────────────────────────────


class TestIsRangeEligible:
    def test_max_uses_1_blocks_after_one_use(self):
        """Limite 1: região usada uma vez não fica elegível novamente."""
        used = [UsedRange(100, 120)]
        # Same region — should be blocked with max_uses=1
        assert not is_range_eligible(used, 100, 120, max_uses=1)

    def test_max_uses_2_allows_one_reuse(self):
        """Limite 2: região usada uma vez continua elegível."""
        used = [UsedRange(100, 120)]
        # Same region — should still be eligible with max_uses=2
        assert is_range_eligible(used, 105, 115, max_uses=2)

    def test_max_uses_2_blocks_after_second_use(self):
        """Limite 2: depois da segunda utilização relevante, fica bloqueado."""
        used = [UsedRange(100, 120), UsedRange(105, 125)]
        # Two overlapping uses — should be blocked with max_uses=2
        assert not is_range_eligible(used, 110, 115, max_uses=2)

    def test_unlimited_always_eligible(self):
        """Ilimitado: reutilização não é bloqueada por quota."""
        used = [UsedRange(100, 120), UsedRange(100, 120), UsedRange(100, 120)]
        assert is_range_eligible(used, 100, 120, max_uses=0)

    def test_minor_overlap_eligible_even_with_max_1(self):
        """Overlap mínimo não bloqueia tudo (§11)."""
        used = [UsedRange(100, 120)]
        # candidate 119.5-140 — 0.5s overlap < 1s tolerance
        assert is_range_eligible(used, 119.5, 140, max_uses=1)

    def test_backward_compat_is_range_available(self):
        """is_range_available is alias for is_range_eligible(max_uses=1)."""
        used = [UsedRange(100, 120)]
        assert is_range_available(used, 105, 115) == is_range_eligible(used, 105, 115, max_uses=1)
        assert is_range_available(used, 200, 220) == is_range_eligible(used, 200, 220, max_uses=1)


# ── Unit tests: find_available_segment with event_boundaries ─────────────────


class TestFindAvailableSegmentEventAware:
    def test_prefers_event_boundary(self):
        """When event_boundaries provided, prefers event-aligned starts."""
        rng = random.Random(42)
        used = [UsedRange(0, 50)]  # First 50s used
        events = [(100, 130), (130, 160), (160, 190)]
        seg = find_available_segment(
            300, 20, used, rng=rng, event_boundaries=events, max_uses=1,
        )
        assert seg is not None
        start, end = seg
        # Should align with an event boundary
        event_starts = [e[0] for e in events]
        assert start in event_starts or any(abs(start - es) < 0.1 for es in event_starts)

    def test_fallback_to_gap_when_no_events_fit(self):
        """When no event boundaries fit, falls back to gap-based selection."""
        rng = random.Random(42)
        used = [UsedRange(0, 50)]
        events = [(10, 15)]  # Too small for needed_duration=20
        seg = find_available_segment(
            300, 20, used, rng=rng, event_boundaries=events, max_uses=1,
        )
        assert seg is not None
        start, end = seg
        assert end - start >= 20

    def test_max_uses_2_finds_reused_event(self):
        """With max_uses=2, can select from events that have 1 use."""
        rng = random.Random(42)
        used = [UsedRange(100, 120)]  # Event at 100-130 has 1 use
        events = [(100, 130), (200, 230)]
        # With max_uses=2, the event at 100-130 should still be eligible
        seg = find_available_segment(
            300, 15, used, rng=rng, event_boundaries=events, max_uses=2,
        )
        assert seg is not None

    def test_no_events_random_fallback(self):
        """Without event_boundaries, uses random gap selection (backward compat)."""
        rng = random.Random(42)
        used = [UsedRange(0, 100)]
        seg = find_available_segment(300, 20, used, rng=rng)
        assert seg is not None
        start, end = seg
        assert start >= 100  # After used range


# ── Integration tests: per-consumer tracking ─────────────────────────────────


class TestPerConsumerTracking:
    def test_user_a_usage_doesnt_block_user_b(self):
        """Por consumidor: A esgota uma região pública, B continua podendo utilizar."""
        with session_scope() as session:
            user_a = _make_user(session, email="a@example.com", uid=1)
            user_b = _make_user(session, email="b@example.com", uid=2)
            game = get_or_create(session, "TestGame")
            src = _make_source(session, game.id, user_id=1, is_public=True)
            asset = _make_asset(session, src.id)
            video_a = Video(
                user_id=1, status=VideoStatus.ready.value,
                file_path="/tmp/a.mp4",
            )
            session.add(video_a)
            session.flush()

            # User A uses region 100-120
            from gpcg.application.clip_usage_service import record_clip_usage
            record_clip_usage(session, video_a.id, src.id, 100, 120, consumer_user_id=1)
            session.commit()

        # User B queries — should see NO used ranges for their consumer
        with session_scope() as session:
            from gpcg.application.clip_usage_service import get_used_ranges
            ranges_b = get_used_ranges(session, src.id, consumer_user_id=2)
            assert len(ranges_b) == 0

            # User A queries — should see the used range
            ranges_a = get_used_ranges(session, src.id, consumer_user_id=1)
            assert len(ranges_a) == 1
            assert ranges_a[0].start_sec == 100

    def test_user_a_exhausts_with_max_1_user_b_free(self):
        """A esgota região com max_uses=1, B continua livre para usar a mesma região."""
        with session_scope() as session:
            user_a = _make_user(session, email="a@example.com", uid=1)
            user_b = _make_user(session, email="b@example.com", uid=2)
            game = get_or_create(session, "TestGame2")
            src = _make_source(session, game.id, user_id=1, is_public=True)
            asset = _make_asset(session, src.id)
            video_a = Video(
                user_id=1, status=VideoStatus.ready.value,
                file_path="/tmp/a2.mp4",
            )
            session.add(video_a)
            session.flush()
            from gpcg.application.clip_usage_service import record_clip_usage
            record_clip_usage(session, video_a.id, src.id, 100, 120, consumer_user_id=1)
            session.commit()

        with session_scope() as session:
            from gpcg.application.clip_usage_service import get_used_ranges
            # A has used 100-120 — blocked for A with max_uses=1
            ranges_a = get_used_ranges(session, src.id, consumer_user_id=1)
            assert not is_range_eligible(ranges_a, 105, 115, max_uses=1)

            # B has no usage — region is free for B
            ranges_b = get_used_ranges(session, src.id, consumer_user_id=2)
            assert is_range_eligible(ranges_b, 105, 115, max_uses=1)


# ── Integration tests: SelectedClip auditability ────────────────────────────


class TestSelectedClipAuditability:
    def test_selected_clip_has_event_id_and_reason(self):
        """SelectedClip carries event_id and selection_reason for auditability."""
        clip = SelectedClip(
            asset=None,  # Not needed for this unit test
            source_path="/tmp/test.mp4",
            start_sec=100,
            end_sec=120,
            duration=20,
            scene_index=0,
            event_id=42,
            selection_reason="semantic_event",
            usage_count_at_selection=0,
        )
        assert clip.event_id == 42
        assert clip.selection_reason == "semantic_event"
        assert clip.usage_count_at_selection == 0

    def test_selected_clip_defaults(self):
        """SelectedClip defaults: event_id=None, reason='', usage_count=0."""
        clip = SelectedClip(
            asset=None,
            source_path="/tmp/test.mp4",
            start_sec=0,
            end_sec=10,
            duration=10,
        )
        assert clip.event_id is None
        assert clip.selection_reason == ""
        assert clip.usage_count_at_selection == 0


# ── Integration tests: GameplaySelector with max_uses ────────────────────────


class TestGameplaySelectorMaxUses:
    def test_max_uses_1_blocks_reuse(self):
        """Selector with max_uses=1 doesn't reuse already-used segments."""
        with session_scope() as session:
            user = _make_user(session, uid=1)
            game = get_or_create(session, "SelectorTest")
            src = _make_source(session, game.id, user_id=1, duration=200)
            asset = _make_asset(session, src.id, 0, 200)
            video = Video(
                user_id=1, status=VideoStatus.ready.value,
                file_path="/tmp/v1.mp4",
            )
            session.add(video)
            session.flush()
            from gpcg.application.clip_usage_service import record_clip_usage
            record_clip_usage(session, video.id, src.id, 50, 150, consumer_user_id=1)
            session.commit()

        with session_scope() as session:
            selector = GameplaySelector()
            rng = random.Random(42)
            clips = selector.select(
                session, game.id, 20, scene_duration=0, rng=rng,
                user_id=1, max_uses=1,
            )
            # Should find clips outside the used range (0-50 or 150-200)
            for clip in clips:
                # No significant overlap with 50-150
                assert clip.start_sec < 50 or clip.start_sec >= 150 or \
                    (clip.end_sec <= 51 or clip.start_sec >= 149)

    def test_max_uses_2_allows_reuse(self):
        """Selector with max_uses=2 can select from used regions."""
        with session_scope() as session:
            user = _make_user(session, uid=1)
            game = get_or_create(session, "SelectorTest2")
            src = _make_source(session, game.id, user_id=1, duration=200)
            asset = _make_asset(session, src.id, 0, 200)
            video = Video(
                user_id=1, status=VideoStatus.ready.value,
                file_path="/tmp/v2.mp4",
            )
            session.add(video)
            session.flush()
            from gpcg.application.clip_usage_service import record_clip_usage
            record_clip_usage(session, video.id, src.id, 50, 150, consumer_user_id=1)
            session.commit()

        with session_scope() as session:
            selector = GameplaySelector()
            rng = random.Random(42)
            clips = selector.select(
                session, game.id, 20, scene_duration=0, rng=rng,
                user_id=1, max_uses=2,
            )
            # Should find clips — either in unused gaps or reused regions
            assert len(clips) > 0


# ── Integration tests: estimate_availability ────────────────────────────────


class TestEstimateAvailability:
    def test_abundant_with_no_usage(self):
        avail = estimate_availability(
            300, [], [(0, 30), (30, 60), (60, 90)],
            max_uses=1,
        )
        assert avail["status"] == "abundant"
        assert avail["eligible_events"] == 3
        assert avail["total_events"] == 3

    def test_none_when_all_used(self):
        used = [UsedRange(0, 90)]
        avail = estimate_availability(
            300, used, [(0, 30), (30, 60), (60, 90)],
            max_uses=1,
        )
        assert avail["status"] in ("none", "low")
        assert avail["eligible_events"] == 0

    def test_partial_when_some_used(self):
        used = [UsedRange(0, 30)]
        avail = estimate_availability(
            300, used, [(0, 30), (30, 60), (60, 90)],
            max_uses=1,
        )
        assert avail["eligible_events"] == 2  # 2 of 3 still eligible

    def test_reuse_only_when_all_used_but_unlimited(self):
        used = [UsedRange(0, 90)]
        avail = estimate_availability(
            300, used, [(0, 30), (30, 60), (60, 90)],
            max_uses=0,  # unlimited
        )
        # With unlimited, all events are eligible
        assert avail["eligible_events"] == 3


# ── Integration tests: overlap scenarios from spec §11 ──────────────────────


class TestOverlapScenarios:
    def test_significant_overlap_blocks_with_max_1(self):
        """uso: 100-120, candidato: 105-125 → Sistema reconhece reutilização significativa."""
        used = [UsedRange(100, 120)]
        # 105-125 overlaps 15s with 100-120 → significant
        assert not is_range_eligible(used, 105, 125, max_uses=1)

    def test_minor_overlap_doesnt_block(self):
        """uso: 100-120, candidato: 119-140 → Sistema aplica corretamente a política."""
        used = [UsedRange(100, 120)]
        # 119-140 overlaps 1s with 100-120 → exactly at tolerance (not > tolerance)
        assert is_range_eligible(used, 119, 140, max_uses=1)

    def test_minor_overlap_doesnt_block_with_max_2(self):
        """Same scenario with max_uses=2 — still eligible."""
        used = [UsedRange(100, 120)]
        assert is_range_eligible(used, 119, 140, max_uses=2)

    def test_essentially_same_scene_blocked(self):
        """uso: 100-120, candidato: 104-124 → essencialmente reutilização da mesma cena."""
        used = [UsedRange(100, 120)]
        # 104-124 overlaps 16s with 100-120 → significant
        assert not is_range_eligible(used, 104, 124, max_uses=1)
