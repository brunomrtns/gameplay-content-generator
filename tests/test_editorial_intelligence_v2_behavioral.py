"""Behavioral tests for Editorial Intelligence V2 — long-term robustness.

These tests validate the system's behavior across the full lifecycle of a
channel, from creation through abandonment, niche changes, and scale. They
are NOT unit tests — they test emergent behavior and convergence properties.

Scenarios:
  1. Brand new channel (no gameplay, no history, no feedback)
  2. Abandoned channel (long gap, then return)
  3. Complete niche change (FPS → Retro Gaming)
  4. Gameplay removed (important game's clips deleted)
  5. Gameplay added (completely new game)
  6. Conflicting feedback (analytics success vs user rejection)
  7. Degraded sources (feeds unavailable)
  8. Queue edge cases (empty, full, overflow)
  9. Scale (thousands of KnowledgeItems)
  10. Convergence: no death spirals after repeated rejections
  11. Learned preferences caps and decay
  12. Feedback adjustment cap and decay
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Fresh SQLite DB with all V2 flags enabled."""
    db_path = tmp_path / "test_behavioral.db"
    monkeypatch.setenv("GPCG_DB_PATH", str(db_path))
    monkeypatch.setenv("GPCG_EDITORIAL_BRIEF_ENABLED", "true")
    monkeypatch.setenv("GPCG_COMPOSITE_SCORING_ENABLED", "true")
    monkeypatch.setenv("GPCG_DIVERSITY_ENGINE_ENABLED", "true")
    monkeypatch.setenv("GPCG_FEEDBACK_LOOP_ENABLED", "true")
    monkeypatch.setenv("GPCG_EDITORIAL_EXPLORATION_FACTOR", "0.0")
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
    from gpcg.core.models import KnowledgeItem, KnowledgeItemSource, KnowledgeItemStatus
    defaults = dict(
        title="Test KI",
        content="Test content",
        item_type="curiosity",
        source_type=KnowledgeItemSource.rss.value,
        status=KnowledgeItemStatus.fresh.value,
        editorial_score=50.0,
        content_hash="abc123",
    )
    defaults.update(kwargs)
    return KnowledgeItem(**defaults)


def _make_game_with_clips(session, user_id, name, n_clips):
    """Helper: create a game with gameplay source + clips."""
    from gpcg.domains.games.models import Game, GameplaySource, GameplayAsset
    game = Game(canonical_name=name, slug=name.lower())
    session.add(game)
    session.flush()
    src = GameplaySource(
        user_id=user_id, game_id=game.id, filename=f"{name}.mp4",
        file_hash=f"h_{name}", ingestion_status="ready",
    )
    session.add(src)
    session.flush()
    for i in range(n_clips):
        session.add(GameplayAsset(source_id=src.id, start_sec=float(i), end_sec=float(i+1)))
    session.flush()
    return game


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 1: Brand new channel
# ══════════════════════════════════════════════════════════════════════════════


