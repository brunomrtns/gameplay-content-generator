"""Tests for the Game Catalog Service (IGDB client, sync, query).

Uses a temp catalog DB for each test class. IGDB HTTP calls are mocked
to avoid hitting the real API.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from gpcg.catalog.database import (
    init_catalog_db,
    reset_engine,
    session_scope,
)
from gpcg.catalog.igdb_client import IGDBClient, IGDBGame
from gpcg.catalog.models import CatalogAlias, CatalogGame, SyncState
from gpcg.catalog.query_service import QueryService
from gpcg.catalog.sync_service import SyncService
from gpcg.config import get_settings


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_catalog_db(tmp_path, monkeypatch):
    """Use a temp catalog DB for each test."""
    db_path = tmp_path / "catalog.db"
    monkeypatch.setenv("CATALOG_DB_PATH", str(db_path))
    monkeypatch.setenv("GPCG_DATA_DIR", str(tmp_path))
    # Also set IGDB credentials so IGDBClient can be instantiated
    monkeypatch.setenv("IGDB_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("IGDB_CLIENT_SECRET", "test_client_secret")

    # Reset cached settings + engine
    get_settings.cache_clear()
    reset_engine()
    init_catalog_db()
    yield
    get_settings.cache_clear()
    reset_engine()


def _make_igdb_game(
    id: int = 1,
    name: str = "Grand Theft Auto IV",
    slug: str = "grand-theft-auto-iv",
    **kwargs: Any,
) -> IGDBGame:
    """Helper to create an IGDBGame with sensible defaults."""
    defaults = dict(
        id=id,
        name=name,
        slug=slug,
        summary="An open world action-adventure game.",
        first_release_date=1209600000,  # 2008-05-01
        rating=85.0,
        rating_count=500,
        total_rating=85.0,
        total_rating_count=500,
        hypes=None,
        category=0,
        cover_image_id="abc123",
        screenshot_image_ids=["ss1", "ss2"],
        genres=["Action", "Adventure"],
        themes=["Crime"],
        game_modes=["Single player", "Multiplayer"],
        player_perspectives=["Third person"],
        platforms=["PC", "PlayStation 3", "Xbox 360"],
        franchise="Grand Theft Auto",
        developer="Rockstar North",
        publisher="Rockstar Games",
        alternative_names=["GTA IV", "GTA 4", "GTAIV"],
        igdb_url="https://www.igdb.com/games/grand-theft-auto-iv",
        updated_at=1700000000,
    )
    defaults.update(kwargs)
    return IGDBGame(**defaults)


# ── IGDB Client Tests ─────────────────────────────────────────────────────────


class TestIGDBClient:
    def test_requires_credentials(self, monkeypatch):
        """IGDBClient should raise if credentials are missing."""
        get_settings.cache_clear()
        monkeypatch.setenv("IGDB_CLIENT_ID", "")
        monkeypatch.setenv("IGDB_CLIENT_SECRET", "")
        get_settings.cache_clear()
        with pytest.raises(ValueError, match="IGDB credentials not configured"):
            IGDBClient()

    def test_parse_game_basic(self):
        """Test that _parse_game correctly extracts fields from raw IGDB data."""
        client = IGDBClient()
        raw = {
            "id": 123,
            "name": "Test Game",
            "slug": "test-game",
            "summary": "A test game.",
            "first_release_date": 1500000000,
            "rating": 90.0,
            "rating_count": 100,
            "total_rating": 90.0,
            "total_rating_count": 100,
            "category": 0,
            "cover": {"image_id": "cover123"},
            "screenshots": [{"image_id": "ss1"}, {"image_id": "ss2"}],
            "genres": [{"name": "Action"}, {"name": "RPG"}],
            "themes": [{"name": "Fantasy"}],
            "game_modes": [{"name": "Single player"}],
            "player_perspectives": [{"name": "Third person"}],
            "platforms": [{"name": "PC"}, {"name": "PlayStation 5"}],
            "franchise": {"name": "Test Franchise"},
            "involved_companies": [
                {"company": {"name": "Test Studio"}, "developer": True, "publisher": False},
                {"company": {"name": "Test Publisher"}, "developer": False, "publisher": True},
            ],
            "alternative_names": [{"name": "TG", "comment": "abbreviation"}],
            "url": "https://www.igdb.com/games/test-game",
            "updated_at": 1700000000,
        }

        game = client._parse_game(raw)
        assert game.id == 123
        assert game.name == "Test Game"
        assert game.slug == "test-game"
        assert game.cover_image_id == "cover123"
        assert game.screenshot_image_ids == ["ss1", "ss2"]
        assert game.genres == ["Action", "RPG"]
        assert game.themes == ["Fantasy"]
        assert game.platforms == ["PC", "PlayStation 5"]
        assert game.franchise == "Test Franchise"
        assert game.developer == "Test Studio"
        assert game.publisher == "Test Publisher"
        assert game.alternative_names == ["TG"]
        assert game.cover_url == "https://images.igdb.com/igdb/image/upload/t_cover_big/cover123.jpg"
        assert len(game.screenshot_urls) == 2

    def test_parse_game_minimal(self):
        """Test parsing a game with minimal fields (no cover, no genres, etc)."""
        client = IGDBClient()
        raw = {
            "id": 456,
            "name": "Minimal Game",
            "slug": "minimal-game",
            "category": 0,
        }

        game = client._parse_game(raw)
        assert game.id == 456
        assert game.name == "Minimal Game"
        assert game.cover_image_id is None
        assert game.cover_url is None
        assert game.screenshot_image_ids == []
        assert game.genres == []
        assert game.developer is None
        assert game.publisher is None

    def test_rate_limiting(self):
        """Test that rate limiting enforces minimum sleep between requests."""
        client = IGDBClient()
        client._rate_limit_sec = 0.05  # 50ms for test speed

        # First call should be instant (no previous request)
        start = time.time()
        client._rate_limit()
        elapsed1 = time.time() - start
        assert elapsed1 < 0.01

        # Second call should sleep ~50ms
        start = time.time()
        client._rate_limit()
        elapsed2 = time.time() - start
        assert elapsed2 >= 0.04  # Allow small timing variance


# ── Sync Service Tests ────────────────────────────────────────────────────────


class TestSyncService:
    def test_upsert_creates_new_game(self):
        """Test that upserting a new IGDB game creates a CatalogGame."""
        service = SyncService(client=MagicMock())
        game = _make_igdb_game(id=1, name="Test Game", slug="test-game")

        stats = service._upsert_batch([game])
        assert stats["created"] == 1
        assert stats["updated"] == 0
        assert stats["aliases_added"] == 3  # GTA IV, GTA 4, GTAIV

        with session_scope() as s:
            catalog_game = s.get(CatalogGame, 1)
            assert catalog_game is not None
            assert catalog_game.name == "Test Game"
            assert catalog_game.slug == "test-game"
            assert catalog_game.developer == "Rockstar North"
            assert catalog_game.genres == ["Action", "Adventure"]

    def test_upsert_updates_existing_game(self):
        """Test that upserting an existing game updates its fields."""
        service = SyncService(client=MagicMock())

        # First insert
        game1 = _make_igdb_game(id=1, name="Test Game", slug="test-game", rating=80.0)
        service._upsert_batch([game1])

        # Now update with new data
        game2 = _make_igdb_game(id=1, name="Test Game Updated", slug="test-game", rating=95.0)
        stats = service._upsert_batch([game2])
        assert stats["created"] == 0
        assert stats["updated"] == 1

        with session_scope() as s:
            catalog_game = s.get(CatalogGame, 1)
            assert catalog_game.name == "Test Game Updated"
            assert catalog_game.rating == 95.0

    def test_upsert_aliases_are_additive(self):
        """Test that aliases are additive — re-syncing doesn't duplicate."""
        service = SyncService(client=MagicMock())
        game = _make_igdb_game(id=1, alternative_names=["GTA IV", "GTA 4"])

        # First sync
        stats1 = service._upsert_batch([game])
        assert stats1["aliases_added"] == 2

        # Second sync with same aliases
        stats2 = service._upsert_batch([game])
        assert stats2["aliases_added"] == 0  # Already exist

        # Third sync with a new alias added
        game.alternative_names.append("GTAIV")
        stats3 = service._upsert_batch([game])
        assert stats3["aliases_added"] == 1  # Only the new one

    def test_sync_lock_prevents_concurrent(self):
        """Test that the sync lock prevents concurrent syncs."""
        service = SyncService(client=MagicMock())

        # Acquire lock
        assert service._acquire_sync_lock() is True
        # Second acquire should fail
        assert service._acquire_sync_lock() is False
        # Release
        service._release_sync_lock()
        # Now should work
        assert service._acquire_sync_lock() is True
        service._release_sync_lock()

    def test_full_sync_with_mocked_client(self):
        """Test a full sync with a mocked IGDB client."""
        mock_client = MagicMock(spec=IGDBClient)
        mock_client.fetch_game_count.return_value = 2
        mock_client.fetch_games.return_value = [
            _make_igdb_game(id=1, name="Game A", slug="game-a", updated_at=1700000001),
            _make_igdb_game(id=2, name="Game B", slug="game-b", updated_at=1700000002),
        ]

        service = SyncService(client=mock_client)
        stats = service.run_full_sync()

        assert stats["fetched"] == 2
        assert stats["created"] == 2
        assert stats["type"] == "full"
        assert "error" not in stats

        # Verify sync state was updated
        with session_scope() as s:
            state = s.get(SyncState, 1)
            assert state is not None
            assert state.last_full_sync is not None
            assert state.last_igdb_updated_at == 1700000002
            assert state.total_games == 2
            assert state.sync_in_progress is False

    def test_incremental_sync_uses_updated_after(self):
        """Test that incremental sync passes the correct updated_after value."""
        # First, do a full sync to set last_igdb_updated_at
        mock_client = MagicMock(spec=IGDBClient)
        mock_client.fetch_game_count.return_value = 1
        mock_client.fetch_games.return_value = [
            _make_igdb_game(id=1, name="Game A", slug="game-a", updated_at=1700000001),
        ]
        service = SyncService(client=mock_client)
        service.run_full_sync()

        # Now do an incremental sync
        mock_client.fetch_game_count.return_value = 1
        mock_client.fetch_games.return_value = [
            _make_igdb_game(id=2, name="Game B", slug="game-b", updated_at=1700000005),
        ]
        stats = service.run_incremental_sync()

        assert stats["fetched"] == 1
        assert stats["created"] == 1
        assert stats["type"] == "incremental"

        # Verify fetch_games was called with updated_after=1700000001
        # (the max updated_at from the full sync)
        call_args = mock_client.fetch_games.call_args
        assert call_args.kwargs["updated_after"] == 1700000001

    def test_incremental_sync_falls_back_to_full(self):
        """Test that incremental sync falls back to full if no previous sync."""
        mock_client = MagicMock(spec=IGDBClient)
        mock_client.fetch_game_count.return_value = 0
        mock_client.fetch_games.return_value = []

        service = SyncService(client=mock_client)
        stats = service.run_incremental_sync()

        # Should have run a full sync instead
        assert stats["type"] == "full"

    def test_get_status_empty(self):
        """Test get_status when no sync has been done."""
        service = SyncService(client=MagicMock())
        status = service.get_status()
        assert status["initialized"] is False
        assert status["sync_in_progress"] is False
        assert status["total_games"] == 0


