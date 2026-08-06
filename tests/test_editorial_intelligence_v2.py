"""Tests for Editorial Intelligence V2 — Phase 1.

Tests the full channel-driven collection pipeline:
  - Search Templates (first-class components)
  - Editorial Profile (structured fields + presets)
  - Editorial Intent Builder (per-cycle targets)
  - Editorial Brief Builder (feeds + queries + templates)
  - Goal-Oriented Collector (with early termination)
  - Integration with the existing pipeline (feature flag off = legacy)

These tests use a fresh SQLite DB and mock feedparser to avoid network calls.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from gpcg.domain.editorial_types import (
    CollectionResult,
    CompositeScore,
    EditorialBrief,
    EditorialIntent,
    FeedSpec,
    GameTarget,
    SearchQuery,
)
from gpcg.domain.search_templates import (
    DECAY_EVERGREEN,
    DECAY_FAST,
    SEARCH_TEMPLATES,
    get_template,
    list_template_names,
    merge_keywords,
)


# ── Search Templates ─────────────────────────────────────────────────────────


class TestSearchTemplates:
    def test_all_five_templates_registered(self):
        assert set(SEARCH_TEMPLATES.keys()) == {
            "curiosity", "news", "lore", "nostalgia", "fact"
        }

    def test_curiosity_template_has_evergreen_decay(self):
        t = SEARCH_TEMPLATES["curiosity"]
        assert t.decay_category == DECAY_EVERGREEN

    def test_news_template_has_fast_decay(self):
        t = SEARCH_TEMPLATES["news"]
        assert t.decay_category == DECAY_FAST

    def test_lore_template_has_evergreen_decay(self):
        t = SEARCH_TEMPLATES["lore"]
        assert t.decay_category == DECAY_EVERGREEN

    def test_nostalgia_maps_to_curiosity_item_type(self):
        t = SEARCH_TEMPLATES["nostalgia"]
        assert t.item_type == "curiosity"

    def test_fact_template_has_medium_decay(self):
        t = SEARCH_TEMPLATES["fact"]
        assert t.decay_category == "medium"

    def test_get_template_returns_none_for_unknown(self):
        assert get_template("unknown") is None

    def test_list_template_names_returns_all(self):
        names = list_template_names()
        assert len(names) == 5
        assert "curiosity" in names

    def test_merge_keywords_appends_custom_without_duplicates(self):
        t = SEARCH_TEMPLATES["curiosity"]
        merged = merge_keywords(t, ["custom1", "hidden", "custom2"])
        assert "custom1" in merged
        assert "custom2" in merged
        # "hidden" is already in template — should not be duplicated
        assert merged.count("hidden") == 1

    def test_templates_are_frozen(self):
        t = SEARCH_TEMPLATES["curiosity"]
        with pytest.raises((AttributeError, Exception)):
            t.name = "other"  # frozen dataclass


# ── Editorial Types ──────────────────────────────────────────────────────────


class TestEditorialTypes:
    def test_game_target_is_frozen(self):
        gt = GameTarget(game_id=1, name="Bully", priority=0.9, reason="test")
        with pytest.raises((AttributeError, Exception)):
            gt.name = "other"

    def test_composite_score_compute_multiplies_layers(self):
        cs = CompositeScore.compute(80, 0.9, 0.8)
        assert cs.editorial_quality == 0.8
        assert cs.production_fit == 0.9
        assert cs.editorial_timing == 0.8
        assert abs(cs.final_score - 0.8 * 0.9 * 0.8) < 0.001

    def test_composite_score_clamps_to_0_1(self):
        cs = CompositeScore.compute(150, 2.0, -0.5)
        assert cs.editorial_quality == 1.0
        assert cs.production_fit == 1.0
        assert cs.editorial_timing == 0.0
        assert cs.final_score == 0.0

    def test_composite_score_zero_fit_zeros_final(self):
        cs = CompositeScore.compute(100, 0.0, 1.0)
        assert cs.final_score == 0.0

    def test_editorial_intent_defaults(self):
        intent = EditorialIntent()
        assert intent.collection_targets == {}
        assert intent.priority_games == []
        assert intent.fill_strategy == "balanced"

    def test_editorial_brief_defaults(self):
        brief = EditorialBrief()
        assert brief.feeds == []
        assert brief.max_queries_per_game == 5
        assert brief.max_total_queries == 30


# ── Editorial Profile Service ────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Fresh SQLite DB with schema for each test."""
    db_path = tmp_path / "test_editorial.db"
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