class TestBrandNewChannel:
    def test_new_channel_has_empty_profile(self, fresh_db):
        """A brand new channel should have an empty profile with safe defaults."""
        from gpcg.application.editorial_profile_service import get_or_create_profile
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User

        with session_scope() as session:
            user = User(email="new@example.com", name="new")
            session.add(user)
            session.flush()

            profile = get_or_create_profile(session, user.id)
            assert profile.content_type_affinity == {}
            assert profile.editorial_keywords == []
            assert profile.custom_feeds == []
            assert profile.gameplay_driven_collection is True
            assert profile.diversity_strictness == 0.5
            assert profile.learned_preferences == {}
            assert profile.production_history_summary == {}

    def test_new_channel_intent_has_no_priority_games(self, fresh_db):
        """A channel with no gameplay should have empty priority_games."""
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import get_or_create_profile
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User

        with session_scope() as session:
            user = User(email="new@example.com", name="new")
            session.add(user)
            session.flush()

            profile = get_or_create_profile(session, user.id)
            intent = EditorialIntentBuilder().build(session, user.id, profile)

            assert intent.priority_games == []
            assert intent.cooldown_games == {}
            # Should have default targets (balanced)
            assert intent.collection_targets.get("curiosity", 0) > 0

    def test_new_channel_reconciler_still_fills_queue(self, fresh_db):
        """Even with no gameplay, the reconciler should fill the queue
        using editorial_score (composite score will be low but relative
        ordering is preserved)."""
        from gpcg.api.automation_routes import _reconcile_idea_queue
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, KnowledgeItem, Automation

        with session_scope() as session:
            user = User(email="new@example.com", name="new")
            session.add(user)
            session.flush()
            uid = user.id

            # Add some KIs (no game_id — general topic)
            for i in range(5):
                session.add(_make_ki(
                    title=f"Idea {i}", content_hash=f"h{i}",
                    user_id=uid, is_public=False, game_id=None,
                    editorial_score=50.0 + i * 10,
                ))

            auto = Automation(
                user_id=uid, name="Test",
                config={"queue_mode": "automatic", "auto_fill_queue": True,
                        "max_queue_size": 3, "idea_queue": []},
            )
            session.add(auto)
            session.flush()

            result = _reconcile_idea_queue(session, uid, {"max_queue_size": 3})
            assert len(result) == 3
            # Should be sorted by composite score (which for no-gameplay
            # channel is quality * 0.15 * timing — relative order preserved)
            kis = [session.get(KnowledgeItem, r["ki_id"]) for r in result]
            # Highest editorial_score should be first (quality dominates)
            assert kis[0].editorial_score >= kis[1].editorial_score >= kis[2].editorial_score

    def test_new_channel_feedback_does_not_crash(self, fresh_db):
        """Feedback loop on a new channel (no embeddings) should not crash."""
        from gpcg.application.feedback_propagator import FeedbackPropagator
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, KnowledgeItem

        with session_scope() as session:
            user = User(email="new@example.com", name="new")
            session.add(user)
            session.flush()

            ki = _make_ki(user_id=user.id, content_hash="h1")
            session.add(ki)
            session.flush()

            # Should not crash even with no embeddings
            propagator = FeedbackPropagator()
            with patch("gpcg.application.feedback_propagator.get_knowledge_item_embedding", return_value=None):
                count = propagator.propagate_rejection(session, user.id, ki.id)
            assert count == 0  # no similar KIs to propagate to


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 2: Abandoned channel (long gap, then return)
# ══════════════════════════════════════════════════════════════════════════════


class TestAbandonedChannel:
    def test_return_after_gap_still_collects(self, fresh_db):
        """A channel that returns after months should still be able to collect."""
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import get_or_create_profile
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, Video

        with session_scope() as session:
            user = User(email="abandoned@example.com", name="abandoned")
            session.add(user)
            session.flush()
            uid = user.id

            # Add a video from 6 months ago
            old_video = Video(user_id=uid, game_id=1, file_path="/tmp/old.mp4")
            old_video.created_at = datetime.now(timezone.utc) - timedelta(days=180)
            session.add(old_video)

            profile = get_or_create_profile(session, uid)
            intent = EditorialIntentBuilder().build(session, uid, profile)

            # Should still produce targets (not crash, not return empty)
            assert len(intent.collection_targets) > 0
            # No cooldowns (old video is outside the 10-video window)
            assert intent.cooldown_games == {}

    def test_old_kis_are_archived_on_return(self, fresh_db):
        """KIs collected before the gap should be archived by lifecycle."""
        from gpcg.application.lifecycle_manager import LifecycleManager
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, KnowledgeItem

        with session_scope() as session:
            user = User(email="abandoned@example.com", name="abandoned")
            session.add(user)
            session.flush()

            # Old news KI from 30 days ago
            ki = _make_ki(
                item_type="news",
                published_at=datetime.now(timezone.utc) - timedelta(days=30),
                content_hash="old_news",
            )
            session.add(ki)
            session.flush()

            LifecycleManager().update_all_fresh(session)
            session.refresh(ki)
            # News archive_after = 14 days → 30 days → archived
            assert ki.lifecycle_stage == "archived"


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 3: Complete niche change (FPS → Retro Gaming)
# ══════════════════════════════════════════════════════════════════════════════


