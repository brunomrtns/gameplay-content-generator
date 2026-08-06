"""Tests for Editorial Intelligence V2 — Phase 3 (Diversity + Gameplay Driver).

Tests:
  - Cooldown by game (strictness levels)
  - Format rotation (generate_short vs curiosity_short)
  - Gameplay as driver of collection priority
  - Exploration factor (random KIs in queue to avoid filter bubbles)
  - Creative style rotation
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Fresh SQLite DB with schema for each test."""
    db_path = tmp_path / "test_v2_phase3.db"
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


# ── Cooldown by game ─────────────────────────────────────────────────────────


class TestCooldownByGame:
    def test_no_cooldown_with_low_strictness(self, fresh_db):
        """strictness 0.3 → threshold 2, cooldown 14 days. 1 coverage < 2 → no cooldown."""
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import (
            get_or_create_profile,
            update_structured_fields,
        )
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User, Video

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            # 1 video about game 1
            v = Video(user_id=uid, game_id=1, file_path="/tmp/x.mp4")
            session.add(v)

            update_structured_fields(session, uid, diversity_strictness=0.3)
            profile = get_or_create_profile(session, uid)

            intent = EditorialIntentBuilder().build(session, uid, profile)
            # strictness 0.3 → threshold = round(3 - 0.6) = 2
            # 1 coverage < 2 → no cooldown
            assert 1 not in intent.cooldown_games

    def test_cooldown_with_high_strictness(self, fresh_db):
        """strictness 1.0 → threshold 1, cooldown 30 days. 1 coverage >= 1 → cooldown."""
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import (
            get_or_create_profile,
            update_structured_fields,
        )
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User, Video

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            v = Video(user_id=uid, game_id=1, file_path="/tmp/x.mp4")
            session.add(v)

            update_structured_fields(session, uid, diversity_strictness=1.0)
            profile = get_or_create_profile(session, uid)

            intent = EditorialIntentBuilder().build(session, uid, profile)
            assert 1 in intent.cooldown_games
            assert intent.cooldown_games[1] == 30  # 7 + 1.0 * 23 = 30

    def test_cooldown_days_scale_with_strictness(self, fresh_db):
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import (
            get_or_create_profile,
            update_structured_fields,
        )
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User, Video

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            # 3 videos to trigger cooldown at any strictness
            for _ in range(3):
                v = Video(user_id=uid, game_id=1, file_path="/tmp/x.mp4")
                session.add(v)

            # strictness 0.5 → cooldown = 7 + 0.5*23 = 18 days
            update_structured_fields(session, uid, diversity_strictness=0.5)
            profile = get_or_create_profile(session, uid)
            intent = EditorialIntentBuilder().build(session, uid, profile)
            assert intent.cooldown_games.get(1) == 18

    def test_cooldown_applies_to_multiple_games(self, fresh_db):
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import (
            get_or_create_profile,
            update_structured_fields,
        )
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User, Video

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            # Videos about 2 different games
            for _ in range(2):
                session.add(Video(user_id=uid, game_id=1, file_path="/tmp/x.mp4"))
                session.add(Video(user_id=uid, game_id=2, file_path="/tmp/y.mp4"))

            update_structured_fields(session, uid, diversity_strictness=1.0)
            profile = get_or_create_profile(session, uid)
            intent = EditorialIntentBuilder().build(session, uid, profile)

            assert 1 in intent.cooldown_games
            assert 2 in intent.cooldown_games


# ── Format rotation ──────────────────────────────────────────────────────────


class TestFormatRotation:
    def test_balanced_when_no_videos(self, fresh_db):
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import get_or_create_profile
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id
            profile = get_or_create_profile(session, uid)

            intent = EditorialIntentBuilder().build(session, uid, profile)
            assert intent.format_rotation == "balanced"

    def test_prefer_curiosity_short_when_mostly_game_related(self, fresh_db):
        """If >70% of recent videos have game_id, prefer curiosity_short."""
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import get_or_create_profile
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User, Video

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            # 8 game-related, 2 general → 80% game_related
            for i in range(8):
                session.add(Video(user_id=uid, game_id=1, file_path=f"/tmp/{i}.mp4"))
            for i in range(2):
                session.add(Video(user_id=uid, game_id=None, file_path=f"/tmp/g{i}.mp4"))

            profile = get_or_create_profile(session, uid)
            intent = EditorialIntentBuilder().build(session, uid, profile)
            assert intent.format_rotation == "prefer_curiosity_short"

    def test_prefer_generate_short_when_mostly_general(self, fresh_db):
        """If <30% of recent videos have game_id, prefer generate_short."""
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import get_or_create_profile
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User, Video

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            # 2 game-related, 8 general → 20% game_related
            for i in range(2):
                session.add(Video(user_id=uid, game_id=1, file_path=f"/tmp/{i}.mp4"))
            for i in range(8):
                session.add(Video(user_id=uid, game_id=None, file_path=f"/tmp/g{i}.mp4"))

            profile = get_or_create_profile(session, uid)
            intent = EditorialIntentBuilder().build(session, uid, profile)
            assert intent.format_rotation == "prefer_generate_short"


