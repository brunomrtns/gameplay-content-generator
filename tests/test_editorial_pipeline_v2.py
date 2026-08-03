"""Tests for V2 Editorial Pipeline — content planning with KnowledgeItems.

Tests the integration of KnowledgeItems into the content planning pipeline
and the video.knowledge_item_id linkage.

Covers ARCHITECTURE_V2.md §9.1 (content_planning + script_review changes).
"""

from unittest.mock import MagicMock

import pytest

from gpcg.application.content_collectors import _compute_hash
from gpcg.application.content_planning_service import ContentPlanningService
from gpcg.domain.game_registry import get_or_create
from gpcg.domain.models import (
    ContentScope,
    Fact,
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
    db_path = tmp_path / "test_editorial_v2.db"
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


def _make_fact(game_id, claim="Test fact claim", quality=0.8, novelty=0.7):
    """Create a Fact for testing."""
    return Fact(
        game_id=game_id,
        category="trivia",
        claim=claim,
        quality_score=quality,
        novelty_score=novelty,
        used_count=0,
    )


def _make_knowledge_item(
    game_id,
    title="Test News",
    content="Test news content about a game",
    editorial_score=75.0,
    item_type=KnowledgeItemType.news.value,
    franchise=None,
    developer=None,
):
    """Create a KnowledgeItem for testing."""
    return KnowledgeItem(
        game_id=game_id,
        title=title,
        content=content,
        item_type=item_type,
        source_type=KnowledgeItemSource.rss.value,
        editorial_score=editorial_score,
        status=KnowledgeItemStatus.fresh.value,
        franchise=franchise,
        developer=developer,
        content_hash=_compute_hash(title, content),
    )


class TestContentPlanningWithKnowledgeItems:
    """Tests for content planning integrating KnowledgeItems."""

    def test_planning_considers_knowledge_items_when_enabled(self, monkeypatch):
        """When GPCG_CONTENT_INTELLIGENCE_ENABLED is on, planning includes KIs."""
        monkeypatch.setenv("GPCG_CONTENT_INTELLIGENCE_ENABLED", "true")
        monkeypatch.setenv("GPCG_CONTENT_MIN_EDITORIAL_SCORE", "50")
        from gpcg.config import get_settings
        get_settings.cache_clear()

        with session_scope() as s:
            game = get_or_create(s, "Editorial V2 Game")
            s.flush()
            # Add a fact
            s.add(_make_fact(game.id, claim="A fact about the game"))
            # Add a knowledge item
            s.add(_make_knowledge_item(game.id, title="Breaking News", content="Big news about the game"))
            s.flush()

        # Mock LLM that picks the knowledge item
        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "fact_id": None,
            "knowledge_item_id": 1,  # Will be set after flush
            "topic": "Breaking News Topic",
            "hook": "Did you hear the news?",
            "tone": "energetic",
            "energy": 0.8,
            "music_mood": "energetic",
            "visual_strategy": "fast_cuts",
            "reasoning": "News is fresh and relevant",
        }

        service = ContentPlanningService(llm=mock_llm)

        with session_scope() as s:
            # Get the KI id
            ki = s.query(KnowledgeItem).first()
            mock_llm.chat_json.return_value["knowledge_item_id"] = ki.id

            plan = service.plan_for_game(s, game.id)

        assert plan is not None
        assert plan.fact_id is None
        # metadata should record the knowledge_item_id
        meta = plan.metadata_json or {}
        assert meta.get("knowledge_item_id") == ki.id

        # KI should be marked as used
        with session_scope() as s:
            ki = s.get(KnowledgeItem, ki.id)
            assert ki.status == KnowledgeItemStatus.used.value

        get_settings.cache_clear()

    def test_planning_without_knowledge_items_when_disabled(self, monkeypatch):
        """When GPCG_CONTENT_INTELLIGENCE_ENABLED is off, planning ignores KIs."""
        monkeypatch.setenv("GPCG_CONTENT_INTELLIGENCE_ENABLED", "false")
        from gpcg.config import get_settings
        get_settings.cache_clear()

        with session_scope() as s:
            game = get_or_create(s, "Disabled Editorial Game")
            s.flush()
            s.add(_make_fact(game.id, claim="A fact"))
            s.add(_make_knowledge_item(game.id, title="Ignored News"))
            s.flush()

        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "fact_id": 1,
            "knowledge_item_id": None,
            "topic": "Fact Topic",
            "hook": "Hook",
            "tone": "curious",
            "energy": 0.7,
            "music_mood": "neutral",
            "visual_strategy": "gameplay_compilation",
            "reasoning": "Best fact",
        }

        service = ContentPlanningService(llm=mock_llm)

        with session_scope() as s:
            fact = s.query(Fact).first()
            mock_llm.chat_json.return_value["fact_id"] = fact.id
            plan = service.plan_for_game(s, game.id)

        assert plan is not None
        assert plan.fact_id == fact.id
        # KI should NOT be marked as used
        with session_scope() as s:
            ki = s.query(KnowledgeItem).first()
            assert ki.status == KnowledgeItemStatus.fresh.value

        get_settings.cache_clear()

    def test_planning_with_scope_franchise(self, monkeypatch):
        """Content planning with scope=franchise should include KIs from same franchise."""
        monkeypatch.setenv("GPCG_CONTENT_INTELLIGENCE_ENABLED", "true")
        monkeypatch.setenv("GPCG_CONTENT_MIN_EDITORIAL_SCORE", "50")
        from gpcg.config import get_settings
        get_settings.cache_clear()

        with session_scope() as s:
            game1 = get_or_create(s, "Franchise Planning Game 1")
            game1.franchise = "Shared Franchise"
            game2 = get_or_create(s, "Franchise Planning Game 2")
            game2.franchise = "Shared Franchise"
            s.flush()

            # KI for game2 (same franchise, should be found via scope=franchise)
            s.add(_make_knowledge_item(
                game2.id, title="Franchise News",
                content="News about the franchise",
                franchise="Shared Franchise",
            ))
            s.flush()

        service = ContentPlanningService(llm=MagicMock())

        # Test the internal helper directly
        with session_scope() as s:
            items = service._get_knowledge_items(s, game1.id, ContentScope.franchise.value)
            assert len(items) == 1
            assert items[0].title == "Franchise News"

        get_settings.cache_clear()

    def test_planning_fallback_to_facts_when_no_kis(self, monkeypatch):
        """When no KIs are available, planning should fall back to facts."""
        monkeypatch.setenv("GPCG_CONTENT_INTELLIGENCE_ENABLED", "true")
        monkeypatch.setenv("GPCG_CONTENT_MIN_EDITORIAL_SCORE", "50")
        from gpcg.config import get_settings
        get_settings.cache_clear()

        with session_scope() as s:
            game = get_or_create(s, "Fallback Game")
            s.flush()
            s.add(_make_fact(game.id, claim="The only fact"))
            # No KIs
            s.flush()

        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "fact_id": 1,
            "knowledge_item_id": None,
            "topic": "Fact Topic",
            "hook": "Hook",
            "tone": "curious",
            "energy": 0.7,
            "music_mood": "neutral",
            "visual_strategy": "gameplay_compilation",
            "reasoning": "Only option",
        }

        service = ContentPlanningService(llm=mock_llm)

        with session_scope() as s:
            fact = s.query(Fact).first()
            mock_llm.chat_json.return_value["fact_id"] = fact.id
            plan = service.plan_for_game(s, game.id)

        assert plan is not None
        assert plan.fact_id == fact.id

        get_settings.cache_clear()