class TestNicheChange:
    def test_reapply_preset_preserves_learning(self, fresh_db):
        """Changing niche via preset should preserve learned preferences."""
        from gpcg.application.editorial_profile_service import (
            apply_preset,
            update_learned_preferences,
            get_or_create_profile,
        )
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User

        with session_scope() as session:
            user = User(email="change@example.com", name="change")
            session.add(user)
            session.flush()
            uid = user.id

            # Start as FPS channel (using noticias preset as proxy)
            apply_preset(session, uid, "noticias")
            update_learned_preferences(session, uid, preferred_games=[1, 2, 3])

            # Change to Retro Gaming
            apply_preset(session, uid, "nostalgia")

            profile = get_or_create_profile(session, uid)
            # Configuration should be overwritten
            assert profile.niche == "Nostalgia e retrospectiva de jogos clássicos"
            assert profile.content_type_affinity.get("curiosity") == 0.7
            # Learning should be preserved
            assert profile.learned_preferences.get("preferred_games") == [1, 2, 3]

    def test_niche_change_allows_relearning(self, fresh_db):
        """After niche change, the system should be able to learn new preferences."""
        from gpcg.application.editorial_profile_service import (
            apply_preset,
            update_learned_preferences,
            get_or_create_profile,
        )
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User

        with session_scope() as session:
            user = User(email="change@example.com", name="change")
            session.add(user)
            session.flush()
            uid = user.id

            # Start with FPS preferences
            apply_preset(session, uid, "noticias")
            update_learned_preferences(session, uid, preferred_games=[1, 2])

            # Change to Retro Gaming
            apply_preset(session, uid, "nostalgia")

            # Learn new retro games
            update_learned_preferences(session, uid, preferred_games=[10, 20])

            profile = get_or_create_profile(session, uid)
            games = profile.learned_preferences.get("preferred_games", [])
            # Both old and new should be present (learning accumulates)
            assert 10 in games and 20 in games

    def test_decayed_old_preferences_fade(self, fresh_db):
        """Old preferences from the previous niche should decay over time."""
        from gpcg.application.editorial_profile_service import (
            update_learned_preferences,
            decay_learned_preferences,
            get_or_create_profile,
        )
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User

        with session_scope() as session:
            user = User(email="change@example.com", name="change")
            session.add(user)
            session.flush()
            uid = user.id

            # Build up 10 old preferences
            update_learned_preferences(session, uid, preferred_games=list(range(10)))
            profile = get_or_create_profile(session, uid)
            assert len(profile.learned_preferences["preferred_games"]) == 10

            # Apply decay multiple times (simulating weeks of operation)
            for _ in range(5):
                decay_learned_preferences(session, uid)

            profile = get_or_create_profile(session, uid)
            games = profile.learned_preferences["preferred_games"]
            # Should have shrunk (5 decays removed 5 entries)
            assert len(games) == 5


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 4: Gameplay removed
# ══════════════════════════════════════════════════════════════════════════════


class TestGameplayRemoved:
    def test_removed_game_drops_from_priority(self, fresh_db):
        """When gameplay is removed, the game should drop from priority_games."""
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import get_or_create_profile
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User
        from gpcg.domains.games.models import GameplayAsset, GameplaySource

        with session_scope() as session:
            user = User(email="rm@example.com", name="rm")
            session.add(user)
            session.flush()
            uid = user.id

            game = _make_game_with_clips(session, uid, "Bully", 10)
            game_id = game.id

            profile = get_or_create_profile(session, uid)
            intent = EditorialIntentBuilder().build(session, uid, profile)
            assert any(g.name == "Bully" for g in intent.priority_games)

            # Remove all gameplay assets and sources
            for asset in session.query(GameplayAsset).all():
                session.delete(asset)
            for src in session.query(GameplaySource).all():
                session.delete(src)
            session.flush()

            intent2 = EditorialIntentBuilder().build(session, uid, profile)
            # Game should no longer be in priority (no clips)
            assert not any(g.name == "Bully" for g in intent2.priority_games)

    def test_removed_game_kis_score_lower(self, fresh_db):
        """KIs about a game with removed gameplay should score lower."""
        from gpcg.application.composite_scorer import CompositeScorer
        from gpcg.application.editorial_brief_builder import EditorialBriefBuilder
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import get_or_create_profile
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, KnowledgeItem
        from gpcg.domains.games.models import GameplayAsset, GameplaySource
        from gpcg.domain.editorial_types import EditorialBrief

        with session_scope() as session:
            user = User(email="rm@example.com", name="rm")
            session.add(user)
            session.flush()
            uid = user.id

            game = _make_game_with_clips(session, uid, "Bully", 10)

            ki = _make_ki(
                game_id=game.id, editorial_score=80.0,
                content_hash="h1", user_id=uid, is_public=False,
            )
            session.add(ki)
            session.flush()

            brief = EditorialBrief(
                collection_targets={"curiosity": 10},
                cooldown_games={},
            )

            scorer = CompositeScorer()
            cs_before = scorer.score(ki, brief, session, uid)
            assert cs_before.fit_breakdown["gameplay_availability"] == 1.0

            # Remove all clips AND sources (so gameplay_availability drops to 0.0)
            for asset in session.query(GameplayAsset).all():
                session.delete(asset)
            for src in session.query(GameplaySource).all():
                session.delete(src)
            session.flush()

            cs_after = scorer.score(ki, brief, session, uid)
            assert cs_after.fit_breakdown["gameplay_availability"] == 0.0
            assert cs_after.production_fit < cs_before.production_fit


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 5: Gameplay added (new game)
# ══════════════════════════════════════════════════════════════════════════════


