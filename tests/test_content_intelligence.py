"""Tests for V2 Content Intelligence — KnowledgeItems, RSS, scoring.

Tests the content intelligence pipeline with mocked RSS feeds and LLM:
- KnowledgeItem CRUD (create, list, reject, stats)
- RSS collection (collect_rss with mocked feedparser)
- Editorial scoring (score_knowledge_item with mocked LLM)
- Deduplication via content_hash
- Content ideas query with scope filtering
- News retention cleanup

Covers ARCHITECTURE_V2.md §7 acceptance criteria.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from gpcg.application.content_collectors import (
    cleanup_old_news,
    collect_rss,
    _compute_hash,
    _normalize_rss_entry,
)
from gpcg.application.knowledge_item_service import (
    get_by_id,
    get_content_ideas,
    get_stats,
    list_items,
    mark_as_used,
    reject_item,
    score_knowledge_item,
    score_all_fresh,
)
from gpcg.domain.game_registry import get_or_create
from gpcg.domain.models import (
    ContentScope,
    Game,
    KnowledgeItem,
    KnowledgeItemSource,
    KnowledgeItemStatus,
    KnowledgeItemType,
)
from gpcg.infrastructure.database import init_db, session_scope


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Use a temp DB for each test."""
    db_path = tmp_path / "test_ci.db"
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


def _make_knowledge_item(
    *,
    game_id=None,
    title="Test News Item",
    content="This is a test news content about a game.",
    item_type=KnowledgeItemType.news.value,
    source_type=KnowledgeItemSource.rss.value,
    editorial_score=50.0,
    status=KnowledgeItemStatus.fresh.value,
    published_at=None,
    franchise=None,
    developer=None,
) -> KnowledgeItem:
    """Create a KnowledgeItem for testing."""
    return KnowledgeItem(
        game_id=game_id,
        title=title,
        content=content,
        item_type=item_type,
        source_type=source_type,
        editorial_score=editorial_score,
        status=status,
        published_at=published_at or datetime.now(timezone.utc),
        collected_at=datetime.now(timezone.utc),
        franchise=franchise,
        developer=developer,
        content_hash=_compute_hash(title, content),
    )


class TestKnowledgeItemCRUD:
    """Tests for KnowledgeItem CRUD operations."""

    def test_create_and_get(self):
        with session_scope() as s:
            item = _make_knowledge_item(title="Test Create")
            s.add(item)
            s.flush()
            item_id = item.id

        with session_scope() as s:
            found = get_by_id(s, item_id)
            assert found is not None
            assert found.title == "Test Create"

    def test_list_items(self):
        with session_scope() as s:
            for i in range(5):
                item = _make_knowledge_item(title=f"Item {i}", editorial_score=float(i * 10))
                s.add(item)
            s.flush()

        with session_scope() as s:
            items = list_items(s, limit=10)
            assert len(items) == 5
            # Should be ordered by editorial_score DESC
            assert items[0].editorial_score >= items[1].editorial_score

    def test_list_filter_by_type(self):
        with session_scope() as s:
            s.add(_make_knowledge_item(title="News", item_type=KnowledgeItemType.news.value))
            s.add(_make_knowledge_item(title="Lore", item_type=KnowledgeItemType.lore.value))
            s.flush()

        with session_scope() as s:
            news = list_items(s, item_type=KnowledgeItemType.news.value)
            assert len(news) == 1
            assert news[0].item_type == KnowledgeItemType.news.value

    def test_list_filter_by_status(self):
        with session_scope() as s:
            s.add(_make_knowledge_item(title="Fresh", status=KnowledgeItemStatus.fresh.value))
            s.add(_make_knowledge_item(title="Used", status=KnowledgeItemStatus.used.value))
            s.flush()

        with session_scope() as s:
            fresh = list_items(s, status=KnowledgeItemStatus.fresh.value)
            assert len(fresh) == 1
            assert fresh[0].status == KnowledgeItemStatus.fresh.value

    def test_reject_item(self):
        with session_scope() as s:
            item = _make_knowledge_item(title="Reject Me")
            s.add(item)
            s.flush()
            item_id = item.id

        with session_scope() as s:
            result = reject_item(s, item_id)
            assert result is True
            s.flush()

        with session_scope() as s:
            item = get_by_id(s, item_id)
            assert item.status == KnowledgeItemStatus.rejected.value

    def test_mark_as_used(self):
        with session_scope() as s:
            item = _make_knowledge_item(title="Use Me")
            s.add(item)
            s.flush()
            item_id = item.id

        with session_scope() as s:
            result = mark_as_used(s, item_id)
            assert result is True
            s.flush()

        with session_scope() as s:
            item = get_by_id(s, item_id)
            assert item.status == KnowledgeItemStatus.used.value

    def test_get_stats(self):
        with session_scope() as s:
            s.add(_make_knowledge_item(title="News 1", item_type=KnowledgeItemType.news.value))
            s.add(_make_knowledge_item(title="News 2", item_type=KnowledgeItemType.news.value))
            s.add(_make_knowledge_item(title="Lore 1", item_type=KnowledgeItemType.lore.value, status=KnowledgeItemStatus.used.value))
            s.flush()

        with session_scope() as s:
            stats = get_stats(s)
            assert stats["total"] == 3
            assert stats["by_type"].get(KnowledgeItemType.news.value) == 2
            assert stats["by_type"].get(KnowledgeItemType.lore.value) == 1
            assert stats["fresh"] == 2  # 2 fresh, 1 used


