"""Tests for V2 Game Knowledge Enrichment — enrich_game + clients.

Tests the enrichment pipeline with mocked HTTP clients (no real network calls)
and a mocked LLM for lore generation.

Covers ARCHITECTURE_V2.md §6.5 acceptance criteria:
- Enrichment fills description, developer, publisher, franchise, genres
- LLM failure is non-fatal (lore_summary NULL, enrichment still succeeds)
- Fetch failure sets enrichment_error
- Re-enrichment with force=True overwrites previous data
- Cache is stored in metadata_json.enrichment_cache
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from gpcg.application.game_enrichment import enrich_game
from gpcg.domain.game_registry import get_or_create
from gpcg.core.models import Job, JobType, JobStatus
from gpcg.domains.games.models import Game, GameAlias
from gpcg.infrastructure.database import init_db, session_scope
from gpcg.infrastructure.wikidata_client import WikidataGameInfo
from gpcg.infrastructure.wikipedia_client import WikipediaArticle


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Use a temp DB for each test."""
    db_path = tmp_path / "test_enrich.db"
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


def _make_wikidata_info(
    *,
    qid="Q123456",
    label="Resident Evil 4",
    developer="Capcom",
    publisher="Capcom",
    franchise="Resident Evil",
    genres=["survival horror", "action"],
    release_date=datetime(2005, 1, 11),
    aliases=["RE4", "Biohazard 4"],
    steam_app_id="254700",
) -> WikidataGameInfo:
    """Create a mock WikidataGameInfo for testing."""
    return WikidataGameInfo(
        qid=qid,
        label=label,
        description="video game",
        developer=developer,
        publisher=publisher,
        franchise=franchise,
        genres=genres,
        release_date=release_date,
        aliases=aliases,
        steam_app_id=steam_app_id,
    )


def _make_wikipedia_article(
    *,
    title="Resident Evil 4",
    description="2005 survival horror video game developed by Capcom",
    extract="Resident Evil 4 is a survival horror game developed by Capcom...",
    url="https://en.wikipedia.org/wiki/Resident_Evil_4",
) -> WikipediaArticle:
    """Create a mock WikipediaArticle for testing."""
    return WikipediaArticle(
        title=title,
        description=description,
        extract=extract,
        url=url,
    )