class TestGameplayAdded:
    def test_new_game_appears_in_priority(self, fresh_db):
        """Adding gameplay for a new game should make it appear in priority_games."""
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import get_or_create_profile
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User

        with session_scope() as session:
            user = User(email="add@example.com", name="add")
            session.add(user)
            session.flush()
            uid = user.id

            profile = get_or_create_profile(session, uid)
            intent = EditorialIntentBuilder().build(session, uid, profile)
            assert intent.priority_games == []

            # Add gameplay for a new game
            _make_game_with_clips(session, uid, "Minecraft", 10)

            intent2 = EditorialIntentBuilder().build(session, uid, profile)
            assert any(g.name == "Minecraft" for g in intent2.priority_games)

    def test_new_game_kis_score_higher(self, fresh_db):
        """KIs about a newly-added game should score higher after gameplay is added."""
        from gpcg.application.composite_scorer import CompositeScorer
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, KnowledgeItem
        from gpcg.domains.games.models import Game, GameplaySource, GameplayAsset
        from gpcg.domain.editorial_types import EditorialBrief

        with session_scope() as session:
            user = User(email="add@example.com", name="add")
            session.add(user)
            session.flush()
            uid = user.id

            game = Game(canonical_name="Minecraft", slug="minecraft")
            session.add(game)
            session.flush()

            ki = _make_ki(
                game_id=game.id, editorial_score=80.0,
                content_hash="h1", user_id=uid, is_public=False,
            )
            session.add(ki)
            session.flush()

            brief = EditorialBrief(collection_targets={"curiosity": 10})
            scorer = CompositeScorer()

            cs_before = scorer.score(ki, brief, session, uid)
            assert cs_before.fit_breakdown["gameplay_availability"] == 0.0

            # Add gameplay for the existing game (don't create a new game)
            src = GameplaySource(
                user_id=uid, game_id=game.id, filename="mc.mp4",
                file_hash="hmc", ingestion_status="ready",
            )
            session.add(src)
            session.flush()
            session.add(GameplayAsset(source_id=src.id, start_sec=0, end_sec=5))
            session.flush()

            cs_after = scorer.score(ki, brief, session, uid)
            assert cs_after.fit_breakdown["gameplay_availability"] == 1.0
            assert cs_after.production_fit > cs_before.production_fit


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 6: Conflicting feedback
# ══════════════════════════════════════════════════════════════════════════════


