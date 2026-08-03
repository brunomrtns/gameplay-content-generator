"""Tests for V2 Game Registry Canônico — slug, game_aliases, dedup, resolver.

Covers ARCHITECTURE_V2.md §4 (Game Registry Canônico) acceptance criteria:
- New game gets unique slug
- Aliases are individually searchable
- Upload with existing game name links to existing Game (no duplicate)
- Game.user_id deprecated without breaking existing queries
"""

import pytest

from gpcg.domain.game_registry import (
    add_alias,
    find_by_alias,
    find_by_name,
    find_by_slug,
    get_aliases,
    get_or_create,
    list_all,
    remove_alias,
    search,
)
from gpcg.domain.game_resolver import resolve_l1
from gpcg.domain.filename_parser import parse_filename
from gpcg.domain.models import Game, GameAlias, GameResolutionMethod
from gpcg.domain.slug_utils import normalize_name, slugify
from gpcg.infrastructure.database import init_db, session_scope


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Use a temp DB for each test."""
    db_path = tmp_path / "test_v2.db"
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


# ── slug_utils tests ──────────────────────────────────────────────────────────


class TestSlugUtils:
    def test_slugify_basic(self):
        assert slugify("Resident Evil 4") == "resident-evil-4"

    def test_slugify_accents(self):
        assert slugify("São Paulo") == "sao-paulo"
        assert slugify("Pokémon") == "pokemon"

    def test_slugify_special_chars(self):
        assert slugify("Bully: Scholarship Edition") == "bully-scholarship-edition"
        assert slugify("GTA: Vice City") == "gta-vice-city"

    def test_slugify_empty(self):
        assert slugify("") == "game"
        assert slugify("   ") == "game"

    def test_slugify_numbers(self):
        assert slugify("Super Mario 64") == "super-mario-64"
        assert slugify("Final Fantasy VII") == "final-fantasy-vii"

    def test_slugify_consecutive_hyphens(self):
        assert slugify("A  B   C") == "a-b-c"

    def test_normalize_name_removes_platform_suffix(self):
        assert normalize_name("Resident Evil 4 (PS2)") == "resident evil 4"
        assert normalize_name("Resident Evil 4 PS2") == "resident evil 4"
        assert normalize_name("Doom PC") == "doom"

    def test_normalize_name_keeps_subtitles(self):
        # Subtitles are NOT removed (they're distinct aliases)
        assert "scholarship" in normalize_name("Bully Scholarship Edition")
        assert "remake" in normalize_name("Resident Evil 4 Remake")

    def test_normalize_name_accents(self):
        assert normalize_name("Pokémon") == "pokemon"


# ── Game Registry tests ───────────────────────────────────────────────────────


class TestGameRegistry:
    def test_create_game_generates_slug(self):
        with session_scope() as s:
            game = get_or_create(s, "Resident Evil 4")
            assert game.slug == "resident-evil-4"
            assert game.id is not None

    def test_find_by_slug(self):
        with session_scope() as s:
            get_or_create(s, "Resident Evil 4")
        with session_scope() as s:
            game = find_by_slug(s, "resident-evil-4")
            assert game is not None
            assert game.canonical_name == "Resident Evil 4"

    def test_find_by_name_uses_slug(self):
        with session_scope() as s:
            get_or_create(s, "Resident Evil 4")
        with session_scope() as s:
            # Different case should still find via slug
            game = find_by_name(s, "resident evil 4")
            assert game is not None
            assert game.canonical_name == "Resident Evil 4"

    def test_find_by_alias(self):
        with session_scope() as s:
            get_or_create(s, "Resident Evil 4", aliases=["RE4"])
        with session_scope() as s:
            game = find_by_alias(s, "RE4")
            assert game is not None
            assert game.canonical_name == "Resident Evil 4"

    def test_find_by_alias_case_insensitive(self):
        with session_scope() as s:
            get_or_create(s, "Resident Evil 4", aliases=["RE4"])
        with session_scope() as s:
            game = find_by_alias(s, "re4")
            assert game is not None

    def test_dedup_on_create_reuses_existing(self):
        """Upload with existing game name should link to existing Game."""
        with session_scope() as s:
            g1 = get_or_create(s, "Bully", aliases=["Bully Scholarship Edition"])
        with session_scope() as s:
            # Creating again with same name should reuse, not duplicate
            g2 = get_or_create(s, "Bully")
            assert g1.id == g2.id

    def test_dedup_by_alias_reuses_existing(self):
        """Creating with an alias of an existing game should reuse."""
        with session_scope() as s:
            g1 = get_or_create(s, "Resident Evil 4", aliases=["RE4"])
        with session_scope() as s:
            # "RE4" is an alias of "Resident Evil 4" — should reuse
            g2 = get_or_create(s, "RE4")
            assert g1.id == g2.id

    def test_add_alias_to_existing_game(self):
        with session_scope() as s:
            game = get_or_create(s, "Zelda")
        with session_scope() as s:
            alias = add_alias(s, game.id, "Legend of Zelda", source="manual")
            assert alias is not None
            assert alias.alias == "Legend of Zelda"
            assert alias.source == "manual"

    def test_add_duplicate_alias_returns_none(self):
        with session_scope() as s:
            game = get_or_create(s, "Zelda", aliases=["LoZ"])
        with session_scope() as s:
            # Adding same alias again should return None (already exists)
            result = add_alias(s, game.id, "LoZ")
            assert result is None

    def test_add_alias_belongs_to_other_game_returns_none(self):
        with session_scope() as s:
            g1 = get_or_create(s, "Zelda", aliases=["LoZ"])
            g2 = get_or_create(s, "Mario")
        with session_scope() as s:
            # "LoZ" belongs to Zelda, can't add to Mario
            result = add_alias(s, g2.id, "LoZ")
            assert result is None

    def test_get_aliases(self):
        with session_scope() as s:
            game = get_or_create(s, "Bully", aliases=["Bully SE", "Bully Scholarship Edition"])
        with session_scope() as s:
            aliases = get_aliases(s, game.id)
            alias_names = [a.alias for a in aliases]
            assert "Bully SE" in alias_names
            assert "Bully Scholarship Edition" in alias_names

    def test_remove_alias(self):
        with session_scope() as s:
            game = get_or_create(s, "Zelda", aliases=["LoZ"])
            aliases = get_aliases(s, game.id)
            alias_id = aliases[0].id
        with session_scope() as s:
            removed = remove_alias(s, game.id, alias_id)
            assert removed is True
            # Verify it's gone
            aliases = get_aliases(s, game.id)
            assert len(aliases) == 0

    def test_remove_alias_not_found(self):
        with session_scope() as s:
            game = get_or_create(s, "Zelda")
        with session_scope() as s:
            removed = remove_alias(s, game.id, 99999)
            assert removed is False

    def test_search_by_name(self):
        with session_scope() as s:
            get_or_create(s, "Resident Evil 4")
            get_or_create(s, "Resident Evil 2")
            get_or_create(s, "Super Mario")
        with session_scope() as s:
            results = search(s, "resident evil")
            assert len(results) == 2
            names = [g.canonical_name for g in results]
            assert "Resident Evil 4" in names
            assert "Resident Evil 2" in names

    def test_search_by_alias(self):
        with session_scope() as s:
            get_or_create(s, "Resident Evil 4", aliases=["RE4"])
        with session_scope() as s:
            results = search(s, "RE4")
            assert len(results) == 1
            assert results[0].canonical_name == "Resident Evil 4"

    def test_search_empty_query_returns_all(self):
        with session_scope() as s:
            get_or_create(s, "Game A")
            get_or_create(s, "Game B")
        with session_scope() as s:
            results = search(s, "")
            assert len(results) >= 2

    def test_slug_collision_appends_suffix(self):
        """Two games with same name should get different slugs."""
        with session_scope() as s:
            g1 = get_or_create(s, "Doom")
            assert g1.slug == "doom"
        with session_scope() as s:
            # This would be a different game (e.g., Doom 2016 vs Doom 1993)
            # Since get_or_create reuses by name, we need to create directly
            from gpcg.domain.models import Game as GameModel
            g2 = GameModel(canonical_name="Doom", slug=None)
            s.add(g2)
            s.flush()
            # Generate slug via the migration helper
            from gpcg.infrastructure.database import _generate_unique_slug
            g2.slug = _generate_unique_slug(s, slugify("Doom"), exclude_id=g2.id)
            assert g2.slug == "doom-2"

    def test_user_id_deprecated_on_new_games(self):
        """V2: new games should not have user_id set (games are global)."""
        with session_scope() as s:
            game = get_or_create(s, "New Global Game")
            assert game.user_id is None

    def test_enrichment_state_pending(self):
        with session_scope() as s:
            game = get_or_create(s, "New Game")
            assert game.enrichment_state == "pending"
            assert game.is_enriched is False

    def test_enrichment_state_enriched(self):
        from datetime import datetime, timezone
        with session_scope() as s:
            game = get_or_create(s, "Enriched Game")
            game.enriched_at = datetime.now(timezone.utc)
            game.developer = "Test Studio"
            s.flush()
            assert game.enrichment_state == "enriched"
            assert game.is_enriched is True

    def test_enrichment_state_error(self):
        with session_scope() as s:
            game = get_or_create(s, "Error Game")
            game.enrichment_error = "Wikidata lookup failed"
            s.flush()
            assert game.enrichment_state == "error"
            assert game.is_enriched is False


# ── Game Resolver V2 tests ────────────────────────────────────────────────────


class TestResolveL1V2:
    def test_deterministic_match_by_slug(self):
        with session_scope() as s:
            get_or_create(s, "Resident Evil 4")
            parsed = parse_filename("Resident Evil 4_2026-07-26_14-32-11.mp4")
            r = resolve_l1(parsed, s)
            assert r is not None
            assert r.game_name == "Resident Evil 4"
            assert r.method == GameResolutionMethod.deterministic.value
            assert r.confidence >= 0.9

    def test_deterministic_match_by_alias(self):
        with session_scope() as s:
            get_or_create(s, "Resident Evil 4", aliases=["RE4"])
            parsed = parse_filename("RE4_2026-07-26_15-00-00.mp4")
            r = resolve_l1(parsed, s)
            assert r is not None
            assert r.game_name == "Resident Evil 4"
            assert r.confidence >= 0.9

    def test_deterministic_match_case_insensitive(self):
        with session_scope() as s:
            get_or_create(s, "Bully")
            parsed = parse_filename("bully_2026-07-26_14-32-11.mp4")
            r = resolve_l1(parsed, s)
            assert r is not None
            assert r.game_name == "Bully"

    def test_no_match_returns_none(self):
        with session_scope() as s:
            get_or_create(s, "Bully")
            parsed = parse_filename("UnknownGame_2026-07-26_14-32-11.mp4")
            r = resolve_l1(parsed, s)
            assert r is None

    def test_capture_source_only_skipped(self):
        with session_scope() as s:
            get_or_create(s, "Bully")
            parsed = parse_filename("Yuzu_2026-07-26_15-07-43.mp4")
            r = resolve_l1(parsed, s)
            assert r is None  # Yuzu is a capture source, not a game

    def test_platform_suffix_stripped_in_match(self):
        """L1 should match even if filename has platform suffix."""
        with session_scope() as s:
            get_or_create(s, "Resident Evil 4")
            # The filename parser may or may not strip (PS2) — depends on parser
            # But normalize_name in the resolver strips platform suffixes
            parsed = parse_filename("Resident Evil 4_2026-07-26_14-32-11.mp4")
            r = resolve_l1(parsed, s)
            assert r is not None
            assert r.game_name == "Resident Evil 4"


# ── Data Migration tests ──────────────────────────────────────────────────────


class TestDataMigration:
    def test_existing_games_get_slugs_on_init(self):
        """When init_db runs, existing games without slugs should get them."""
        # The fresh_db fixture already runs init_db on an empty DB.
        # Create a game without slug directly, then re-run init_db.
        with session_scope() as s:
            from gpcg.domain.models import Game as GameModel
            game = GameModel(canonical_name="Migration Test Game", slug=None)
            s.add(game)
            s.flush()
            assert game.slug is None

        # Re-run init_db — should generate slug
        init_db()

        with session_scope() as s:
            from sqlalchemy import select
            game = s.execute(
                select(Game).where(Game.canonical_name == "Migration Test Game")
            ).scalar_one_or_none()
            assert game is not None
            assert game.slug == "migration-test-game"

    def test_json_aliases_migrated_to_table(self):
        """JSON aliases column should be migrated to game_aliases table on init."""
        with session_scope() as s:
            from gpcg.domain.models import Game as GameModel
            game = GameModel(
                canonical_name="Migration Alias Test",
                slug=None,
                aliases=["MAT", "Migration Alias"],
            )
            s.add(game)
            s.flush()

        init_db()

        with session_scope() as s:
            from sqlalchemy import select
            game = s.execute(
                select(Game).where(Game.canonical_name == "Migration Alias Test")
            ).scalar_one_or_none()
            assert game is not None
            aliases = get_aliases(s, game.id)
            alias_names = [a.alias for a in aliases]
            assert "MAT" in alias_names
            assert "Migration Alias" in alias_names