class TestEnrichGame:
    """Tests for the enrich_game function."""

    @patch("gpcg.application.game_enrichment.WikidataClient")
    @patch("gpcg.application.game_enrichment.WikipediaClient")
    def test_successful_enrichment(self, mock_wiki_cls, mock_wd_cls):
        """Enrichment fills all fields when Wikidata + Wikipedia + LLM succeed."""
        mock_wd = MagicMock()
        mock_wd.resolve_game.return_value = _make_wikidata_info()
        mock_wd_cls.return_value = mock_wd

        mock_wiki = MagicMock()
        mock_wiki.get_article_for_game.return_value = _make_wikipedia_article()
        mock_wiki_cls.return_value = mock_wiki

        mock_llm = MagicMock()
        mock_llm.generate.return_value = "Resident Evil 4 é um jogo de terror e sobrevivência..."

        with session_scope() as s:
            game = get_or_create(s, "Resident Evil 4")
            game_id = game.id

        with session_scope() as s:
            result = enrich_game(s, game_id, force=True, llm=mock_llm)

        assert result is True
        with session_scope() as s:
            game = s.get(Game, game_id)
            assert game.is_enriched
            assert game.enrichment_error is None
            assert game.developer == "Capcom"
            assert game.publisher == "Capcom"
            assert game.franchise == "Resident Evil"
            assert "survival horror" in game.genres
            assert game.description is not None
            assert game.lore_summary is not None
            assert game.external_ids.get("wikidata") == "Q123456"
            assert game.external_ids.get("steam") == "254700"

    @patch("gpcg.application.game_enrichment.WikidataClient")
    @patch("gpcg.application.game_enrichment.WikipediaClient")
    def test_wikidata_aliases_added_to_game_aliases(self, mock_wiki_cls, mock_wd_cls):
        """Wikidata aliases should be added to the game_aliases table."""
        mock_wd = MagicMock()
        mock_wd.resolve_game.return_value = _make_wikidata_info(aliases=["RE4", "Biohazard 4"])
        mock_wd_cls.return_value = mock_wd

        mock_wiki = MagicMock()
        mock_wiki.get_article_for_game.return_value = _make_wikipedia_article()
        mock_wiki_cls.return_value = mock_wiki

        mock_llm = MagicMock()
        mock_llm.generate.return_value = "Lore summary that is long enough to pass the length check."

        with session_scope() as s:
            game = get_or_create(s, "Resident Evil 4")
            game_id = game.id

        with session_scope() as s:
            enrich_game(s, game_id, force=True, llm=mock_llm)

        with session_scope() as s:
            from gpcg.domain.game_registry import get_aliases
            aliases = get_aliases(s, game_id)
            alias_names = [a.alias for a in aliases]
            assert "RE4" in alias_names
            assert "Biohazard 4" in alias_names
            # Check provenance
            for a in aliases:
                if a.alias in ("RE4", "Biohazard 4"):
                    assert a.source == "wikidata"

    @patch("gpcg.application.game_enrichment.WikidataClient")
    @patch("gpcg.application.game_enrichment.WikipediaClient")
    def test_llm_failure_is_non_fatal(self, mock_wiki_cls, mock_wd_cls):
        """LLM failure should not prevent enrichment — lore_summary stays NULL."""
        mock_wd = MagicMock()
        mock_wd.resolve_game.return_value = _make_wikidata_info()
        mock_wd_cls.return_value = mock_wd

        mock_wiki = MagicMock()
        mock_wiki.get_article_for_game.return_value = _make_wikipedia_article()
        mock_wiki_cls.return_value = mock_wiki

        mock_llm = MagicMock()
        mock_llm.generate.side_effect = Exception("LLM unavailable")

        with session_scope() as s:
            game = get_or_create(s, "Resident Evil 4")
            game_id = game.id

        with session_scope() as s:
            result = enrich_game(s, game_id, force=True, llm=mock_llm)

        # Should succeed despite LLM failure
        assert result is True
        with session_scope() as s:
            game = s.get(Game, game_id)
            assert game.is_enriched
            assert game.enrichment_error is None
            assert game.lore_summary is None  # LLM failed, lore is NULL
            # But other fields are populated
            assert game.developer == "Capcom"
            assert game.description is not None

    @patch("gpcg.application.game_enrichment.WikidataClient")
    @patch("gpcg.application.game_enrichment.WikipediaClient")
    def test_wikidata_failure_sets_error(self, mock_wiki_cls, mock_wd_cls):
        """Wikidata fetch failure should set enrichment_error."""
        mock_wd = MagicMock()
        mock_wd.resolve_game.return_value = None  # Wikidata failed
        mock_wd_cls.return_value = mock_wd

        mock_wiki = MagicMock()
        mock_wiki_cls.return_value = mock_wiki

        with session_scope() as s:
            game = get_or_create(s, "Unknown Game XYZ")
            game_id = game.id

        with session_scope() as s:
            result = enrich_game(s, game_id, force=True)

        assert result is False
        with session_scope() as s:
            game = s.get(Game, game_id)
            assert game.enrichment_state == "error"
            assert game.enrichment_error is not None
            assert not game.is_enriched

    @patch("gpcg.application.game_enrichment.WikipediaClient")
    @patch("gpcg.application.game_enrichment.WikidataClient")
    def test_wikipedia_failure_sets_error(self, mock_wd_cls, mock_wiki_cls):
        """Wikipedia fetch failure should set enrichment_error (no description)."""
        mock_wd = MagicMock()
        mock_wd.resolve_game.return_value = _make_wikidata_info()
        mock_wd_cls.return_value = mock_wd

        mock_wiki = MagicMock()
        mock_wiki.get_article_for_game.return_value = None  # Wikipedia failed
        mock_wiki_cls.return_value = mock_wiki

        with session_scope() as s:
            game = get_or_create(s, "Resident Evil 4")
            game_id = game.id

        with session_scope() as s:
            result = enrich_game(s, game_id, force=True)

        assert result is False
        with session_scope() as s:
            game = s.get(Game, game_id)
            assert game.enrichment_state == "error"
            assert game.enrichment_error is not None

    @patch("gpcg.application.game_enrichment.WikidataClient")
    @patch("gpcg.application.game_enrichment.WikipediaClient")
    def test_re_enrichment_with_force_overwrites(self, mock_wiki_cls, mock_wd_cls):
        """Re-enrichment with force=True should overwrite previous data."""
        # First enrichment
        mock_wd = MagicMock()
        mock_wd.resolve_game.return_value = _make_wikidata_info(developer="Capcom")
        mock_wd_cls.return_value = mock_wd

        mock_wiki = MagicMock()
        mock_wiki.get_article_for_game.return_value = _make_wikipedia_article()
        mock_wiki_cls.return_value = mock_wiki

        mock_llm = MagicMock()
        mock_llm.generate.return_value = "Original lore summary that is long enough to pass the length check."

        with session_scope() as s:
            game = get_or_create(s, "Resident Evil 4")
            game_id = game.id

        with session_scope() as s:
            enrich_game(s, game_id, force=True, llm=mock_llm)

        # Second enrichment with different data
        mock_wd.resolve_game.return_value = _make_wikidata_info(developer="Capcom Co., Ltd.")
        mock_llm.generate.return_value = "Updated lore summary that is long enough to pass the length check."

        with session_scope() as s:
            enrich_game(s, game_id, force=True, llm=mock_llm)

        with session_scope() as s:
            game = s.get(Game, game_id)
            assert game.developer == "Capcom Co., Ltd."
            assert "Updated lore" in (game.lore_summary or "")

    @patch("gpcg.application.game_enrichment.WikidataClient")
    @patch("gpcg.application.game_enrichment.WikipediaClient")
    def test_skip_if_already_enriched(self, mock_wiki_cls, mock_wd_cls):
        """enrich_game should skip if already enriched and force=False."""
        mock_wd = MagicMock()
        mock_wd_cls.return_value = mock_wd
        mock_wiki = MagicMock()
        mock_wiki_cls.return_value = mock_wiki

        with session_scope() as s:
            game = get_or_create(s, "Resident Evil 4")
            game_id = game.id
            # Mark as already enriched
            game.enriched_at = datetime.now(timezone.utc)
            game.developer = "Manual Developer"
            s.flush()

        with session_scope() as s:
            result = enrich_game(s, game_id, force=False)

        assert result is True  # Returns True (already enriched)
        # Verify no HTTP calls were made
        mock_wd.resolve_game.assert_not_called()
        mock_wiki.get_article_for_game.assert_not_called()

        with session_scope() as s:
            game = s.get(Game, game_id)
            assert game.developer == "Manual Developer"  # Not overwritten

    @patch("gpcg.application.game_enrichment.WikidataClient")
    @patch("gpcg.application.game_enrichment.WikipediaClient")
    def test_cache_stored_in_metadata(self, mock_wiki_cls, mock_wd_cls):
        """Enrichment should cache results in metadata_json.enrichment_cache."""
        mock_wd = MagicMock()
        mock_wd.resolve_game.return_value = _make_wikidata_info()
        mock_wd_cls.return_value = mock_wd

        mock_wiki = MagicMock()
        mock_wiki.get_article_for_game.return_value = _make_wikipedia_article()
        mock_wiki_cls.return_value = mock_wiki

        mock_llm = MagicMock()
        mock_llm.generate.return_value = "Lore that is long enough to pass the length check."

        with session_scope() as s:
            game = get_or_create(s, "Resident Evil 4")
            game_id = game.id

        with session_scope() as s:
            enrich_game(s, game_id, force=True, llm=mock_llm)

        with session_scope() as s:
            game = s.get(Game, game_id)
            cache = (game.metadata_json or {}).get("enrichment_cache")
            assert cache is not None
            assert "wikidata" in cache
            assert "wikipedia" in cache
            assert "cached_at" in cache
            assert cache["wikidata"]["qid"] == "Q123456"