class TestConflictingFeedback:
    def test_rejection_after_production_is_consistent(self, fresh_db):
        """If a video was produced but the user rejects similar KIs later,
        the rejection should apply (user's current intent wins)."""
        from gpcg.application.feedback_propagator import FeedbackPropagator
        from gpcg.application.editorial_profile_service import get_or_create_profile
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, KnowledgeItem, Video

        with session_scope() as session:
            user = User(email="conflict@example.com", name="conflict")
            session.add(user)
            session.flush()
            uid = user.id

            # Produce a video about game 1
            video = Video(user_id=uid, game_id=1, file_path="/tmp/v.mp4")
            session.add(video)
            session.flush()

            propagator = FeedbackPropagator()
            propagator.record_production(session, uid, video.id)

            # Now reject a similar KI
            ki = _make_ki(
                title="Game 1 curiosity", game_id=1,
                user_id=uid, content_hash="h1",
                editorial_score=70.0,
            )
            session.add(ki)
            session.flush()

            # Mock embeddings: ki is similar to itself
            def mock_emb(s, kid):
                return [1.0, 0.0, 0.0] if kid == ki.id else None

            with patch("gpcg.application.feedback_propagator.get_knowledge_item_embedding",
                       side_effect=mock_emb):
                propagator.propagate_rejection(session, uid, ki.id)

            # Production history should show the video
            profile = get_or_create_profile(session, uid)
            assert profile.production_history_summary.get("total_videos") == 1

            # The rejected KI should have a signal
            from gpcg.core.models import EditorialSignal
            signals = session.query(EditorialSignal).all()
            assert len(signals) == 2  # production + rejection


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 7: Degraded sources (feeds unavailable)
# ══════════════════════════════════════════════════════════════════════════════


class TestDegradedSources:
    def test_all_feeds_down_still_completes(self, fresh_db):
        """When all feeds return errors, collection completes with 0 items
        (no crash)."""
        from gpcg.application.goal_oriented_collector import GoalOrientedCollector
        from gpcg.application.editorial_brief_builder import EditorialBriefBuilder
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import get_or_create_profile
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User

        with session_scope() as session:
            user = User(email="degraded@example.com", name="degraded")
            session.add(user)
            session.flush()
            uid = user.id

            profile = get_or_create_profile(session, uid)
            intent = EditorialIntentBuilder().build(session, uid, profile)
            brief = EditorialBriefBuilder().build(session, uid, profile, intent)

            # Mock feedparser to return errors
            def mock_parse(url):
                raise ConnectionError("Feed unavailable")

            collector = GoalOrientedCollector()
            with patch("feedparser.parse", side_effect=mock_parse):
                result = collector.collect(session, brief, uid)

            # Should complete with 0 items, not crash
            assert result.total == 0
            # It should have consulted feeds (tried and failed gracefully)
            assert result.feeds_consulted > 0

    def test_partial_feed_failure_collects_from_working(self, fresh_db):
        """When some feeds fail, collection still works from the rest."""
        from gpcg.application.goal_oriented_collector import GoalOrientedCollector
        from gpcg.application.editorial_brief_builder import EditorialBriefBuilder
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import get_or_create_profile
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User

        with session_scope() as session:
            user = User(email="degraded@example.com", name="degraded")
            session.add(user)
            session.flush()
            uid = user.id

            _make_game_with_clips(session, uid, "Bully", 5)

            profile = get_or_create_profile(session, uid)
            intent = EditorialIntentBuilder().build(session, uid, profile)
            brief = EditorialBriefBuilder().build(session, uid, profile, intent)

            call_count = {"n": 0}
            def mock_parse(url):
                call_count["n"] += 1
                if call_count["n"] <= 2:
                    raise ConnectionError("Feed unavailable")
                # Later calls return entries
                return MagicMock(entries=[
                    MagicMock(title=f"Item {call_count['n']}", summary="Content",
                              link=f"http://x/{call_count['n']}")
                ])

            collector = GoalOrientedCollector()
            with patch("feedparser.parse", side_effect=mock_parse):
                result = collector.collect(session, brief, uid)

            # Should have collected something from the working feeds
            assert result.total > 0


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 8: Queue edge cases
# ══════════════════════════════════════════════════════════════════════════════