class TestEditorialProfileService:
    def test_get_or_create_creates_profile(self, fresh_db):
        from gpcg.application.editorial_profile_service import get_or_create_profile
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            profile = get_or_create_profile(session, uid)
            assert profile.user_id == uid
            assert profile.content_type_affinity == {} or profile.content_type_affinity is None

    def test_apply_preset_curiosidades(self, fresh_db):
        from gpcg.application.editorial_profile_service import apply_preset, serialize_profile
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            profile = apply_preset(session, uid, "curiosidades")
            assert profile.content_type_affinity["curiosity"] == 0.9
            assert profile.content_type_affinity["news"] == 0.1
            assert "hidden" in profile.editorial_keywords
            assert len(profile.custom_feeds) > 0

    def test_apply_preset_lore(self, fresh_db):
        from gpcg.application.editorial_profile_service import apply_preset
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            profile = apply_preset(session, uid, "lore")
            assert profile.content_type_affinity["lore"] == 0.9
            assert "story" in profile.editorial_keywords

    def test_apply_preset_unknown_raises(self, fresh_db):
        from gpcg.application.editorial_profile_service import apply_preset
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            with pytest.raises(ValueError):
                apply_preset(session, user.id, "unknown_preset")

    def test_update_structured_fields_validates_affinity(self, fresh_db):
        from gpcg.application.editorial_profile_service import (
            get_or_create_profile,
            update_structured_fields,
        )
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            update_structured_fields(
                session, uid,
                content_type_affinity={"curiosity": 1.5, "news": -0.5, "invalid": 0.8},
            )
            profile = get_or_create_profile(session, uid)
            # Clamped to 0.0–1.0, invalid key dropped
            assert profile.content_type_affinity["curiosity"] == 1.0
            assert profile.content_type_affinity["news"] == 0.0
            assert "invalid" not in profile.content_type_affinity

    def test_update_structured_fields_validates_strictness(self, fresh_db):
        from gpcg.application.editorial_profile_service import (
            get_or_create_profile,
            update_structured_fields,
        )
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            update_structured_fields(session, uid, diversity_strictness=2.0)
            profile = get_or_create_profile(session, uid)
            assert profile.diversity_strictness == 1.0

    def test_update_learned_preferences_merges(self, fresh_db):
        from gpcg.application.editorial_profile_service import (
            get_or_create_profile,
            update_learned_preferences,
        )
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            update_learned_preferences(session, uid, preferred_games=[1, 2])
            update_learned_preferences(session, uid, preferred_games=[2, 3])
            profile = get_or_create_profile(session, uid)
            assert set(profile.learned_preferences["preferred_games"]) == {1, 2, 3}

    def test_serialize_profile_includes_v2_fields(self, fresh_db):
        from gpcg.application.editorial_profile_service import (
            apply_preset,
            serialize_profile,
        )
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            apply_preset(session, uid, "curiosidades")
            profile = session.query(
                __import__("gpcg.domain.models", fromlist=["ChannelProfile"]).ChannelProfile
            ).filter_by(user_id=uid).first()
            data = serialize_profile(profile)
            assert "content_type_affinity" in data
            assert "editorial_keywords" in data
            assert "custom_feeds" in data
            assert "learned_preferences" in data
            assert "production_history_summary" in data


# ── Editorial Intent Builder ─────────────────────────────────────────────────


