"""Tests for Editorial Intelligence V2 — Phase 2 (Composite Scoring + Lifecycle).

Tests:
  - LifecycleManager: freshness decay per item_type, stage transitions
  - CompositeScorer: 3-layer multiplicative scoring
  - Reconciler V2: composite scoring path vs legacy path
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from gpcg.domain.editorial_types import EditorialBrief, FeedSpec, SearchQuery


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Fresh SQLite DB with schema for each test."""
    db_path = tmp_path / "test_v2_phase2.db"
    monkeypatch.setenv("GPCG_DB_PATH", str(db_path))
    from gpcg.config import get_settings
    get_settings.cache_clear()
    import gpcg.infrastructure.database as db_module
    db_module._engine = None
    db_module._SessionLocal = None
    from gpcg.infrastructure.database import init_db
    init_db()
    yield db_path
    db_module._engine = None
    db_module._SessionLocal = None
    get_settings.cache_clear()


def _make_ki(**kwargs):
    """Create a KnowledgeItem with sensible defaults."""
    from gpcg.domain.models import KnowledgeItem, KnowledgeItemSource, KnowledgeItemStatus
    defaults = dict(
        title="Test KI",
        content="Test content",
        item_type="news",
        source_type=KnowledgeItemSource.rss.value,
        status=KnowledgeItemStatus.fresh.value,
        editorial_score=50.0,
        content_hash="abc123",
    )
    defaults.update(kwargs)
    return KnowledgeItem(**defaults)


# ── Lifecycle Manager ────────────────────────────────────────────────────────