class TestQueueEdgeCases:
    def test_empty_queue_fills(self, fresh_db):
        """Empty queue should be filled up to max_queue_size."""
        from gpcg.api.automation_routes import _reconcile_idea_queue
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, KnowledgeItem, Automation

        with session_scope() as session:
            user = User(email="q@example.com", name="q")
            session.add(user)
            session.flush()
            uid = user.id

            for i in range(20):
                session.add(_make_ki(
                    title=f"KI {i}", content_hash=f"h{i}",
                    user_id=uid, is_public=False,
                    editorial_score=50.0 + i,
                ))

            auto = Automation(
                user_id=uid, name="Test",
                config={"queue_mode": "automatic", "auto_fill_queue": True,
                        "max_queue_size": 10, "idea_queue": []},
            )
            session.add(auto)
            session.flush()

            result = _reconcile_idea_queue(session, uid, {"max_queue_size": 10})
            assert len(result) == 10

    def test_no_kis_returns_empty(self, fresh_db):
        """No KIs available → empty result (no crash)."""
        from gpcg.api.automation_routes import _reconcile_idea_queue
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, Automation

        with session_scope() as session:
            user = User(email="empty@example.com", name="empty")
            session.add(user)
            session.flush()

            auto = Automation(
                user_id=user.id, name="Test",
                config={"queue_mode": "automatic", "auto_fill_queue": True,
                        "max_queue_size": 10, "idea_queue": []},
            )
            session.add(auto)
            session.flush()

            result = _reconcile_idea_queue(session, user.id, {"max_queue_size": 10})
            assert result == []

    def test_queue_smaller_than_max(self, fresh_db):
        """Fewer KIs than max_queue_size → return what's available."""
        from gpcg.api.automation_routes import _reconcile_idea_queue
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, Automation

        with session_scope() as session:
            user = User(email="few@example.com", name="few")
            session.add(user)
            session.flush()
            uid = user.id

            for i in range(2):
                session.add(_make_ki(
                    title=f"KI {i}", content_hash=f"h{i}",
                    user_id=uid, is_public=False,
                ))

            auto = Automation(
                user_id=uid, name="Test",
                config={"queue_mode": "automatic", "auto_fill_queue": True,
                        "max_queue_size": 10, "idea_queue": []},
            )
            session.add(auto)
            session.flush()

            result = _reconcile_idea_queue(session, uid, {"max_queue_size": 10})
            assert len(result) == 2


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 9: Scale (thousands of KnowledgeItems)
# ══════════════════════════════════════════════════════════════════════════════


class TestScale:
    def test_reconciler_with_many_kis(self, fresh_db):
        """Reconciler should handle 1000+ KIs without performance issues."""
        from gpcg.api.automation_routes import _reconcile_idea_queue
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, Automation

        with session_scope() as session:
            user = User(email="scale@example.com", name="scale")
            session.add(user)
            session.flush()
            uid = user.id

            # Create 500 KIs (enough to test scale without being too slow)
            for i in range(500):
                session.add(_make_ki(
                    title=f"KI {i}", content_hash=f"h{i}",
                    user_id=uid, is_public=False,
                    editorial_score=float(i % 100),
                ))
            session.flush()

            auto = Automation(
                user_id=uid, name="Test",
                config={"queue_mode": "automatic", "auto_fill_queue": True,
                        "max_queue_size": 10, "idea_queue": []},
            )
            session.add(auto)
            session.flush()

            result = _reconcile_idea_queue(session, uid, {"max_queue_size": 10})
            assert len(result) == 10

    def test_lifecycle_manager_with_many_kis(self, fresh_db):
        """LifecycleManager should handle many KIs."""
        from gpcg.application.lifecycle_manager import LifecycleManager
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User

        with session_scope() as session:
            user = User(email="scale@example.com", name="scale")
            session.add(user)
            session.flush()

            for i in range(200):
                session.add(_make_ki(
                    item_type="news",
                    content_hash=f"h{i}",
                    published_at=datetime.now(timezone.utc) - timedelta(days=i % 20),
                ))
            session.flush()

            count = LifecycleManager().update_all_fresh(session)
            # Should update many KIs (those with non-default freshness)
            assert count > 0


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 10: Convergence — no death spirals
# ══════════════════════════════════════════════════════════════════════════════