# ── Query Service Tests ───────────────────────────────────────────────────────


class TestQueryService:
    def _seed_games(self):
        """Seed the catalog DB with test games."""
        games = [
            CatalogGame(
                id=1,
                name="Grand Theft Auto IV",
                slug="grand-theft-auto-iv",
                summary="Open world action game",
                first_release_date=1209600000,
                rating=85.0,
                total_rating=85.0,
                total_rating_count=500,
                genres=["Action", "Adventure"],
                cover_url="https://images.igdb.com/igdb/image/upload/t_cover_big/abc.jpg",
            ),
            CatalogGame(
                id=2,
                name="Grand Theft Auto V",
                slug="grand-theft-auto-v",
                summary="Open world action game",
                first_release_date=1380000000,
                rating=95.0,
                total_rating=95.0,
                total_rating_count=2000,
                genres=["Action", "Adventure"],
            ),
            CatalogGame(
                id=3,
                name="Bully",
                slug="bully",
                summary="School simulation",
                first_release_date=1167609600,
                rating=80.0,
                total_rating=80.0,
                total_rating_count=200,
                genres=["Action", "Adventure"],
            ),
        ]
        with session_scope() as s:
            for g in games:
                s.add(g)
            s.flush()
            # Add aliases
            s.add(CatalogAlias(game_id=1, alias="GTA IV", source="igdb"))
            s.add(CatalogAlias(game_id=1, alias="GTA 4", source="igdb"))
            s.add(CatalogAlias(game_id=2, alias="GTA V", source="igdb"))
            s.add(CatalogAlias(game_id=2, alias="GTA 5", source="igdb"))
            s.add(CatalogAlias(game_id=3, alias="Bully: Scholarship Edition", source="igdb"))

    def test_search_exact_slug(self):
        """Search by exact slug should return the game with highest priority."""
        self._seed_games()
        service = QueryService()
        results = service.search("grand-theft-auto-iv")
        assert len(results) >= 1
        assert results[0].slug == "grand-theft-auto-iv"

    def test_search_by_name_prefix(self):
        """Search by name prefix should return matching games."""
        self._seed_games()
        service = QueryService()
        results = service.search("Grand Theft Auto")
        names = [r.name for r in results]
        assert "Grand Theft Auto IV" in names
        assert "Grand Theft Auto V" in names

    def test_search_by_alias(self):
        """Search by alias should return the corresponding game."""
        self._seed_games()
        service = QueryService()
        results = service.search("GTA IV")
        assert len(results) >= 1
        assert results[0].name == "Grand Theft Auto IV"

    def test_search_empty_query(self):
        """Empty search query should return empty list."""
        service = QueryService()
        results = service.search("")
        assert results == []

    def test_search_no_results(self):
        """Search with no matches should return empty list."""
        self._seed_games()
        service = QueryService()
        results = service.search("Nonexistent Game 12345")
        assert results == []

    def test_get_game(self):
        """Get a game by ID with full details."""
        self._seed_games()
        service = QueryService()
        game = service.get_game(1)
        assert game is not None
        assert game.name == "Grand Theft Auto IV"
        assert game.developer is None  # Not set in seed
        assert "GTA IV" in game.aliases
        assert "GTA 4" in game.aliases

    def test_get_game_not_found(self):
        """Get a non-existent game should return None."""
        service = QueryService()
        game = service.get_game(99999)
        assert game is None

    def test_get_by_slug(self):
        """Get a game by slug."""
        self._seed_games()
        service = QueryService()
        game = service.get_by_slug("bully")
        assert game is not None
        assert game.name == "Bully"

    def test_autocomplete(self):
        """Autocomplete should return games with names starting with the query."""
        self._seed_games()
        service = QueryService()
        results = service.autocomplete("Grand")
        assert len(results) == 2
        # GTA V should come first (higher total_rating_count)
        assert results[0].name == "Grand Theft Auto V"
        assert results[1].name == "Grand Theft Auto IV"

    def test_autocomplete_short_query(self):
        """Autocomplete with < 2 chars should return empty list."""
        service = QueryService()
        results = service.autocomplete("G")
        assert results == []

    def test_popular(self):
        """Popular should return games sorted by total_rating_count."""
        self._seed_games()
        service = QueryService()
        results = service.popular(limit=10)
        assert len(results) == 3
        assert results[0].name == "Grand Theft Auto V"  # 2000 count
        assert results[1].name == "Grand Theft Auto IV"  # 500 count
        assert results[2].name == "Bully"  # 200 count

    def test_recent(self):
        """Recent should return games sorted by release date (newest first)."""
        self._seed_games()
        service = QueryService()
        results = service.recent(limit=10)
        assert len(results) == 3
        assert results[0].name == "Grand Theft Auto V"  # 2013
        assert results[1].name == "Grand Theft Auto IV"  # 2008
        assert results[2].name == "Bully"  # 2007

    def test_stats(self):
        """Stats should return correct counts."""
        self._seed_games()
        service = QueryService()
        stats = service.stats()
        assert stats["total_games"] == 3
        assert stats["total_aliases"] == 5
        assert stats["games_with_cover"] == 1