class TestDeduplication:
    """Tests for content_hash deduplication."""

    def test_same_content_produces_same_hash(self):
        hash1 = _compute_hash("Same Title", "Same Content")
        hash2 = _compute_hash("Same Title", "Same Content")
        assert hash1 == hash2

    def test_different_content_produces_different_hash(self):
        hash1 = _compute_hash("Title 1", "Content 1")
        hash2 = _compute_hash("Title 2", "Content 2")
        assert hash1 != hash2

    def test_case_insensitive_hash(self):
        hash1 = _compute_hash("Test Title", "Test Content")
        hash2 = _compute_hash("TEST TITLE", "TEST CONTENT")
        assert hash1 == hash2  # normalized to lowercase


class TestRSSCollection:
    """Tests for RSS collection with mocked feedparser."""

    @patch("feedparser.parse")
    def test_collect_rss_creates_items(self, mock_parse):
        """collect_rss should create KnowledgeItems from RSS entries."""
        mock_feed = MagicMock()
        mock_feed.entries = [
            {
                "title": "New DLC Announced",
                "summary": "Exciting new DLC content coming next month.",
                "link": "https://example.com/news/1",
                "published_parsed": None,
            },
            {
                "title": "Game Review",
                "summary": "A comprehensive review of the latest game.",
                "link": "https://example.com/news/2",
                "published_parsed": None,
            },
        ]
        mock_parse.return_value = mock_feed

        with session_scope() as s:
            game = get_or_create(s, "Test RSS Game")
            game_id = game.id

        with session_scope() as s:
            count = collect_rss(s, game_id)

        assert count == 2
        with session_scope() as s:
            items = list_items(s, game_id=game_id)
            assert len(items) == 2
            titles = [i.title for i in items]
            assert "New DLC Announced" in titles
            assert "Game Review" in titles

    @patch("feedparser.parse")
    def test_collect_rss_dedup(self, mock_parse):
        """collect_rss should not create duplicate items."""
        mock_feed = MagicMock()
        mock_feed.entries = [
            {
                "title": "Duplicate News",
                "summary": "Same content",
                "link": "https://example.com/1",
                "published_parsed": None,
            },
        ]
        mock_parse.return_value = mock_feed

        with session_scope() as s:
            game = get_or_create(s, "Dedup RSS Game")
            game_id = game.id

        # First collection
        with session_scope() as s:
            count1 = collect_rss(s, game_id)

        # Second collection (same feed)
        with session_scope() as s:
            count2 = collect_rss(s, game_id)

        assert count1 == 1
        assert count2 == 0  # duplicate, not added

    @patch("feedparser.parse")
    def test_collect_rss_denormalizes_franchise_developer(self, mock_parse):
        """collect_rss should denormalize franchise/developer from Game."""
        mock_feed = MagicMock()
        mock_feed.entries = [
            {
                "title": "News with Franchise",
                "summary": "Content",
                "link": "https://example.com/1",
                "published_parsed": None,
            },
        ]
        mock_parse.return_value = mock_feed

        with session_scope() as s:
            game = get_or_create(s, "Franchise Test Game")
            game.franchise = "Test Franchise"
            game.developer = "Test Developer"
            s.flush()
            game_id = game.id

        with session_scope() as s:
            collect_rss(s, game_id)

        with session_scope() as s:
            items = list_items(s, game_id=game_id)
            assert len(items) == 1
            assert items[0].franchise == "Test Franchise"
            assert items[0].developer == "Test Developer"


class TestEditorialScoring:
    """Tests for editorial scoring with mocked LLM."""

    def test_score_knowledge_item(self):
        """score_knowledge_item should set editorial_score from LLM response."""
        mock_llm = MagicMock()
        mock_llm.generate.return_value = "85"

        with session_scope() as s:
            item = _make_knowledge_item(title="Score Me", editorial_score=0.0)
            s.add(item)
            s.flush()

            score = score_knowledge_item(item, mock_llm)
            assert score == 85.0
            assert item.editorial_score == 85.0

    def test_score_parses_number_from_text(self):
        """Score should extract number from LLM text response."""
        mock_llm = MagicMock()
        mock_llm.generate.return_value = "I rate this 72 out of 100."

        with session_scope() as s:
            item = _make_knowledge_item(editorial_score=0.0)
            s.add(item)
            s.flush()

            score = score_knowledge_item(item, mock_llm)
            assert score == 72.0

    def test_score_defaults_on_llm_failure(self):
        """Score should default to 30 on LLM failure."""
        mock_llm = MagicMock()
        mock_llm.generate.side_effect = Exception("LLM unavailable")

        with session_scope() as s:
            item = _make_knowledge_item(editorial_score=0.0)
            s.add(item)
            s.flush()

            score = score_knowledge_item(item, mock_llm)
            assert score == 30.0
            assert item.editorial_score == 30.0

    def test_score_all_fresh(self):
        """score_all_fresh should score all fresh items with score=0."""
        mock_llm = MagicMock()
        mock_llm.generate.return_value = "60"

        with session_scope() as s:
            for i in range(3):
                s.add(_make_knowledge_item(title=f"Fresh {i}", editorial_score=0.0))
            s.add(_make_knowledge_item(title="Already Scored", editorial_score=50.0))
            s.flush()

            count = score_all_fresh(s, mock_llm, limit=10)
            assert count == 3  # only the 3 with score=0