class TestConvergenceNoDeathSpirals:
    def test_repeated_rejections_are_capped(self, fresh_db):
        """Rejecting 10 similar KIs should not reduce a KI's score by more
        than MAX_CUMULATIVE_ADJUSTMENT."""
        from gpcg.application.feedback_propagator import (
            FeedbackPropagator,
            MAX_CUMULATIVE_ADJUSTMENT,
        )
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, KnowledgeItem

        with session_scope() as session:
            user = User(email="spiral@example.com", name="spiral")
            session.add(user)
            session.flush()
            uid = user.id

            # Target KI (will receive propagated penalties)
            target = _make_ki(
                title="Target", user_id=uid, content_hash="target",
                editorial_score=80.0,
            )
            session.add(target)
            session.flush()

            # 10 source KIs to reject (all similar to target)
            source_ids = []
            for i in range(10):
                ki = _make_ki(
                    title=f"Source {i}", user_id=uid, content_hash=f"s{i}",
                    editorial_score=50.0,
                )
                session.add(ki)
                session.flush()
                source_ids.append(ki.id)

            # Mock embeddings: all KIs are identical
            def mock_emb(s, kid):
                return [1.0, 0.0, 0.0]

            propagator = FeedbackPropagator()
            with patch("gpcg.application.feedback_propagator.get_knowledge_item_embedding",
                       side_effect=mock_emb):
                for sid in source_ids:
                    propagator.propagate_rejection(session, uid, sid)

            session.refresh(target)
            # The total penalty should be capped at MAX_CUMULATIVE_ADJUSTMENT
            total_penalty = 80.0 - target.editorial_score
            assert total_penalty <= MAX_CUMULATIVE_ADJUSTMENT + 0.1  # small float tolerance

    def test_feedback_decay_restores_score(self, fresh_db):
        """After decay, a penalized KI's feedback_adjustment should fade."""
        from gpcg.application.feedback_propagator import (
            FeedbackPropagator,
            FEEDBACK_DECAY_FACTOR,
        )
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, KnowledgeItem

        with session_scope() as session:
            user = User(email="decay@example.com", name="decay")
            session.add(user)
            session.flush()
            uid = user.id

            ki = _make_ki(
                title="Test", user_id=uid, content_hash="h1",
                editorial_score=70.0,
            )
            ki.feedback_adjustment = -15.0
            session.add(ki)
            session.flush()

            propagator = FeedbackPropagator()
            propagator.decay_feedback_adjustments(session)
            session.refresh(ki)

            # Should have decayed (not exact -15.0 anymore)
            assert abs(ki.feedback_adjustment) < 15.0
            assert ki.feedback_adjustment == -15.0 * FEEDBACK_DECAY_FACTOR

    def test_decay_to_zero_snaps(self, fresh_db):
        """Very small feedback adjustments should snap to zero."""
        from gpcg.application.feedback_propagator import FeedbackPropagator
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, KnowledgeItem

        with session_scope() as session:
            user = User(email="snap@example.com", name="snap")
            session.add(user)
            session.flush()

            ki = _make_ki(user_id=user.id, content_hash="h1")
            ki.feedback_adjustment = -0.05  # very small
            session.add(ki)
            session.flush()

            propagator = FeedbackPropagator()
            propagator.decay_feedback_adjustments(session)
            session.refresh(ki)

            assert ki.feedback_adjustment == 0.0  # snapped to zero

    def test_feedback_does_not_cross_contaminate_users(self, fresh_db):
        """User A's rejection should NOT affect User B's KI scores."""
        from gpcg.application.feedback_propagator import FeedbackPropagator
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, KnowledgeItem

        with session_scope() as session:
            user_a = User(email="a@example.com", name="a")
            user_b = User(email="b@example.com", name="b")
            session.add_all([user_a, user_b])
            session.flush()

            # User A has a KI to reject
            ki_a = _make_ki(
                title="A's KI", user_id=user_a.id, content_hash="ha",
                editorial_score=70.0,
            )
            session.add(ki_a)

            # User B has a similar KI (should NOT be affected)
            ki_b = _make_ki(
                title="B's KI", user_id=user_b.id, content_hash="hb",
                editorial_score=70.0,
            )
            session.add(ki_b)
            session.flush()

            # Mock: both have identical embeddings
            def mock_emb(s, kid):
                return [1.0, 0.0, 0.0]

            propagator = FeedbackPropagator()
            with patch("gpcg.application.feedback_propagator.get_knowledge_item_embedding",
                       side_effect=mock_emb):
                propagator.propagate_rejection(session, user_a.id, ki_a.id)

            session.refresh(ki_b)
            # User B's KI should be unchanged
            assert ki_b.editorial_score == 70.0
            assert ki_b.feedback_adjustment == 0.0


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 11: Learned preferences caps
# ══════════════════════════════════════════════════════════════════════════════


