"""REFACTORY_V2 — Content/sources tests (Fase 3).

Tests cover:
1. Feature flags activated by default (content_intelligence, curiosity_scoring)
2. KnowledgeItem scoring with clickbait/promotion/rumor gate
3. Provenance tracking in ContentPlan.metadata_json
4. rejection_reason field on KnowledgeItem
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from gpcg.core.models import (
    Base,
    ContentPlan,
    Fact,
    KnowledgeItem,
    KnowledgeItemStatus,
    KnowledgeItemType,
    KnowledgeItemSource,
    User,
)
from gpcg.domains.games.models import Game


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


class TestFeatureFlagsActivated:
    """REFACTORY_V2: content_intelligence and curiosity_scoring default to True."""

    def test_content_intelligence_enabled_by_default(self):
        from gpcg.config import get_settings
        settings = get_settings()
        assert settings.gpcg_content_intelligence_enabled is True

    def test_curiosity_scoring_enabled_by_default(self):
        from gpcg.config import get_settings
        settings = get_settings()
        assert settings.gpcg_curiosity_scoring_enabled is True


class TestKnowledgeItemQualityGate:
    """REFACTORY_V2: deterministic clickbait/promotion/rumor detection."""

    def _make_ki(self, title, content, **kwargs):
        return KnowledgeItem(
            title=title,
            content=content,
            item_type=KnowledgeItemType.news.value,
            source_type=KnowledgeItemSource.rss.value,
            status=KnowledgeItemStatus.fresh.value,
            editorial_score=0.0,
            **kwargs,
        )

    def test_detect_clickbait(self, db_session):
        from gpcg.application.knowledge_item_service import _detect_quality_issues
        ki = self._make_ki(
            "Você não vai acreditar no que aconteceu",
            "Algum conteúdo sobre o jogo",
        )
        assert _detect_quality_issues(ki) == "clickbait"

    def test_detect_promotion(self, db_session):
        from gpcg.application.knowledge_item_service import _detect_quality_issues
        ki = self._make_ki(
            "Nova oferta",
            "Compre agora o novo jogo com desconto",
        )
        assert _detect_quality_issues(ki) == "promotion"

    def test_detect_rumor(self, db_session):
        from gpcg.application.knowledge_item_service import _detect_quality_issues
        ki = self._make_ki(
            "Rumor sobre novo jogo",
            "Segundo fontes, o jogo pode ser anunciado",
        )
        assert _detect_quality_issues(ki) == "rumor"

    def test_no_quality_issue_for_legitimate_content(self, db_session):
        from gpcg.application.knowledge_item_service import _detect_quality_issues
        ki = self._make_ki(
            "Novo patch traz correções importantes",
            "O desenvolvedor anunciou correções de bugs no patch 1.2",
        )
        assert _detect_quality_issues(ki) is None

    def test_scoring_rejects_clickbait(self, db_session):
        """Clickbait KI should be auto-rejected with penalty score."""
        from gpcg.application.knowledge_item_service import score_knowledge_item

        class MockLLM:
            def generate(self, prompt, **kwargs):
                return "85"  # Would be a high score, but gate should reject

        ki = self._make_ki(
            "Você não vai acreditar neste truque",
            "Conteúdo sobre o jogo",
        )
        db_session.add(ki)
        db_session.flush()

        score = score_knowledge_item(ki, MockLLM())
        assert score <= 15.0  # clickbait penalty
        assert ki.status == KnowledgeItemStatus.rejected.value
        assert ki.rejection_reason is not None
        assert "clickbait" in ki.rejection_reason

    def test_scoring_rejects_promotion(self, db_session):
        """Promotional KI should be auto-rejected."""
        from gpcg.application.knowledge_item_service import score_knowledge_item

        class MockLLM:
            def generate(self, prompt, **kwargs):
                return "90"

        ki = self._make_ki(
            "Oferta especial",
            "Compre agora com cupom de desconto",
        )
        db_session.add(ki)
        db_session.flush()

        score = score_knowledge_item(ki, MockLLM())
        assert score <= 10.0
        assert ki.status == KnowledgeItemStatus.rejected.value

    def test_scoring_does_not_call_llm_for_clickbait(self, db_session):
        """Quality gate should short-circuit — no LLM call needed."""
        from gpcg.application.knowledge_item_service import score_knowledge_item

        class TrackingLLM:
            called = False
            def generate(self, prompt, **kwargs):
                TrackingLLM.called = True
                return "85"

        ki = self._make_ki(
            "Você não vai acreditar",
            "Conteúdo",
        )
        db_session.add(ki)
        db_session.flush()

        score_knowledge_item(ki, TrackingLLM())
        assert TrackingLLM.called is False  # LLM should NOT be called

    def test_scoring_calls_llm_for_legitimate_content(self, db_session):
        """Legitimate content should proceed to LLM scoring."""
        from gpcg.application.knowledge_item_service import score_knowledge_item

        class TrackingLLM:
            called = False
            def generate(self, prompt, **kwargs):
                TrackingLLM.called = True
                return "75"

        ki = self._make_ki(
            "Novo patch traz correções",
            "O desenvolvedor anunciou correções de bugs",
        )
        db_session.add(ki)
        db_session.flush()

        score = score_knowledge_item(ki, TrackingLLM())
        assert TrackingLLM.called is True
        assert score == 75.0
        assert ki.status == KnowledgeItemStatus.fresh.value  # not rejected


class TestProvenanceTracking:
    """REFACTORY_V2: ContentPlan.metadata_json includes provenance chain."""

    def test_provenance_in_metadata_for_fact_based_plan(self, db_session):
        """ContentPlan created from a fact should have provenance.source_type=fact."""
        user = User(email="test@test.com", name="Test", is_active=True)
        db_session.add(user)
        db_session.flush()

        game = Game(canonical_name="Test Game", user_id=user.id)
        db_session.add(game)
        db_session.flush()

        fact = Fact(
            user_id=user.id, game_id=game.id,
            category="trivia", claim="Test claim",
            quality_score=80.0, novelty_score=70.0,
        )
        db_session.add(fact)
        db_session.flush()

        plan = ContentPlan(
            user_id=user.id,
            game_id=game.id,
            fact_id=fact.id,
            topic="Test topic",
            hook="Test hook",
            metadata_json={
                "reasoning": "test",
                "provenance": {
                    "source_type": "fact",
                    "source_id": fact.id,
                    "source_claim": fact.claim,
                    "game_name": game.canonical_name,
                },
            },
        )
        db_session.add(plan)
        db_session.flush()

        meta = plan.metadata_json or {}
        assert "provenance" in meta
        prov = meta["provenance"]
        assert prov["source_type"] == "fact"
        assert prov["source_id"] == fact.id
        assert prov["source_claim"] == "Test claim"

    def test_provenance_in_metadata_for_ki_based_plan(self, db_session):
        """ContentPlan created from a KnowledgeItem should have provenance.source_type=knowledge_item."""
        user = User(email="test@test.com", name="Test", is_active=True)
        db_session.add(user)
        db_session.flush()

        game = Game(canonical_name="Test Game", user_id=user.id)
        db_session.add(game)
        db_session.flush()

        ki = KnowledgeItem(
            user_id=None, game_id=game.id,  # system-collected
            title="Breaking News",
            content="Important news about the game",
            item_type=KnowledgeItemType.news.value,
            source_type=KnowledgeItemSource.rss.value,
            status=KnowledgeItemStatus.fresh.value,
            editorial_score=75.0,
        )
        db_session.add(ki)
        db_session.flush()

        plan = ContentPlan(
            user_id=user.id,
            game_id=game.id,
            topic="News topic",
            hook="News hook",
            metadata_json={
                "reasoning": "test",
                "knowledge_item_id": ki.id,
                "provenance": {
                    "source_type": "knowledge_item",
                    "source_id": ki.id,
                    "game_name": game.canonical_name,
                },
            },
        )
        db_session.add(plan)
        db_session.flush()

        meta = plan.metadata_json or {}
        prov = meta["provenance"]
        assert prov["source_type"] == "knowledge_item"
        assert prov["source_id"] == ki.id


class TestRejectionReasonField:
    """REFACTORY_V2: KnowledgeItem has rejection_reason field."""

    def test_rejection_reason_nullable(self, db_session):
        ki = KnowledgeItem(
            title="Test",
            content="Content",
            item_type=KnowledgeItemType.news.value,
            source_type=KnowledgeItemSource.rss.value,
            status=KnowledgeItemStatus.fresh.value,
        )
        db_session.add(ki)
        db_session.flush()
        assert ki.rejection_reason is None

    def test_rejection_reason_set(self, db_session):
        ki = KnowledgeItem(
            title="Test",
            content="Content",
            item_type=KnowledgeItemType.news.value,
            source_type=KnowledgeItemSource.rss.value,
            status=KnowledgeItemStatus.rejected.value,
            rejection_reason="auto-rejected: clickbait detected",
        )
        db_session.add(ki)
        db_session.flush()
        assert ki.rejection_reason == "auto-rejected: clickbait detected"