class TestLifecycleManager:
    def test_news_decays_fast(self):
        from gpcg.application.lifecycle_manager import compute_freshness
        ki = _make_ki(
            item_type="news",
            published_at=datetime.now(timezone.utc) - timedelta(days=2),
        )
        freshness = compute_freshness(ki)
        # news half-life = 2 days → 2 days old → 0.5
        assert abs(freshness - 0.5) < 0.05

    def test_curiosity_decays_slowly(self):
        from gpcg.application.lifecycle_manager import compute_freshness
        ki = _make_ki(
            item_type="curiosity",
            published_at=datetime.now(timezone.utc) - timedelta(days=2),
        )
        freshness = compute_freshness(ki)
        # curiosity half-life = 90 days → 2 days old → very fresh
        assert freshness > 0.9

    def test_lore_is_evergreen(self):
        from gpcg.application.lifecycle_manager import compute_freshness
        ki = _make_ki(
            item_type="lore",
            published_at=datetime.now(timezone.utc) - timedelta(days=365),
        )
        freshness = compute_freshness(ki)
        assert freshness == 1.0

    def test_fact_decays_medium(self):
        from gpcg.application.lifecycle_manager import compute_freshness
        ki = _make_ki(
            item_type="fact",
            published_at=datetime.now(timezone.utc) - timedelta(days=180),
        )
        freshness = compute_freshness(ki)
        # fact half-life = 180 days → 180 days old → 0.5
        assert abs(freshness - 0.5) < 0.05

    def test_fresh_ki_has_high_freshness(self):
        from gpcg.application.lifecycle_manager import compute_freshness
        ki = _make_ki(
            item_type="news",
            published_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        freshness = compute_freshness(ki)
        assert freshness > 0.9

    def test_determine_stage_fresh(self):
        from gpcg.application.lifecycle_manager import determine_stage
        ki = _make_ki(
            item_type="news",
            published_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        assert determine_stage(ki) == "fresh"

    def test_determine_stage_aging(self):
        from gpcg.application.lifecycle_manager import determine_stage
        ki = _make_ki(
            item_type="news",
            published_at=datetime.now(timezone.utc) - timedelta(days=5),
        )
        # news with 5 days → freshness ~0.18 < 0.3 → aging
        assert determine_stage(ki) == "aging"

    def test_determine_stage_archived(self):
        from gpcg.application.lifecycle_manager import determine_stage
        ki = _make_ki(
            item_type="news",
            published_at=datetime.now(timezone.utc) - timedelta(days=20),
        )
        # news archive_after = 14 days → 20 days → archived
        assert determine_stage(ki) == "archived"

    def test_lore_never_archived(self):
        from gpcg.application.lifecycle_manager import determine_stage
        ki = _make_ki(
            item_type="lore",
            published_at=datetime.now(timezone.utc) - timedelta(days=1000),
        )
        assert determine_stage(ki) == "fresh"  # evergreen, always fresh

    def test_update_all_fresh_updates_scores(self, fresh_db):
        from gpcg.application.lifecycle_manager import LifecycleManager
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User, KnowledgeItem

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()

            # Add a news KI that's 5 days old
            ki = _make_ki(
                item_type="news",
                published_at=datetime.now(timezone.utc) - timedelta(days=5),
            )
            session.add(ki)
            session.flush()

            # Initially freshness_score = 1.0 (default)
            assert ki.freshness_score == 1.0

            count = LifecycleManager().update_all_fresh(session)
            assert count == 1
            # After update, freshness should be < 1.0
            assert ki.freshness_score < 1.0
            assert ki.lifecycle_stage == "aging"

    def test_update_all_fresh_skips_used_items(self, fresh_db):
        from gpcg.application.lifecycle_manager import LifecycleManager
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import KnowledgeItem, KnowledgeItemStatus

        with session_scope() as session:
            ki = _make_ki(
                item_type="news",
                status=KnowledgeItemStatus.used.value,
                published_at=datetime.now(timezone.utc) - timedelta(days=30),
            )
            session.add(ki)
            session.flush()
            ki_id = ki.id

            count = LifecycleManager().update_all_fresh(session)
            assert count == 0  # used items are not updated


# ── Composite Scorer ─────────────────────────────────────────────────────────


class TestCompositeScorer:
    def test_score_with_gameplay_available(self, fresh_db):
        from gpcg.application.composite_scorer import CompositeScorer
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import (
            User, Game, GameplaySource, GameplayAsset, KnowledgeItem,
        )

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            game = Game(canonical_name="Bully", slug="bully")
            session.add(game)
            session.flush()
            gid = game.id

            source = GameplaySource(
                user_id=uid, game_id=gid, filename="bully.mp4",
                file_hash="abc", ingestion_status="ready",
            )
            session.add(source)
            session.flush()
            asset = GameplayAsset(source_id=source.id, start_sec=0, end_sec=5)
            session.add(asset)

            ki = _make_ki(
                game_id=gid, item_type="curiosity",
                editorial_score=80.0,
            )
            session.add(ki)
            session.flush()

            brief = EditorialBrief(
                collection_targets={"curiosity": 10, "news": 2},
                cooldown_games={},
            )

            scorer = CompositeScorer()
            cs = scorer.score(ki, brief, session, uid)

            # gameplay available → fit should be high
            assert cs.fit_breakdown["gameplay_availability"] == 1.0
            assert cs.production_fit > 0.5
            assert cs.final_score > 0

    def test_score_without_gameplay(self, fresh_db):
        from gpcg.application.composite_scorer import CompositeScorer
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User, Game, KnowledgeItem

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            game = Game(canonical_name="Minecraft", slug="minecraft")
            session.add(game)
            session.flush()

            ki = _make_ki(
                game_id=game.id, item_type="curiosity",
                editorial_score=80.0,
            )
            session.add(ki)
            session.flush()

            brief = EditorialBrief(
                collection_targets={"curiosity": 10},
                cooldown_games={},
            )

            scorer = CompositeScorer()
            cs = scorer.score(ki, brief, session, uid)

            # no gameplay → gameplay_availability = 0.0
            assert cs.fit_breakdown["gameplay_availability"] == 0.0
            # fit should be low (but not zero — other components contribute)
            assert cs.production_fit < 0.5
            # final score should be lower than with gameplay
            assert cs.final_score < 0.5

    def test_score_with_cooldown_penalty(self, fresh_db):
        from gpcg.application.composite_scorer import CompositeScorer
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User, Game, KnowledgeItem

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            game = Game(canonical_name="Bully", slug="bully")
            session.add(game)
            session.flush()
            gid = game.id

            ki = _make_ki(
                game_id=gid, item_type="curiosity",
                editorial_score=80.0,
            )
            session.add(ki)
            session.flush()

            # Game is in cooldown
            brief = EditorialBrief(
                collection_targets={"curiosity": 10},
                cooldown_games={gid: 14},
            )

            scorer = CompositeScorer()
            cs = scorer.score(ki, brief, session, uid)

            # cooldown → diversity_penalty = 0.3
            assert cs.timing_breakdown["diversity_penalty"] == 0.3
            assert cs.editorial_timing < 0.5

    def test_score_without_cooldown(self, fresh_db):
        from gpcg.application.composite_scorer import CompositeScorer
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User, Game, KnowledgeItem

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            game = Game(canonical_name="Bully", slug="bully")
            session.add(game)
            session.flush()

            ki = _make_ki(
                game_id=game.id, item_type="curiosity",
                editorial_score=80.0,
            )
            session.add(ki)
            session.flush()

            brief = EditorialBrief(
                collection_targets={"curiosity": 10},
                cooldown_games={},
            )

            scorer = CompositeScorer()
            cs = scorer.score(ki, brief, session, uid)

            # no cooldown → diversity_penalty = 1.0
            assert cs.timing_breakdown["diversity_penalty"] == 1.0

    def test_score_multiplicative_nature(self, fresh_db):
        """A KI with zero gameplay should have near-zero final score."""
        from gpcg.application.composite_scorer import CompositeScorer
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User, Game, KnowledgeItem

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            game = Game(canonical_name="Unknown", slug="unknown")
            session.add(game)
            session.flush()

            # High editorial score but no gameplay
            ki = _make_ki(
                game_id=game.id, item_type="curiosity",
                editorial_score=95.0,
            )
            session.add(ki)
            session.flush()

            brief = EditorialBrief(
                collection_targets={"curiosity": 10},
                cooldown_games={},
            )

            scorer = CompositeScorer()
            cs = scorer.score(ki, brief, session, uid)

            # quality is high (0.95) but fit is low (no gameplay)
            assert cs.editorial_quality > 0.9
            assert cs.fit_breakdown["gameplay_availability"] == 0.0
            # final = quality * fit * timing
            # fit is not zero (other components contribute) but is reduced
            assert cs.final_score < cs.editorial_quality

    def test_source_authority_tiers(self, fresh_db):
        from gpcg.application.composite_scorer import CompositeScorer
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User, KnowledgeItem

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            # IGN is high authority
            ki_ign = _make_ki(
                source_name="IGN", item_type="news",
                editorial_score=50.0,
            )
            session.add(ki_ign)
            session.flush()

            # Unknown source is default
            ki_unknown = _make_ki(
                source_name="RandomBlog", item_type="news",
                editorial_score=50.0,
                content_hash="different",
            )
            session.add(ki_unknown)
            session.flush()

            brief = EditorialBrief(collection_targets={"news": 5})
            scorer = CompositeScorer()

            cs_ign = scorer.score(ki_ign, brief, session, uid)
            cs_unknown = scorer.score(ki_unknown, brief, session, uid)

            assert cs_ign.fit_breakdown["source_authority"] > cs_unknown.fit_breakdown["source_authority"]


# ── Reconciler V2 ────────────────────────────────────────────────────────────


class TestReconcilerV2:
    def test_legacy_reconciler_sorts_by_editorial_score(self, fresh_db):
        """When composite scoring is off, reconciler uses editorial_score."""
        from gpcg.api.automation_routes import _reconcile_idea_queue
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User, KnowledgeItem

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            # Create KIs with different scores
            for i, score in enumerate([30, 80, 50, 90, 60]):
                ki = _make_ki(
                    title=f"KI {i}", editorial_score=float(score),
                    content_hash=f"hash_{i}",
                    user_id=uid, is_public=False,
                )
                session.add(ki)
            session.flush()

            result = _reconcile_idea_queue(
                session, uid, {"max_queue_size": 3},
            )

            assert len(result) == 3
            # Should be sorted by editorial_score desc: 90, 80, 60
            kis = [session.get(KnowledgeItem, r["ki_id"]) for r in result]
            assert kis[0].editorial_score == 90
            assert kis[1].editorial_score == 80
            assert kis[2].editorial_score == 60

    def test_composite_reconciler_prefers_kis_with_gameplay(self, fresh_db, monkeypatch):
        """When composite scoring is on, KIs with gameplay rank higher."""
        from gpcg.api.automation_routes import _reconcile_idea_queue
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import (
            User, Game, GameplaySource, GameplayAsset, KnowledgeItem,
        )

        # Enable composite scoring
        monkeypatch.setenv("GPCG_COMPOSITE_SCORING_ENABLED", "true")
        from gpcg.config import get_settings
        get_settings.cache_clear()

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            # Game with gameplay
            game1 = Game(canonical_name="Bully", slug="bully")
            session.add(game1)
            session.flush()
            g1 = game1.id

            source = GameplaySource(
                user_id=uid, game_id=g1, filename="bully.mp4",
                file_hash="abc", ingestion_status="ready",
            )
            session.add(source)
            session.flush()
            asset = GameplayAsset(source_id=source.id, start_sec=0, end_sec=5)
            session.add(asset)

            # Game without gameplay
            game2 = Game(canonical_name="Minecraft", slug="minecraft")
            session.add(game2)
            session.flush()

            # KI about Minecraft (higher editorial_score, no gameplay)
            ki_mc = _make_ki(
                title="Minecraft secret", game_id=game2.id,
                item_type="curiosity", editorial_score=90.0,
                content_hash="mc_hash", user_id=uid, is_public=False,
            )
            session.add(ki_mc)

            # KI about Bully (lower editorial_score, has gameplay)
            ki_bully = _make_ki(
                title="Bully secret", game_id=g1,
                item_type="curiosity", editorial_score=60.0,
                content_hash="bully_hash", user_id=uid, is_public=False,
            )
            session.add(ki_bully)
            session.flush()

            result = _reconcile_idea_queue(
                session, uid, {"max_queue_size": 2},
            )

            assert len(result) == 2
            # With composite scoring, Bully (gameplay available) should rank
            # higher than Minecraft (no gameplay) despite lower editorial_score
            kis = [session.get(KnowledgeItem, r["ki_id"]) for r in result]
            assert kis[0].title == "Bully secret"

    def test_reconciler_excludes_archived_kis(self, fresh_db):
        """Archived KIs should not appear in the queue."""
        from gpcg.api.automation_routes import _reconcile_idea_queue
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User, KnowledgeItem

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            # Fresh KI
            ki1 = _make_ki(
                title="Fresh", editorial_score=80.0,
                content_hash="h1", user_id=uid, is_public=False,
                lifecycle_stage="fresh",
            )
            session.add(ki1)

            # Archived KI (high score but archived)
            ki2 = _make_ki(
                title="Archived", editorial_score=95.0,
                content_hash="h2", user_id=uid, is_public=False,
                lifecycle_stage="archived",
            )
            session.add(ki2)
            session.flush()

            result = _reconcile_idea_queue(
                session, uid, {"max_queue_size": 5},
            )

            # Only the fresh KI should be returned
            assert len(result) == 1
            ki = session.get(KnowledgeItem, result[0]["ki_id"])
            assert ki.title == "Fresh"