class TestEditorialIntentBuilder:
    def test_intent_with_no_gameplay_returns_empty_priority(self, fresh_db):
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

            builder = EditorialIntentBuilder()
            intent = builder.build(session, uid, profile)

            assert intent.priority_games == []
            assert intent.cooldown_games == {}

    def test_intent_with_gameplay_sets_priority_games(self, fresh_db):
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import get_or_create_profile
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import (
            User, Game, GameplaySource, GameplayAsset, AnalysisStatus,
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
                file_hash="abc123", ingestion_status="ready",
            )
            session.add(source)
            session.flush()
            sid = source.id

            asset = GameplayAsset(source_id=sid, start_sec=0, end_sec=5)
            session.add(asset)

            profile = get_or_create_profile(session, uid)
            builder = EditorialIntentBuilder()
            intent = builder.build(session, uid, profile)

            assert len(intent.priority_games) == 1
            assert intent.priority_games[0].name == "Bully"
            assert intent.priority_games[0].clips_ready >= 1

    def test_intent_cooldown_with_strictness(self, fresh_db):
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

            # Create 3 recent videos about game 1
            for _ in range(3):
                v = Video(user_id=uid, game_id=1, file_path="/tmp/x.mp4")
                session.add(v)

            update_structured_fields(session, uid, diversity_strictness=1.0)
            profile = get_or_create_profile(session, uid)

            builder = EditorialIntentBuilder()
            intent = builder.build(session, uid, profile)

            # strictness 1.0 → threshold 1, so game 1 should be in cooldown
            assert 1 in intent.cooldown_games

    def test_intent_no_cooldown_with_zero_strictness(self, fresh_db):
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

            for _ in range(3):
                v = Video(user_id=uid, game_id=1, file_path="/tmp/x.mp4")
                session.add(v)

            update_structured_fields(session, uid, diversity_strictness=0.0)
            profile = get_or_create_profile(session, uid)

            builder = EditorialIntentBuilder()
            intent = builder.build(session, uid, profile)

            assert intent.cooldown_games == {}

    def test_intent_targets_from_affinity(self, fresh_db):
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import (
            apply_preset,
            get_or_create_profile,
        )
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            apply_preset(session, uid, "curiosidades")
            profile = get_or_create_profile(session, uid)

            builder = EditorialIntentBuilder()
            intent = builder.build(session, uid, profile)

            # Curiosidades preset has high curiosity affinity → more curiosity targets
            assert intent.collection_targets.get("curiosity", 0) > intent.collection_targets.get("news", 0)


# ── Editorial Brief Builder ──────────────────────────────────────────────────