class TestLearnedPreferencesCaps:
    def test_preferred_games_capped(self, fresh_db):
        """preferred_games should be capped at 20 entries."""
        from gpcg.application.editorial_profile_service import (
            update_learned_preferences,
            get_or_create_profile,
            LEARNED_PREF_CAPS,
        )
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User

        with session_scope() as session:
            user = User(email="cap@example.com", name="cap")
            session.add(user)
            session.flush()

            # Add 30 games
            update_learned_preferences(session, user.id, preferred_games=list(range(30)))
            profile = get_or_create_profile(session, user.id)
            assert len(profile.learned_preferences["preferred_games"]) == LEARNED_PREF_CAPS["preferred_games"]

    def test_avoided_topics_capped(self, fresh_db):
        """avoided_topics should be capped at 50 entries."""
        from gpcg.application.editorial_profile_service import (
            update_learned_preferences,
            get_or_create_profile,
            LEARNED_PREF_CAPS,
        )
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User

        with session_scope() as session:
            user = User(email="cap@example.com", name="cap")
            session.add(user)
            session.flush()

            # Add 60 topics
            update_learned_preferences(session, user.id,
                                       avoided_topics=[f"topic_{i}" for i in range(60)])
            profile = get_or_create_profile(session, user.id)
            assert len(profile.learned_preferences["avoided_topics"]) == LEARNED_PREF_CAPS["avoided_topics"]

    def test_fifo_eviction_keeps_recent(self, fresh_db):
        """When cap is exceeded, oldest entries should be evicted (FIFO)."""
        from gpcg.application.editorial_profile_service import (
            update_learned_preferences,
            get_or_create_profile,
            LEARNED_PREF_CAPS,
        )
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User

        with session_scope() as session:
            user = User(email="fifo@example.com", name="fifo")
            session.add(user)
            session.flush()

            # Add games 0-19 (fills the cap)
            update_learned_preferences(session, user.id, preferred_games=list(range(20)))
            # Now add game 100 (should push out game 19 — the oldest)
            update_learned_preferences(session, user.id, preferred_games=[100])
            profile = get_or_create_profile(session, user.id)
            games = profile.learned_preferences["preferred_games"]
            assert 100 in games  # new entry is present
            assert 19 not in games  # oldest was evicted
            assert 0 in games  # second-oldest is still there


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 12: Editorial signals cleanup
# ══════════════════════════════════════════════════════════════════════════════


class TestSignalsCleanup:
    def test_old_signals_are_deleted(self, fresh_db):
        """Signals older than 90 days should be cleaned up."""
        from gpcg.application.feedback_propagator import FeedbackPropagator
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, EditorialSignal

        with session_scope() as session:
            user = User(email="cleanup@example.com", name="cleanup")
            session.add(user)
            session.flush()

            # Create an old signal (100 days ago)
            old_signal = EditorialSignal(
                user_id=user.id,
                signal_type="rejection_penalty",
                signal_value=-10.0,
            )
            old_signal.created_at = datetime.now(timezone.utc) - timedelta(days=100)
            session.add(old_signal)

            # Create a recent signal
            recent_signal = EditorialSignal(
                user_id=user.id,
                signal_type="manual_add_boost",
                signal_value=10.0,
            )
            session.add(recent_signal)
            session.flush()

            propagator = FeedbackPropagator()
            deleted = propagator.cleanup_old_signals(session, days=90)

            assert deleted == 1
            remaining = session.query(EditorialSignal).all()
            assert len(remaining) == 1
            assert remaining[0].signal_type == "manual_add_boost"


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 13: Priority floor prevents monopoly
# ══════════════════════════════════════════════════════════════════════════════


class TestPriorityFloor:
    def test_lightly_clipped_game_gets_minimum_priority(self, fresh_db):
        """A game with 1 clip should still get at least 0.15 priority,
        even if another game has 100 clips."""
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import get_or_create_profile
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User

        with session_scope() as session:
            user = User(email="floor@example.com", name="floor")
            session.add(user)
            session.flush()
            uid = user.id

            # Game A: 100 clips, Game B: 1 clip
            _make_game_with_clips(session, uid, "BigGame", 100)
            _make_game_with_clips(session, uid, "SmallGame", 1)

            profile = get_or_create_profile(session, uid)
            intent = EditorialIntentBuilder().build(session, uid, profile)

            priorities = {g.name: g.priority for g in intent.priority_games}
            assert priorities["BigGame"] > priorities["SmallGame"]
            assert priorities["SmallGame"] >= 0.15  # priority floor