# ── FastAPI App Tests ─────────────────────────────────────────────────────────


class TestCatalogAPI:
    """Test the catalog FastAPI endpoints using TestClient."""

    @pytest.fixture
    def client(self, fresh_catalog_db):
        """Create a FastAPI TestClient with the catalog app."""
        from fastapi.testclient import TestClient

        # Patch the sync thread to not start (we don't want background syncs in tests)
        with patch("gpcg.catalog.app._start_sync_thread"):
            from gpcg.catalog.app import create_catalog_app

            app = create_catalog_app()
            with TestClient(app) as client:
                yield client

    def test_health(self, client):
        """Health endpoint should return ok."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "gpcg-catalog"

    def test_api_health(self, client):
        """API health endpoint should return ok."""
        resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_search_empty(self, client):
        """Search with results should return a list."""
        resp = client.get("/api/search", params={"q": "test"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["results"] == []

    def test_search_with_games(self, client):
        """Search after seeding games should return results."""
        # Seed a game directly
        with session_scope() as s:
            s.add(CatalogGame(
                id=1,
                name="Test Game",
                slug="test-game",
                genres=["Action"],
                total_rating_count=100,
            ))

        resp = client.get("/api/search", params={"q": "Test"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["results"][0]["name"] == "Test Game"

    def test_get_game_not_found(self, client):
        """Get non-existent game should return 404."""
        resp = client.get("/api/games/99999")
        assert resp.status_code == 404

    def test_get_game_found(self, client):
        """Get existing game should return details."""
        with session_scope() as s:
            s.add(CatalogGame(
                id=1,
                name="Test Game",
                slug="test-game",
                summary="A test game",
                genres=["Action"],
            ))
            s.add(CatalogAlias(game_id=1, alias="TG"))

        resp = client.get("/api/games/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test Game"
        assert data["summary"] == "A test game"
        assert "TG" in data["aliases"]

    def test_autocomplete(self, client):
        """Autocomplete endpoint should return matching games."""
        with session_scope() as s:
            s.add(CatalogGame(
                id=1,
                name="Halo Infinite",
                slug="halo-infinite",
                total_rating_count=500,
            ))

        resp = client.get("/api/autocomplete", params={"q": "Halo"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["results"][0]["name"] == "Halo Infinite"

    def test_admin_stats(self, client):
        """Admin stats endpoint should return catalog statistics."""
        resp = client.get("/admin/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_games" in data
        assert "sync" in data

    def test_admin_sync_status(self, client):
        """Admin sync status endpoint should return sync state."""
        resp = client.get("/admin/sync/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "sync_in_progress" in data