class TestAutoTriggerEnrichment:
    """Tests for auto-triggering enrichment on game creation."""

    def test_auto_trigger_when_flag_on(self, monkeypatch):
        """When GPCG_GAME_ENRICHMENT_ENABLED=on, creating a game creates a job."""
        monkeypatch.setenv("GPCG_GAME_ENRICHMENT_ENABLED", "true")
        from gpcg.config import get_settings
        get_settings.cache_clear()

        with session_scope() as s:
            game = get_or_create(s, "Auto Trigger Test Game")
            game_id = game.id

        with session_scope() as s:
            from sqlalchemy import select
            job = s.execute(
                select(Job).where(
                    Job.type == JobType.game_enrich.value,
                    Job.game_id == game_id,
                )
            ).scalar_one_or_none()
            assert job is not None
            assert job.status == JobStatus.queued.value

        get_settings.cache_clear()

    def test_no_auto_trigger_when_flag_off(self, monkeypatch):
        """When GPCG_GAME_ENRICHMENT_ENABLED=off, no job is created."""
        monkeypatch.setenv("GPCG_GAME_ENRICHMENT_ENABLED", "false")
        from gpcg.config import get_settings
        get_settings.cache_clear()

        with session_scope() as s:
            game = get_or_create(s, "No Trigger Test Game")
            game_id = game.id

        with session_scope() as s:
            from sqlalchemy import select
            job = s.execute(
                select(Job).where(
                    Job.type == JobType.game_enrich.value,
                    Job.game_id == game_id,
                )
            ).scalar_one_or_none()
            assert job is None

        get_settings.cache_clear()

    def test_auto_trigger_dedup(self, monkeypatch):
        """Auto-trigger should not create duplicate jobs."""
        monkeypatch.setenv("GPCG_GAME_ENRICHMENT_ENABLED", "true")
        from gpcg.config import get_settings
        get_settings.cache_clear()

        with session_scope() as s:
            game = get_or_create(s, "Dedup Test Game")
            game_id = game.id

        # Trigger again by calling get_or_create with the same name (reuses game)
        with session_scope() as s:
            get_or_create(s, "Dedup Test Game")

        with session_scope() as s:
            from sqlalchemy import select
            jobs = s.execute(
                select(Job).where(
                    Job.type == JobType.game_enrich.value,
                    Job.game_id == game_id,
                )
            ).scalars().all()
            assert len(jobs) == 1  # No duplicate

        get_settings.cache_clear()