# ── Gameplay as driver ───────────────────────────────────────────────────────


class TestGameplayDriver:
    def test_gameplay_driven_prioritizes_games_with_more_clips(self, fresh_db):
        """Game with more clips should have higher priority."""
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import get_or_create_profile
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import (
            User, Game, GameplaySource, GameplayAsset,
        )

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            # Game A: 10 clips
            game_a = Game(canonical_name="GameA", slug="gamea")
            session.add(game_a)
            session.flush()
            src_a = GameplaySource(
                user_id=uid, game_id=game_a.id, filename="a.mp4",
                file_hash="ha", ingestion_status="ready",
            )
            session.add(src_a)
            session.flush()
            for i in range(10):
                session.add(GameplayAsset(source_id=src_a.id, start_sec=float(i), end_sec=float(i+1)))

            # Game B: 2 clips
            game_b = Game(canonical_name="GameB", slug="gameb")
            session.add(game_b)
            session.flush()
            src_b = GameplaySource(
                user_id=uid, game_id=game_b.id, filename="b.mp4",
                file_hash="hb", ingestion_status="ready",
            )
            session.add(src_b)
            session.flush()
            for i in range(2):
                session.add(GameplayAsset(source_id=src_b.id, start_sec=float(i), end_sec=float(i+1)))

            profile = get_or_create_profile(session, uid)
            intent = EditorialIntentBuilder().build(session, uid, profile)

            # GameA (10 clips) should have higher priority than GameB (2 clips)
            priorities = {g.name: g.priority for g in intent.priority_games}
            assert priorities["GameA"] > priorities["GameB"]

    def test_gameplay_driven_reduces_priority_for_covered_games(self, fresh_db):
        """Games recently covered should have reduced priority."""
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import get_or_create_profile
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import (
            User, Game, GameplaySource, GameplayAsset, Video,
        )

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            # Both games have same clips
            for name in ["GameA", "GameB"]:
                g = Game(canonical_name=name, slug=name.lower())
                session.add(g)
                session.flush()
                src = GameplaySource(
                    user_id=uid, game_id=g.id, filename=f"{name}.mp4",
                    file_hash=f"h{name}", ingestion_status="ready",
                )
                session.add(src)
                session.flush()
                for i in range(5):
                    session.add(GameplayAsset(source_id=src.id, start_sec=float(i), end_sec=float(i+1)))

            # But GameA was covered 3 times recently
            for _ in range(3):
                session.add(Video(user_id=uid, game_id=1, file_path="/tmp/x.mp4"))

            profile = get_or_create_profile(session, uid)
            intent = EditorialIntentBuilder().build(session, uid, profile)

            priorities = {g.name: g.priority for g in intent.priority_games}
            # GameB (no coverage) should have higher priority than GameA (3x covered)
            assert priorities["GameB"] > priorities["GameA"]

    def test_gameplay_driven_disabled_gives_equal_priority(self, fresh_db):
        """When gameplay_driven_collection is False, all games get equal priority."""
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import (
            get_or_create_profile,
            update_structured_fields,
        )
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import (
            User, Game, GameplaySource, GameplayAsset,
        )

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            # Game A: 10 clips, Game B: 2 clips
            for name, n_clips in [("GameA", 10), ("GameB", 2)]:
                g = Game(canonical_name=name, slug=name.lower())
                session.add(g)
                session.flush()
                src = GameplaySource(
                    user_id=uid, game_id=g.id, filename=f"{name}.mp4",
                    file_hash=f"h{name}", ingestion_status="ready",
                )
                session.add(src)
                session.flush()
                for i in range(n_clips):
                    session.add(GameplayAsset(source_id=src.id, start_sec=float(i), end_sec=float(i+1)))

            # Disable gameplay-driven collection
            update_structured_fields(session, uid, gameplay_driven_collection=False)
            profile = get_or_create_profile(session, uid)
            intent = EditorialIntentBuilder().build(session, uid, profile)

            priorities = {g.name: g.priority for g in intent.priority_games}
            # All should have equal priority (0.5)
            assert priorities["GameA"] == priorities["GameB"] == 0.5


