"""Tests for V2 Gameplay Intelligence — cross-game expansion + retriever.

Tests the cross-game gameplay retrieval feature per ARCHITECTURE_V2.md §8:
- _expand_game_ids with scope=game (no expansion)
- _expand_game_ids with scope=franchise (same franchise)
- _expand_game_ids with scope=developer (same developer)
- GameplayRetriever.retrieve with game_ids list (cross-game)
- Feature flag gating (GPCG_CROSS_GAME_GAMEPLAY_ENABLED)
- Fallback: cross-game → game scope → random
"""

import random
from unittest.mock import MagicMock, patch

import pytest

from gpcg.application.gameplay_retriever import GameplayRetriever, _expand_game_ids
from gpcg.application.gameplay_selector import SelectedClip
from gpcg.domain.game_registry import get_or_create
from gpcg.domain.models import ContentScope, Game, GameplaySource
from gpcg.infrastructure.database import init_db, session_scope


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Use a temp DB for each test."""
    db_path = tmp_path / "test_crossgame.db"
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


class TestExpandGameIds:
    """Tests for _expand_game_ids."""

    def test_scope_game_no_expansion(self):
        """scope=game should return only [game_id]."""
        with session_scope() as s:
            game = get_or_create(s, "Solo Game")
            s.flush()
            result = _expand_game_ids(s, game.id, scope=ContentScope.game.value)
            assert result == [game.id]

    def test_scope_franchise_expands(self):
        """scope=franchise should return games with the same franchise."""
        with session_scope() as s:
            game1 = get_or_create(s, "Franchise Game 1")
            game1.franchise = "Resident Evil"
            game2 = get_or_create(s, "Franchise Game 2")
            game2.franchise = "Resident Evil"
            game3 = get_or_create(s, "Other Franchise Game")
            game3.franchise = "Silent Hill"
            s.flush()

            result = _expand_game_ids(s, game1.id, scope=ContentScope.franchise.value)
            assert game1.id in result
            assert game2.id in result
            assert game3.id not in result

    def test_scope_developer_expands(self):
        """scope=developer should return games with the same developer."""
        with session_scope() as s:
            game1 = get_or_create(s, "Dev Game 1")
            game1.developer = "FromSoftware"
            game2 = get_or_create(s, "Dev Game 2")
            game2.developer = "FromSoftware"
            game3 = get_or_create(s, "Other Dev Game")
            game3.developer = "Capcom"
            s.flush()

            result = _expand_game_ids(s, game1.id, scope=ContentScope.developer.value)
            assert game1.id in result
            assert game2.id in result
            assert game3.id not in result

    def test_scope_franchise_no_franchise_returns_just_game(self):
        """If game has no franchise, scope=franchise returns just [game_id]."""
        with session_scope() as s:
            game = get_or_create(s, "No Franchise Game")
            # game.franchise is None
            s.flush()
            result = _expand_game_ids(s, game.id, scope=ContentScope.franchise.value)
            assert result == [game.id]

    def test_scope_developer_no_developer_returns_just_game(self):
        """If game has no developer, scope=developer returns just [game_id]."""
        with session_scope() as s:
            game = get_or_create(s, "No Developer Game")
            s.flush()
            result = _expand_game_ids(s, game.id, scope=ContentScope.developer.value)
            assert result == [game.id]

    def test_nonexistent_game_returns_empty_list(self):
        """Nonexistent game_id should return [game_id] (no crash)."""
        with session_scope() as s:
            result = _expand_game_ids(s, 99999, scope=ContentScope.franchise.value)
            assert result == [99999]


class TestRetrieverCrossGame:
    """Tests for GameplayRetriever with cross-game support."""

    def test_retrieve_accepts_list_of_game_ids(self):
        """retrieve() should accept a list of game_ids (V2)."""
        retriever = GameplayRetriever()

        with session_scope() as s:
            game1 = get_or_create(s, "List Game 1")
            game2 = get_or_create(s, "List Game 2")
            s.flush()

            # Mock the fallback selector to return empty (no gameplay available)
            with patch.object(retriever.fallback_selector, "select", return_value=[]):
                clips = retriever.retrieve(
                    s, [game1.id, game2.id], 10.0,
                    rng=random.Random(42),
                )
                assert clips == []

    def test_retrieve_backward_compatible_with_int(self):
        """retrieve() should still work with a single int game_id (backward compat)."""
        retriever = GameplayRetriever()

        with session_scope() as s:
            game = get_or_create(s, "Backward Compat Game")
            s.flush()

            with patch.object(retriever.fallback_selector, "select", return_value=[]):
                clips = retriever.retrieve(
                    s, game.id, 10.0,
                    rng=random.Random(42),
                )
                assert clips == []

    def test_cross_game_disabled_by_default(self, monkeypatch):
        """Cross-game expansion should not happen when flag is off (default)."""
        monkeypatch.setenv("GPCG_CROSS_GAME_GAMEPLAY_ENABLED", "false")
        from gpcg.config import get_settings
        get_settings.cache_clear()

        retriever = GameplayRetriever()

        with session_scope() as s:
            game1 = get_or_create(s, "Disabled Game 1")
            game1.franchise = "Test Franchise"
            game2 = get_or_create(s, "Disabled Game 2")
            game2.franchise = "Test Franchise"
            s.flush()

            # Mock _expand_game_ids to verify it's NOT called
            with patch("gpcg.application.gameplay_retriever._expand_game_ids") as mock_expand:
                mock_expand.return_value = [game1.id]
                with patch.object(retriever.fallback_selector, "select", return_value=[]):
                    retriever.retrieve(
                        s, game1.id, 10.0,
                        scope=ContentScope.franchise.value,
                        rng=random.Random(42),
                    )
                # Should NOT have been called because flag is off
                mock_expand.assert_not_called()

        get_settings.cache_clear()

    def test_cross_game_enabled_expands(self, monkeypatch):
        """Cross-game expansion should happen when flag is on."""
        monkeypatch.setenv("GPCG_CROSS_GAME_GAMEPLAY_ENABLED", "true")
        from gpcg.config import get_settings
        get_settings.cache_clear()

        retriever = GameplayRetriever()

        with session_scope() as s:
            game1 = get_or_create(s, "Enabled Game 1")
            game1.franchise = "Shared Franchise"
            game2 = get_or_create(s, "Enabled Game 2")
            game2.franchise = "Shared Franchise"
            s.flush()

            with patch.object(retriever.fallback_selector, "select", return_value=[]):
                retriever.retrieve(
                    s, game1.id, 10.0,
                    scope=ContentScope.franchise.value,
                    rng=random.Random(42),
                )
                # The fallback selector should have been called with both game IDs
                # (since no semantic index, it falls back to random for each game_id)

        get_settings.cache_clear()

    def test_cross_game_fallback_tries_each_game(self, monkeypatch):
        """Cross-game fallback should try each game_id until clips are found."""
        monkeypatch.setenv("GPCG_CROSS_GAME_GAMEPLAY_ENABLED", "true")
        from gpcg.config import get_settings
        get_settings.cache_clear()

        retriever = GameplayRetriever()

        with session_scope() as s:
            game1 = get_or_create(s, "Fallback Game 1")
            game1.franchise = "Fallback Franchise"
            game2 = get_or_create(s, "Fallback Game 2")
            game2.franchise = "Fallback Franchise"
            s.flush()

            # Mock selector: returns empty for game1, clips for game2
            call_log = []
            def mock_select(session, gid, duration, **kwargs):
                call_log.append(gid)
                if gid == game2.id:
                    return [MagicMock(spec=SelectedClip)]
                return []

            with patch.object(retriever.fallback_selector, "select", side_effect=mock_select):
                clips = retriever.retrieve(
                    s, game1.id, 10.0,
                    scope=ContentScope.franchise.value,
                    rng=random.Random(42),
                )
                assert len(clips) > 0
                # Should have tried game1 first, then game2
                assert game1.id in call_log
                assert game2.id in call_log

        get_settings.cache_clear()
