"""Tests for game resolution layers and game repository."""

from pathlib import Path

import pytest

from gpcg.domain.game_registry import get_aliases
from gpcg.domain.game_repository import find_by_name, get_or_create, list_all
from gpcg.domain.game_resolver import (
    ResolutionResult,
    resolve_l1,
    resolve_l2,
)
from gpcg.domain.filename_parser import parse_filename
from gpcg.domain.models import Game, GameAlias, GameplaySource, GameResolutionMethod
from gpcg.infrastructure.database import init_db, session_scope


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Use a temp DB for each test."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("GPCG_DB_PATH", str(db_path))
    monkeypatch.setenv("GPCG_DATA_DIR", str(tmp_path))
    # Reset cached settings + engine
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


class TestGameRepository:
    def test_create_and_find(self):
        with session_scope() as s:
            g = get_or_create(s, "Bully", aliases=["Bully Scholarship Edition"])
            assert g.id is not None
        with session_scope() as s:
            found = find_by_name(s, "Bully")
            assert found is not None
            assert found.canonical_name == "Bully"

    def test_find_by_alias(self):
        with session_scope() as s:
            get_or_create(s, "Bully", aliases=["Bully Scholarship Edition"])
        with session_scope() as s:
            found = find_by_name(s, "Bully Scholarship Edition")
            assert found is not None
            assert found.canonical_name == "Bully"

    def test_get_or_create_idempotent(self):
        with session_scope() as s:
            g1 = get_or_create(s, "Crash CTR")
        with session_scope() as s:
            g2 = get_or_create(s, "Crash CTR")
        assert g1.id == g2.id

    def test_merge_aliases(self):
        with session_scope() as s:
            get_or_create(s, "Bully", aliases=["Scholarship Edition"])
        with session_scope() as s:
            g = get_or_create(s, "Bully", aliases=["Rockstar Games"])
            # V2: aliases are stored in game_aliases table, not JSON column
            alias_names = [a.alias for a in get_aliases(s, g.id)]
            assert "Scholarship Edition" in alias_names
            assert "Rockstar Games" in alias_names


class TestResolveL1:
    def test_deterministic_match(self):
        with session_scope() as s:
            get_or_create(s, "Bully", aliases=["Bully Scholarship Edition"])
            parsed = parse_filename("Bully_2026-07-26_14-32-11.mp4")
            r = resolve_l1(parsed, s)
            assert r is not None
            assert r.game_name == "Bully"
            assert r.method == GameResolutionMethod.deterministic.value
            assert r.confidence >= 0.9

    def test_no_match_returns_none(self):
        with session_scope() as s:
            parsed = parse_filename("UnknownGame_2026-07-26_14-32-11.mp4")
            r = resolve_l1(parsed, s)
            assert r is None

    def test_capture_source_only_skipped(self):
        with session_scope() as s:
            get_or_create(s, "Bully")
            parsed = parse_filename("Yuzu_2026-07-26_15-07-43.mp4")
            r = resolve_l1(parsed, s)
            assert r is None  # Yuzu is a capture source, not a game


class TestResolveL2:
    def test_prior_single_game(self):
        with session_scope() as s:
            game = get_or_create(s, "Crash Team Racing")
            # Create a previously-resolved source with Yuzu capture source
            src = GameplaySource(
                game_id=game.id,
                file_path="/tmp/x.mp4",
                filename="Yuzu_2026-01-01_00-00-00.mp4",
                file_hash="abc123",
                capture_source="Yuzu",
                duration=60.0,
                resolution_method=GameResolutionMethod.manual.value,
            )
            s.add(src)
            s.flush()
            parsed = parse_filename("Yuzu_2026-07-26_15-07-43.mp4")
            r = resolve_l2(parsed, s)
            assert r is not None
            assert r.game_name == "Crash Team Racing"
            assert r.method == GameResolutionMethod.prior.value
            assert r.confidence == 0.5

    def test_prior_multiple_games_no_consensus(self):
        with session_scope() as s:
            g1 = get_or_create(s, "Crash Team Racing")
            g2 = get_or_create(s, "Zelda")
            for gid in [g1.id, g2.id]:
                src = GameplaySource(
                    game_id=gid,
                    file_path=f"/tmp/{gid}.mp4",
                    filename=f"Yuzu_2026-01-01_00-00-0{gid}.mp4",
                    file_hash=f"hash{gid}",
                    capture_source="Yuzu",
                    duration=60.0,
                    resolution_method=GameResolutionMethod.manual.value,
                )
                s.add(src)
            s.flush()
            parsed = parse_filename("Yuzu_2026-07-26_15-07-43.mp4")
            r = resolve_l2(parsed, s)
            # Multiple games → no consensus → None
            assert r is None
