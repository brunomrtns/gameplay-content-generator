"""Tests for Editorial Intelligence V2 — Phase 4 (Feedback Loop).

Tests:
  - FeedbackPropagator: rejection penalty, manual-add boost, production history
  - Signal recording in editorial_signals table
  - Learned preferences update
  - Propagation via embeddings (with mock embeddings)
  - Feature flag gating (feedback loop off = no propagation)
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Fresh SQLite DB with schema for each test."""
    db_path = tmp_path / "test_v2_phase4.db"
    monkeypatch.setenv("GPCG_DB_PATH", str(db_path))
    monkeypatch.setenv("GPCG_FEEDBACK_LOOP_ENABLED", "true")
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


# ── Feedback Propagator ──────────────────────────────────────────────────────


class TestFeedbackPropagator:
    def test_rejection_records_signal(self, fresh_db):
        """Rejecting a KI records a signal in editorial_signals."""
        from gpcg.application.feedback_propagator import (
            FeedbackPropagator,
            SIGNAL_REJECTION_PENALTY,
        )
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, KnowledgeItem, EditorialSignal

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            ki = _make_ki(user_id=uid, content_hash="h1")
            session.add(ki)
            session.flush()
            ki_id = ki.id

            propagator = FeedbackPropagator()
            with patch("gpcg.application.feedback_propagator.get_knowledge_item_embedding", return_value=None):
                count = propagator.propagate_rejection(session, uid, ki_id)

            # Signal should be recorded even if no embeddings
            signals = session.query(EditorialSignal).all()
            assert len(signals) == 1
            assert signals[0].signal_type == SIGNAL_REJECTION_PENALTY
            assert signals[0].ki_id == ki_id
            assert signals[0].signal_value < 0  # penalty

    def test_manual_add_records_signal(self, fresh_db):
        from gpcg.application.feedback_propagator import (
            FeedbackPropagator,
            SIGNAL_MANUAL_ADD_BOOST,
        )
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, KnowledgeItem, EditorialSignal

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            ki = _make_ki(user_id=uid, content_hash="h1")
            session.add(ki)
            session.flush()
            ki_id = ki.id

            propagator = FeedbackPropagator()
            with patch("gpcg.application.feedback_propagator.get_knowledge_item_embedding", return_value=None):
                count = propagator.propagate_manual_add(session, uid, ki_id)

            signals = session.query(EditorialSignal).all()
            assert len(signals) == 1
            assert signals[0].signal_type == SIGNAL_MANUAL_ADD_BOOST
            assert signals[0].signal_value > 0  # boost

    def test_rejection_updates_learned_preferences(self, fresh_db):
        """Rejecting a KI adds its title to avoided_topics."""
        from gpcg.application.feedback_propagator import FeedbackPropagator
        from gpcg.application.editorial_profile_service import get_or_create_profile
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, KnowledgeItem

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            ki = _make_ki(title="Clickbait about Minecraft", user_id=uid, content_hash="h1")
            session.add(ki)
            session.flush()

            propagator = FeedbackPropagator()
            with patch("gpcg.application.feedback_propagator.get_knowledge_item_embedding", return_value=None):
                propagator.propagate_rejection(session, uid, ki.id)

            profile = get_or_create_profile(session, uid)
            avoided = profile.learned_preferences.get("avoided_topics", [])
            assert any("Clickbait" in t for t in avoided)

    def test_manual_add_updates_learned_preferences(self, fresh_db):
        """Manually adding a KI with game_id adds the game to preferred_games."""
        from gpcg.application.feedback_propagator import FeedbackPropagator
        from gpcg.application.editorial_profile_service import get_or_create_profile
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, KnowledgeItem
        from gpcg.domains.games.models import Game

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            game = Game(canonical_name="Bully", slug="bully")
            session.add(game)
            session.flush()

            ki = _make_ki(game_id=game.id, user_id=uid, content_hash="h1")
            session.add(ki)
            session.flush()

            propagator = FeedbackPropagator()
            with patch("gpcg.application.feedback_propagator.get_knowledge_item_embedding", return_value=None):
                propagator.propagate_manual_add(session, uid, ki.id)

            profile = get_or_create_profile(session, uid)
            preferred = profile.learned_preferences.get("preferred_games", [])
            assert game.id in preferred

    def test_production_updates_history(self, fresh_db):
        """Recording a production event updates production_history_summary."""
        from gpcg.application.feedback_propagator import FeedbackPropagator
        from gpcg.application.editorial_profile_service import get_or_create_profile
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, Video
        from gpcg.domains.games.models import Game

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            game = Game(canonical_name="Bully", slug="bully")
            session.add(game)
            session.flush()

            video = Video(user_id=uid, game_id=game.id, file_path="/tmp/v.mp4")
            session.add(video)
            session.flush()

            propagator = FeedbackPropagator()
            propagator.record_production(session, uid, video.id)

            profile = get_or_create_profile(session, uid)
            history = profile.production_history_summary
            assert history.get("total_videos") == 1
            assert any(g["game_id"] == game.id for g in history.get("top_games", []))

    def test_propagation_via_embeddings_penalizes_similar(self, fresh_db):
        """Rejection of a KI penalizes similar KIs via embeddings."""
        from gpcg.application.feedback_propagator import FeedbackPropagator
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, KnowledgeItem

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            # Source KI (to be rejected)
            ki1 = _make_ki(title="Bully secrets", user_id=uid, content_hash="h1", editorial_score=70.0)
            session.add(ki1)
            session.flush()

            # Similar KI (should be penalized)
            ki2 = _make_ki(title="Bully hidden content", user_id=uid, content_hash="h2", editorial_score=70.0)
            session.add(ki2)
            session.flush()

            # Mock embeddings: both KIs have very similar embeddings
            def mock_get_embedding(session, item_id):
                if item_id == ki1.id:
                    return [1.0, 0.9, 0.1]
                elif item_id == ki2.id:
                    return [0.95, 0.85, 0.15]  # very similar to ki1
                return None

            propagator = FeedbackPropagator()
            with patch(
                "gpcg.application.feedback_propagator.get_knowledge_item_embedding",
                side_effect=mock_get_embedding,
            ):
                count = propagator.propagate_rejection(session, uid, ki1.id)

            # ki2 should have been penalized
            assert count >= 1
            session.refresh(ki2)
            assert ki2.editorial_score < 70.0  # penalized

    def test_propagation_skips_dissimilar_kis(self, fresh_db):
        """Rejection should NOT penalize dissimilar KIs."""
        from gpcg.application.feedback_propagator import FeedbackPropagator
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, KnowledgeItem

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            ki1 = _make_ki(title="Bully secrets", user_id=uid, content_hash="h1", editorial_score=70.0)
            session.add(ki1)
            session.flush()

            # Dissimilar KI
            ki2 = _make_ki(title="Minecraft patch notes", user_id=uid, content_hash="h2", editorial_score=70.0)
            session.add(ki2)
            session.flush()

            def mock_get_embedding(session, item_id):
                if item_id == ki1.id:
                    return [1.0, 0.0, 0.0]
                elif item_id == ki2.id:
                    return [0.0, 1.0, 0.0]  # orthogonal — completely dissimilar
                return None

            propagator = FeedbackPropagator()
            with patch(
                "gpcg.application.feedback_propagator.get_knowledge_item_embedding",
                side_effect=mock_get_embedding,
            ):
                count = propagator.propagate_rejection(session, uid, ki1.id)

            # ki2 should NOT have been penalized (dissimilar)
            assert count == 0
            session.refresh(ki2)
            assert ki2.editorial_score == 70.0  # unchanged

    def test_feedback_disabled_skips_propagation(self, tmp_path, monkeypatch):
        """When gpcg_feedback_loop_enabled is False, no propagation happens."""
        monkeypatch.setenv("GPCG_DB_PATH", str(tmp_path / "test_disabled.db"))
        monkeypatch.setenv("GPCG_FEEDBACK_LOOP_ENABLED", "false")
        from gpcg.config import get_settings
        get_settings.cache_clear()
        import gpcg.infrastructure.database as db_module
        db_module._engine = None
        db_module._SessionLocal = None
        from gpcg.infrastructure.database import init_db
        init_db()

        try:
            from gpcg.application.feedback_propagator import FeedbackPropagator
            from gpcg.infrastructure.database import session_scope
            from gpcg.core.models import User, KnowledgeItem, EditorialSignal

            with session_scope() as session:
                user = User(email="test@example.com", name="test")
                session.add(user)
                session.flush()
                uid = user.id

                ki = _make_ki(user_id=uid, content_hash="h1")
                session.add(ki)
                session.flush()

                propagator = FeedbackPropagator()
                count = propagator.propagate_rejection(session, uid, ki.id)

                assert count == 0
                # No signal should be recorded
                signals = session.query(EditorialSignal).all()
                assert len(signals) == 0
        finally:
            db_module._engine = None
            db_module._SessionLocal = None
            get_settings.cache_clear()

    def test_production_records_signal(self, fresh_db):
        """Recording production creates an editorial_signal entry."""
        from gpcg.application.feedback_propagator import (
            FeedbackPropagator,
            SIGNAL_PRODUCTION_HISTORY,
        )
        from gpcg.infrastructure.database import session_scope
        from gpcg.core.models import User, Video, EditorialSignal

        with session_scope() as session:
            user = User(email="test@example.com", name="test")
            session.add(user)
            session.flush()
            uid = user.id

            video = Video(user_id=uid, file_path="/tmp/v.mp4")
            session.add(video)
            session.flush()

            propagator = FeedbackPropagator()
            propagator.record_production(session, uid, video.id)

            signals = session.query(EditorialSignal).all()
            assert len(signals) == 1
            assert signals[0].signal_type == SIGNAL_PRODUCTION_HISTORY
            assert signals[0].source_video_id == video.id