class TestContentIdeas:
    """Tests for the unified content ideas query."""

    def test_get_content_ideas_game_scope(self):
        """get_content_ideas with scope=game returns items for that game only."""
        with session_scope() as s:
            game1 = get_or_create(s, "Ideas Game 1")
            game2 = get_or_create(s, "Ideas Game 2")
            s.flush()

            s.add(_make_knowledge_item(game_id=game1.id, title="Game 1 News", editorial_score=80.0))
            s.add(_make_knowledge_item(game_id=game2.id, title="Game 2 News", editorial_score=90.0))
            s.flush()

        with session_scope() as s:
            ideas = get_content_ideas(s, game1.id, scope=ContentScope.game.value)
            assert len(ideas) == 1
            assert ideas[0]["title"] == "Game 1 News"

    def test_get_content_ideas_franchise_scope(self):
        """get_content_ideas with scope=franchise returns items from same franchise."""
        with session_scope() as s:
            game1 = get_or_create(s, "Franchise Game A")
            game1.franchise = "Same Franchise"
            game2 = get_or_create(s, "Franchise Game B")
            game2.franchise = "Same Franchise"
            game3 = get_or_create(s, "Other Game")
            game3.franchise = "Other Franchise"
            s.flush()

            s.add(_make_knowledge_item(game_id=game1.id, title="A News", editorial_score=70.0, franchise="Same Franchise"))
            s.add(_make_knowledge_item(game_id=game2.id, title="B News", editorial_score=80.0, franchise="Same Franchise"))
            s.add(_make_knowledge_item(game_id=game3.id, title="Other News", editorial_score=90.0, franchise="Other Franchise"))
            s.flush()

        with session_scope() as s:
            ideas = get_content_ideas(s, game1.id, scope=ContentScope.franchise.value)
            titles = [i["title"] for i in ideas]
            assert "A News" in titles
            assert "B News" in titles
            assert "Other News" not in titles

    def test_get_content_ideas_ordered_by_score(self):
        """Content ideas should be ordered by editorial_score DESC."""
        with session_scope() as s:
            game = get_or_create(s, "Score Order Game")
            s.flush()

            s.add(_make_knowledge_item(game_id=game.id, title="Low Score", editorial_score=30.0))
            s.add(_make_knowledge_item(game_id=game.id, title="High Score", editorial_score=90.0))
            s.add(_make_knowledge_item(game_id=game.id, title="Mid Score", editorial_score=60.0))
            s.flush()

        with session_scope() as s:
            ideas = get_content_ideas(s, game.id, scope=ContentScope.game.value, limit=10)
            # min_editorial_score default is 50, so only High (90) and Mid (60) should appear
            assert len(ideas) >= 1
            assert ideas[0]["editorial_score"] >= ideas[-1]["editorial_score"]


class TestNewsRetention:
    """Tests for news retention cleanup."""

    def test_cleanup_old_news(self):
        """cleanup_old_news should delete fresh news older than N days."""
        old_date = datetime.now(timezone.utc) - timedelta(days=45)
        recent_date = datetime.now(timezone.utc) - timedelta(days=5)

        with session_scope() as s:
            game = get_or_create(s, "Retention Test Game")
            s.flush()

            # Old news (should be deleted)
            s.add(_make_knowledge_item(
                game_id=game.id, title="Old News",
                item_type=KnowledgeItemType.news.value,
                published_at=old_date,
            ))
            # Recent news (should be kept)
            s.add(_make_knowledge_item(
                game_id=game.id, title="Recent News",
                item_type=KnowledgeItemType.news.value,
                published_at=recent_date,
            ))
            # Old lore (should be kept — evergreen)
            s.add(_make_knowledge_item(
                game_id=game.id, title="Old Lore",
                item_type=KnowledgeItemType.lore.value,
                published_at=old_date,
            ))
            s.flush()

        with session_scope() as s:
            deleted = cleanup_old_news(s, days=30)
            assert deleted == 1  # only old news deleted

        with session_scope() as s:
            items = list_items(s, game_id=game.id, limit=10)
            titles = [i.title for i in items]
            assert "Old News" not in titles
            assert "Recent News" in titles
            assert "Old Lore" in titles  # evergreen kept