# ── Exploration factor ───────────────────────────────────────────────────────


class TestExplorationFactor:
    def test_exploration_includes_random_kis(self, fresh_db, monkeypatch):
        """With exploration factor > 0, some queue slots are random."""
        from gpcg.api.automation_routes import _reconcile_idea_queue
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import (
            User, Game, GameplaySource, GameplayAsset, KnowledgeItem,
        )

        # Enable composite scoring + exploration
        monkeypatch.setenv("GPCG_COMPOSITE_SCORING_ENABLED", "true")
        monkeypatch.setenv("GPCG_EDITORIAL_EXPLORATION_FACTOR", "0.3")
        from gpcg.config import get_settings
        get_settings.cache_clear()

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            # Create a game with gameplay
            game = Game(canonical_name="Bully", slug="bully")
            session.add(game)
            session.flush()
            src = GameplaySource(
                user_id=uid, game_id=game.id, filename="b.mp4",
                file_hash="hb", ingestion_status="ready",
            )
            session.add(src)
            session.flush()
            session.add(GameplayAsset(source_id=src.id, start_sec=0, end_sec=5))

            # Create 20 KIs — 10 about Bully (high fit), 10 about other games (low fit)
            for i in range(10):
                session.add(KnowledgeItem(
                    user_id=uid, is_public=False,
                    title=f"Bully secret {i}", content=f"Content {i}",
                    item_type="curiosity", game_id=game.id,
                    editorial_score=70.0, content_hash=f"bh{i}",
                    status="fresh",
                ))
            for i in range(10):
                session.add(KnowledgeItem(
                    user_id=uid, is_public=False,
                    title=f"Other {i}", content=f"Other content {i}",
                    item_type="curiosity", game_id=None,
                    editorial_score=70.0, content_hash=f"oh{i}",
                    status="fresh",
                ))
            session.flush()

            # Request 10 slots with 30% exploration → 7 top + 3 random
            result = _reconcile_idea_queue(
                session, uid, {"max_queue_size": 10},
            )

            assert len(result) == 10
            # At least some should be "Other" KIs (from exploration)
            ki_ids = [r["ki_id"] for r in result]
            kis = [session.get(KnowledgeItem, kid) for kid in ki_ids]
            other_count = sum(1 for ki in kis if ki.game_id is None)
            # With 30% exploration, at least 1 should be from the "Other" pool
            assert other_count >= 1

    def test_exploration_zero_gives_no_random(self, fresh_db, monkeypatch):
        """With exploration factor = 0, all slots are top-scored."""
        from gpcg.api.automation_routes import _reconcile_idea_queue
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import (
            User, Game, GameplaySource, GameplayAsset, KnowledgeItem,
        )

        monkeypatch.setenv("GPCG_COMPOSITE_SCORING_ENABLED", "true")
        monkeypatch.setenv("GPCG_EDITORIAL_EXPLORATION_FACTOR", "0.0")
        from gpcg.config import get_settings
        get_settings.cache_clear()

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            game = Game(canonical_name="Bully", slug="bully")
            session.add(game)
            session.flush()
            src = GameplaySource(
                user_id=uid, game_id=game.id, filename="b.mp4",
                file_hash="hb", ingestion_status="ready",
            )
            session.add(src)
            session.flush()
            session.add(GameplayAsset(source_id=src.id, start_sec=0, end_sec=5))

            # 5 Bully KIs (high fit), 5 Other KIs (low fit)
            for i in range(5):
                session.add(KnowledgeItem(
                    user_id=uid, is_public=False,
                    title=f"Bully {i}", content=f"Content {i}",
                    item_type="curiosity", game_id=game.id,
                    editorial_score=70.0, content_hash=f"bh{i}",
                    status="fresh",
                ))
            for i in range(5):
                session.add(KnowledgeItem(
                    user_id=uid, is_public=False,
                    title=f"Other {i}", content=f"Other {i}",
                    item_type="curiosity", game_id=None,
                    editorial_score=70.0, content_hash=f"oh{i}",
                    status="fresh",
                ))
            session.flush()

            result = _reconcile_idea_queue(
                session, uid, {"max_queue_size": 5},
            )

            assert len(result) == 5
            # With 0 exploration, all should be Bully KIs (highest fit)
            ki_ids = [r["ki_id"] for r in result]
            kis = [session.get(KnowledgeItem, kid) for kid in ki_ids]
            assert all(ki.game_id == game.id for ki in kis)
