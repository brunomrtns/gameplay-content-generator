"""Tests for gameplay asset service and selector."""

import random

import pytest

from gpcg.application.gameplay_asset_service import AssetCreate, GameplayAssetService
from gpcg.application.gameplay_selector import GameplaySelector
from gpcg.domain.game_repository import get_or_create
from gpcg.domains.games.models import GameplaySource
from gpcg.infrastructure.database import init_db, session_scope


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


def _make_source(session, game_id, duration=120.0):
    src = GameplaySource(
        game_id=game_id,
        file_path=f"/tmp/test_{game_id}.mp4",
        filename=f"test_{game_id}.mp4",
        file_hash=f"hash_{game_id}_{id(session)}",
        duration=duration,
        width=1920,
        height=1080,
        ingestion_status="ready",
    )
    session.add(src)
    session.flush()
    return src


class TestGameplayAssetService:
    def test_create_asset(self):
        with session_scope() as s:
            game = get_or_create(s, "Bully")
            src = _make_source(s, game.id, duration=120.0)
            asset = GameplayAssetService.create(s, AssetCreate(source_id=src.id, start_sec=10, end_sec=30, label="cool moment"))
            assert asset.duration == 20.0
            assert asset.start_sec == 10

    def test_create_invalid_range(self):
        with session_scope() as s:
            game = get_or_create(s, "Bully")
            src = _make_source(s, game.id, duration=120.0)
            with pytest.raises(ValueError):
                GameplayAssetService.create(s, AssetCreate(source_id=src.id, start_sec=50, end_sec=10))
            with pytest.raises(ValueError):
                GameplayAssetService.create(s, AssetCreate(source_id=src.id, start_sec=-5, end_sec=10))

    def test_create_exceeds_duration(self):
        with session_scope() as s:
            game = get_or_create(s, "Bully")
            src = _make_source(s, game.id, duration=60.0)
            with pytest.raises(ValueError):
                GameplayAssetService.create(s, AssetCreate(source_id=src.id, start_sec=0, end_sec=120))

    def test_list_for_game(self):
        with session_scope() as s:
            game = get_or_create(s, "Bully")
            src = _make_source(s, game.id)
            GameplayAssetService.create(s, AssetCreate(source_id=src.id, start_sec=0, end_sec=10))
            GameplayAssetService.create(s, AssetCreate(source_id=src.id, start_sec=20, end_sec=30))
            assets = GameplayAssetService.list_for_game(s, game.id)
            assert len(assets) == 2

    def test_delete(self):
        with session_scope() as s:
            game = get_or_create(s, "Bully")
            src = _make_source(s, game.id)
            asset = GameplayAssetService.create(s, AssetCreate(source_id=src.id, start_sec=0, end_sec=10))
            aid = asset.id
        with session_scope() as s:
            assert GameplayAssetService.delete(s, aid) is True
            assert GameplayAssetService.get(s, aid) is None


class TestGameplaySelector:
    def test_select_covers_duration(self):
        with session_scope() as s:
            game = get_or_create(s, "Bully")
            src = _make_source(s, game.id, duration=300.0)
            for i in range(5):
                GameplayAssetService.create(s, AssetCreate(source_id=src.id, start_sec=i*20, end_sec=i*20+15))
            selector = GameplaySelector()
            clips = selector.select(s, game.id, target_duration=60.0, rng=random.Random(42))
            total = sum(c.duration for c in clips)
            assert total >= 60.0
            assert len(clips) >= 4  # 15s each → need at least 4

    def test_select_empty_returns_empty(self):
        with session_scope() as s:
            game = get_or_create(s, "Bully")
            selector = GameplaySelector()
            clips = selector.select(s, game.id, target_duration=60.0)
            assert clips == []

    def test_select_prefers_unused(self):
        with session_scope() as s:
            game = get_or_create(s, "Bully")
            src = _make_source(s, game.id, duration=300.0)
            # Create one heavily-used and one fresh asset
            a1 = GameplayAssetService.create(s, AssetCreate(source_id=src.id, start_sec=0, end_sec=30, label="used"))
            a1.used_count = 10
            a2 = GameplayAssetService.create(s, AssetCreate(source_id=src.id, start_sec=30, end_sec=60, label="fresh"))
            s.flush()
            selector = GameplaySelector()
            clips = selector.select(s, game.id, target_duration=30.0, rng=random.Random(42))
            # The fresh one (used_count=0) should be preferred
            assert any(c.asset.id == a2.id for c in clips)