class TestEditorialPlannerEnrichedContext:
    """Tests for editorial planner with enriched Game context."""

    def test_gather_context_includes_enriched_fields(self):
        """_gather_context should include description, lore_summary, genres."""
        with session_scope() as s:
            game = get_or_create(s, "Enriched Context Game")
            game.description = "A survival horror game"
            game.lore_summary = "The game takes place in a mysterious town..."
            game.genres = ["survival horror", "action"]
            game.franchise = "Silent Hill"
            game.developer = "Konami"
            s.flush()

            from gpcg.application.editorial_planner import EditorialPlanner
            from gpcg.domain.models import ContentPlan
            plan = ContentPlan(
                game_id=game.id,
                format="9:16",
                target_duration=60,
                topic="Test",
                hook="Hook",
                tone="curious",
                energy=0.7,
                music_mood="neutral",
                visual_strategy="gameplay_compilation",
            )
            s.add(plan)
            s.flush()

            planner = EditorialPlanner()
            context = planner._gather_context(s, plan, "GAME_RELATED", None)

            assert context["game_description"] == "A survival horror game"
            assert "mysterious town" in context["lore_summary"]
            assert "survival horror" in context["genres"]
            assert context["franchise"] == "Silent Hill"
            assert context["developer"] == "Konami"

    def test_gather_context_without_enrichment(self):
        """_gather_context should work with non-enriched games (empty fields)."""
        with session_scope() as s:
            game = get_or_create(s, "Non-Enriched Game")
            # No enrichment — all fields are None/empty
            s.flush()

            from gpcg.application.editorial_planner import EditorialPlanner
            from gpcg.domain.models import ContentPlan
            plan = ContentPlan(
                game_id=game.id,
                format="9:16",
                target_duration=60,
                topic="Test",
                hook="Hook",
                tone="curious",
                energy=0.7,
                music_mood="neutral",
                visual_strategy="gameplay_compilation",
            )
            s.add(plan)
            s.flush()

            planner = EditorialPlanner()
            context = planner._gather_context(s, plan, "GAME_RELATED", None)

            assert context["game_name"] == "Non-Enriched Game"
            assert context["game_description"] == ""
            assert context["lore_summary"] == ""
            assert context["genres"] == []