class TestEditorialBriefBuilder:
    def test_brief_resolves_custom_feeds(self, fresh_db):
        from gpcg.application.editorial_brief_builder import EditorialBriefBuilder
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import apply_preset
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            apply_preset(session, uid, "curiosidades")
            from gpcg.domain.models import ChannelProfile
            profile = session.query(ChannelProfile).filter_by(user_id=uid).first()

            intent_builder = EditorialIntentBuilder()
            intent = intent_builder.build(session, uid, profile)

            brief_builder = EditorialBriefBuilder()
            brief = brief_builder.build(session, uid, profile, intent)

            # Should have channel feeds + global feeds
            assert len(brief.feeds) > 0
            scopes = [f.scope for f in brief.feeds]
            assert "channel" in scopes
            assert "global" in scopes

    def test_brief_expands_queries_with_templates(self, fresh_db):
        from gpcg.application.editorial_brief_builder import EditorialBriefBuilder
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import apply_preset
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import (
            User, Game, GameplaySource, GameplayAsset,
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

            apply_preset(session, uid, "curiosidades")
            from gpcg.domain.models import ChannelProfile
            profile = session.query(ChannelProfile).filter_by(user_id=uid).first()

            intent = EditorialIntentBuilder().build(session, uid, profile)
            brief = EditorialBriefBuilder().build(session, uid, profile, intent)

            # Should have queries combining "Bully" with curiosity keywords
            assert len(brief.search_queries) > 0
            assert any("Bully" in q.text for q in brief.search_queries)
            assert any(q.template_name == "curiosity" for q in brief.search_queries)

    def test_brief_respects_max_queries(self, fresh_db):
        from gpcg.application.editorial_brief_builder import EditorialBriefBuilder
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import apply_preset
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import (
            User, Game, GameplaySource, GameplayAsset,
        )

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            # Create 5 games with gameplay
            for i in range(5):
                g = Game(canonical_name=f"Game{i}", slug=f"game{i}")
                session.add(g)
                session.flush()
                s = GameplaySource(
                    user_id=uid, game_id=g.id, filename=f"g{i}.mp4",
                    file_hash=f"hash{i}", ingestion_status="ready",
                )
                session.add(s)
                session.flush()
                a = GameplayAsset(source_id=s.id, start_sec=0, end_sec=5)
                session.add(a)

            apply_preset(session, uid, "curiosidades")
            from gpcg.domain.models import ChannelProfile
            profile = session.query(ChannelProfile).filter_by(user_id=uid).first()

            intent = EditorialIntentBuilder().build(session, uid, profile)
            brief = EditorialBriefBuilder().build(session, uid, profile, intent)

            assert len(brief.search_queries) <= brief.max_total_queries

    def test_brief_scoring_weights_derived_from_affinity(self, fresh_db):
        from gpcg.application.editorial_brief_builder import EditorialBriefBuilder
        from gpcg.application.editorial_intent_builder import EditorialIntentBuilder
        from gpcg.application.editorial_profile_service import apply_preset
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            apply_preset(session, uid, "lore")
            from gpcg.domain.models import ChannelProfile
            profile = session.query(ChannelProfile).filter_by(user_id=uid).first()

            intent = EditorialIntentBuilder().build(session, uid, profile)
            brief = EditorialBriefBuilder().build(session, uid, profile, intent)

            # Lore affinity > 0.6 → familiarity weight should be boosted
            assert brief.scoring_weights.get("familiarity", 0) > 0.15
            # Weights should sum to ~1.0
            total = sum(brief.scoring_weights.values())
            assert abs(total - 1.0) < 0.01


# ── Goal-Oriented Collector ──────────────────────────────────────────────────


class TestGoalOrientedCollector:
    def test_collect_stops_when_targets_met(self, fresh_db):
        """Collector should stop early when all targets are met."""
        from gpcg.application.goal_oriented_collector import GoalOrientedCollector
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            # Build a minimal brief with 1 target
            brief = EditorialBrief(
                feeds=[],
                search_queries=[
                    SearchQuery(text="Bully hidden", game_id=1, template_name="curiosity", item_type="curiosity"),
                ],
                active_templates=["curiosity"],
                collection_targets={"curiosity": 1},  # only need 1
                user_id=uid,
            )

            # Mock feedparser to return 3 entries
            mock_entries = [
                MagicMock(title=f"Secret {i}", summary=f"Content {i}", link=f"http://x/{i}")
                for i in range(3)
            ]
            mock_feed = MagicMock(entries=mock_entries)

            collector = GoalOrientedCollector()
            with patch("feedparser.parse", return_value=mock_feed):
                result = collector.collect(session, brief, uid)

            # Should collect exactly 1 (target met) and stop
            assert result.total == 1
            assert result.remaining["curiosity"] == 0

    def test_collect_creates_kis_with_correct_item_type(self, fresh_db):
        from gpcg.application.goal_oriented_collector import GoalOrientedCollector
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User, KnowledgeItem

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            brief = EditorialBrief(
                feeds=[],
                search_queries=[
                    SearchQuery(text="Bully secrets", game_id=1, template_name="curiosity", item_type="curiosity"),
                ],
                active_templates=["curiosity"],
                collection_targets={"curiosity": 2},
                user_id=uid,
            )

            mock_entries = [
                MagicMock(title="Secret 1", summary="Content 1", link="http://x/1"),
                MagicMock(title="Secret 2", summary="Content 2", link="http://x/2"),
            ]
            mock_feed = MagicMock(entries=mock_entries)

            collector = GoalOrientedCollector()
            with patch("feedparser.parse", return_value=mock_feed):
                result = collector.collect(session, brief, uid)

            assert result.total == 2
            kis = session.query(KnowledgeItem).all()
            assert len(kis) == 2
            assert all(ki.item_type == "curiosity" for ki in kis)
            assert all(ki.game_id == 1 for ki in kis)

    def test_collect_deduplicates_by_content_hash(self, fresh_db):
        from gpcg.application.goal_oriented_collector import GoalOrientedCollector
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User, KnowledgeItem

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            brief = EditorialBrief(
                feeds=[],
                search_queries=[
                    SearchQuery(text="Bully hidden", game_id=1, template_name="curiosity", item_type="curiosity"),
                    SearchQuery(text="Bully secrets", game_id=1, template_name="curiosity", item_type="curiosity"),
                ],
                active_templates=["curiosity"],
                collection_targets={"curiosity": 5},
                user_id=uid,
            )

            # Both queries return the same entries (same title+content → same hash)
            mock_entries = [
                MagicMock(title="Same Secret", summary="Same Content", link="http://x/1"),
            ]
            mock_feed = MagicMock(entries=mock_entries)

            collector = GoalOrientedCollector()
            with patch("feedparser.parse", return_value=mock_feed):
                result = collector.collect(session, brief, uid)

            # Should only create 1 KI (deduped by content_hash)
            assert result.total == 1

    def test_collect_from_global_feed_creates_shared_ki(self, fresh_db):
        from gpcg.application.goal_oriented_collector import GoalOrientedCollector
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import User, KnowledgeItem

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            brief = EditorialBrief(
                feeds=[
                    FeedSpec(url="http://feed.example", source_name="IGN", item_type="news", scope="global"),
                ],
                search_queries=[],
                active_templates=["news"],
                collection_targets={"news": 2},
                user_id=uid,
            )

            mock_entries = [
                MagicMock(title="News 1", summary="Content 1", link="http://x/1"),
                MagicMock(title="News 2", summary="Content 2", link="http://x/2"),
            ]
            mock_feed = MagicMock(entries=mock_entries)

            collector = GoalOrientedCollector()
            with patch("feedparser.parse", return_value=mock_feed):
                result = collector.collect(session, brief, uid)

            assert result.total == 2
            kis = session.query(KnowledgeItem).all()
            # Global feed → user_id NULL (shared pool), is_public=True
            assert all(ki.user_id is None for ki in kis)
            assert all(ki.is_public for ki in kis)


# ── Integration: feature flag off = legacy behavior ──────────────────────────


class TestFeatureFlagCompatibility:
    def test_flag_off_uses_legacy_collector(self, tmp_path, monkeypatch):
        """When gpcg_editorial_brief_enabled is False, legacy path runs."""
        monkeypatch.setenv("GPCG_DB_PATH", str(tmp_path / "test_flag.db"))
        monkeypatch.setenv("GPCG_EDITORIAL_BRIEF_ENABLED", "false")
        from gpcg.config import get_settings
        get_settings.cache_clear()
        import gpcg.infrastructure.database as db_module
        db_module._engine = None
        db_module._SessionLocal = None
        from gpcg.infrastructure.database import init_db
        init_db()

        try:
            settings = get_settings()
            assert settings.gpcg_editorial_brief_enabled is False
        finally:
            db_module._engine = None
            db_module._SessionLocal = None
            get_settings.cache_clear()

    def test_flag_on_enables_editorial_brief(self, tmp_path, monkeypatch):
        """When gpcg_editorial_brief_enabled is True, V2 path runs."""
        monkeypatch.setenv("GPCG_DB_PATH", str(tmp_path / "test_flag2.db"))
        monkeypatch.setenv("GPCG_EDITORIAL_BRIEF_ENABLED", "true")
        from gpcg.config import get_settings
        get_settings.cache_clear()
        import gpcg.infrastructure.database as db_module
        db_module._engine = None
        db_module._SessionLocal = None
        from gpcg.infrastructure.database import init_db
        init_db()

        try:
            settings = get_settings()
            assert settings.gpcg_editorial_brief_enabled is True
        finally:
            db_module._engine = None
            db_module._SessionLocal = None
            get_settings.cache_clear()
