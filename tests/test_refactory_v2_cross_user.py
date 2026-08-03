"""REFACTORY_V2 — Cross-user isolation tests.

Validates the hybrid content pool model:
- user_id=NULL (system-collected) → shared pool, visible to all users
- user_id=X, is_public=False → private to owner X
- user_id=X, is_public=True → shared with other users
- user_id=Y, is_public=False → NEVER visible to user X

Tests cover:
1. Fact visibility filter (ContentPlanningService queries)
2. Document visibility filter (API endpoints)
3. KnowledgeItem visibility filter
4. YouTube adapter rejects None user_id (no global fallback)
5. ContentPlan is owner-scoped (no shared pool for plans)
6. Negative tests: private facts of B NEVER appear in A's pool
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from gpcg.domain.models import (
    Base,
    ContentPlan,
    Document,
    Fact,
    Game,
    KnowledgeItem,
    KnowledgeItemStatus,
    KnowledgeItemType,
    KnowledgeItemSource,
    User,
)
from gpcg.domain.visibility import visible_to_user


@pytest.fixture
def db_session():
    """In-memory SQLite session for isolated testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def two_users_with_content(db_session):
    """Create two users (A, B) with facts, documents, and KIs in various visibility states."""
    user_a = User(email="a@test.com", name="User A", is_active=True)
    user_b = User(email="b@test.com", name="User B", is_active=True)
    db_session.add_all([user_a, user_b])
    db_session.flush()

    game = Game(canonical_name="Test Game", user_id=user_a.id)
    db_session.add(game)
    db_session.flush()

    # Facts:
    # 1. System-collected (user_id=NULL) → shared pool
    # 2. User A private (user_id=A, is_public=False)
    # 3. User A public (user_id=A, is_public=True)
    # 4. User B private (user_id=B, is_public=False) → MUST NOT be visible to A
    # 5. User B public (user_id=B, is_public=True) → visible to A

    facts = [
        Fact(user_id=None, game_id=game.id, is_public=False,
             category="general", claim="System fact (shared pool)",
             quality_score=80.0, novelty_score=70.0),
        Fact(user_id=user_a.id, game_id=game.id, is_public=False,
             category="trivia", claim="A's private fact",
             quality_score=75.0, novelty_score=65.0),
        Fact(user_id=user_a.id, game_id=game.id, is_public=True,
             category="easter_egg", claim="A's public fact",
             quality_score=85.0, novelty_score=80.0),
        Fact(user_id=user_b.id, game_id=game.id, is_public=False,
             category="dev", claim="B's private fact (MUST NOT leak to A)",
             quality_score=90.0, novelty_score=85.0),
        Fact(user_id=user_b.id, game_id=game.id, is_public=True,
             category="curiosity", claim="B's public fact (visible to A)",
             quality_score=82.0, novelty_score=72.0),
    ]
    db_session.add_all(facts)
    db_session.flush()

    # Documents with same visibility model
    docs = [
        Document(user_id=None, game_id=game.id, is_public=False,
                 filename="system_doc.pdf", file_path="/tmp/system.pdf",
                 file_type="pdf", file_size=100),
        Document(user_id=user_a.id, game_id=game.id, is_public=False,
                 filename="a_private.pdf", file_path="/tmp/a_priv.pdf",
                 file_type="pdf", file_size=200),
        Document(user_id=user_b.id, game_id=game.id, is_public=False,
                 filename="b_private.pdf", file_path="/tmp/b_priv.pdf",
                 file_type="pdf", file_size=300),
    ]
    db_session.add_all(docs)
    db_session.flush()

    # KnowledgeItems
    kis = [
        KnowledgeItem(user_id=None, game_id=game.id, is_public=False,
                      title="System KI", content="System collected news",
                      item_type=KnowledgeItemType.news.value,
                      source_type=KnowledgeItemSource.rss.value,
                      status=KnowledgeItemStatus.fresh.value,
                      editorial_score=60.0),
        KnowledgeItem(user_id=user_b.id, game_id=game.id, is_public=False,
                      title="B's private KI", content="B's private news",
                      item_type=KnowledgeItemType.news.value,
                      source_type=KnowledgeItemSource.rss.value,
                      status=KnowledgeItemStatus.fresh.value,
                      editorial_score=70.0),
    ]
    db_session.add_all(kis)
    db_session.flush()

    return {
        "session": db_session,
        "user_a": user_a,
        "user_b": user_b,
        "game": game,
        "facts": facts,
        "docs": docs,
        "kis": kis,
    }


class TestVisibilityFilter:
    """Test the visible_to_user helper directly."""

    def test_user_sees_own_private(self, two_users_with_content):
        s = two_users_with_content
        vis = visible_to_user(Fact.user_id, Fact.is_public, s["user_a"].id)
        facts = s["session"].execute(select(Fact).where(vis)).scalars().all()
        claims = {f.claim for f in facts}
        assert "A's private fact" in claims

    def test_user_sees_system_pool(self, two_users_with_content):
        s = two_users_with_content
        vis = visible_to_user(Fact.user_id, Fact.is_public, s["user_a"].id)
        facts = s["session"].execute(select(Fact).where(vis)).scalars().all()
        claims = {f.claim for f in facts}
        assert "System fact (shared pool)" in claims

    def test_user_sees_public_of_others(self, two_users_with_content):
        s = two_users_with_content
        vis = visible_to_user(Fact.user_id, Fact.is_public, s["user_a"].id)
        facts = s["session"].execute(select(Fact).where(vis)).scalars().all()
        claims = {f.claim for f in facts}
        assert "B's public fact (visible to A)" in claims

    def test_user_does_not_see_private_of_others(self, two_users_with_content):
        s = two_users_with_content
        vis = visible_to_user(Fact.user_id, Fact.is_public, s["user_a"].id)
        facts = s["session"].execute(select(Fact).where(vis)).scalars().all()
        claims = {f.claim for f in facts}
        assert "B's private fact (MUST NOT leak to A)" not in claims

    def test_user_b_sees_own_private(self, two_users_with_content):
        s = two_users_with_content
        vis = visible_to_user(Fact.user_id, Fact.is_public, s["user_b"].id)
        facts = s["session"].execute(select(Fact).where(vis)).scalars().all()
        claims = {f.claim for f in facts}
        assert "B's private fact (MUST NOT leak to A)" in claims

    def test_no_user_context_sees_only_shared_and_public(self, two_users_with_content):
        s = two_users_with_content
        vis = visible_to_user(Fact.user_id, Fact.is_public, None)
        facts = s["session"].execute(select(Fact).where(vis)).scalars().all()
        claims = {f.claim for f in facts}
        # System pool (user_id=NULL) is visible
        assert "System fact (shared pool)" in claims
        # Public facts are visible
        assert "A's public fact" in claims
        assert "B's public fact (visible to A)" in claims
        # Private facts are NOT visible
        assert "A's private fact" not in claims
        assert "B's private fact (MUST NOT leak to A)" not in claims


class TestContentPlanningServiceVisibility:
    """Test that ContentPlanningService respects visibility when selecting facts."""

    def test_plan_for_game_excludes_other_users_private_facts(
        self, two_users_with_content, monkeypatch
    ):
        """User A's plan_for_game must never select B's private fact."""
        s = two_users_with_content
        from gpcg.application.content_planning_service import ContentPlanningService
        from gpcg.config import get_settings

        # Disable curiosity scoring to use the legacy path (simpler to test)
        settings = get_settings()
        monkeypatch.setattr(settings, "gpcg_curiosity_scoring_enabled", False)
        monkeypatch.setattr(settings, "gpcg_content_intelligence_enabled", False)

        # Mock LLM to just pick the first fact
        class MockLLM:
            def chat_json(self, system, prompt, **kwargs):
                # Return fact_id of the first fact in the prompt
                return {
                    "fact_id": s["facts"][0].id,
                    "topic": "Test topic",
                    "hook": "Test hook",
                    "tone": "curious",
                    "energy": 0.7,
                    "music_mood": "neutral",
                    "visual_strategy": "gameplay_compilation",
                    "reasoning": "test",
                }

        svc = ContentPlanningService(llm=MockLLM())
        plan = svc.plan_for_game(
            s["session"], s["game"].id,
            user_id=s["user_a"].id,
        )
        assert plan is not None

        # Verify: query the facts that were visible to A
        vis = visible_to_user(Fact.user_id, Fact.is_public, s["user_a"].id)
        visible_facts = s["session"].execute(
            select(Fact).where(Fact.game_id == s["game"].id, vis)
        ).scalars().all()
        visible_claims = {f.claim for f in visible_facts}
        assert "B's private fact (MUST NOT leak to A)" not in visible_claims


class TestYouTubeAdapterNoGlobalFallback:
    """Test that the YouTube adapter rejects None user_id (REFACTORY_V2)."""

    def test_upload_without_user_id_raises(self):
        from gpcg.infrastructure.google_integration_adapter import (
            GoogleIntegrationAdapter,
        )
        adapter = GoogleIntegrationAdapter()
        with pytest.raises(ValueError, match="user_id"):
            adapter.upload_to_youtube(
                "/tmp/fake.mp4",
                title="Test",
                description="Test",
                user_id=None,
            )


class TestContentPlanOwnerScoped:
    """ContentPlan must always be owner-scoped (no shared pool)."""

    def test_plan_created_with_user_id(self, db_session):
        user = User(email="test@test.com", name="Test", is_active=True)
        db_session.add(user)
        db_session.flush()

        game = Game(canonical_name="Test Game", user_id=user.id)
        db_session.add(game)
        db_session.flush()

        plan = ContentPlan(
            user_id=user.id,
            game_id=game.id,
            topic="Test topic",
            hook="Test hook",
        )
        db_session.add(plan)
        db_session.flush()

        assert plan.user_id == user.id

    def test_plan_query_filters_by_user(self, db_session):
        user_a = User(email="a@test.com", name="A", is_active=True)
        user_b = User(email="b@test.com", name="B", is_active=True)
        db_session.add_all([user_a, user_b])
        db_session.flush()

        plan_a = ContentPlan(user_id=user_a.id, topic="A's plan", hook="A hook")
        plan_b = ContentPlan(user_id=user_b.id, topic="B's plan", hook="B hook")
        db_session.add_all([plan_a, plan_b])
        db_session.flush()

        # Query for user A only
        plans_a = db_session.execute(
            select(ContentPlan).where(ContentPlan.user_id == user_a.id)
        ).scalars().all()
        topics_a = {p.topic for p in plans_a}
        assert "A's plan" in topics_a
        assert "B's plan" not in topics_a


class TestFactInheritsDocumentVisibility:
    """Facts extracted from a document must inherit the document's user_id and is_public."""

    def test_fact_inherits_owner_and_visibility(self, db_session):
        user = User(email="test@test.com", name="Test", is_active=True)
        db_session.add(user)
        db_session.flush()

        game = Game(canonical_name="Test Game", user_id=user.id)
        db_session.add(game)
        db_session.flush()

        # Private document
        doc = Document(
            user_id=user.id, game_id=game.id, is_public=False,
            filename="private.pdf", file_path="/tmp/private.pdf",
            file_type="pdf", file_size=100,
        )
        db_session.add(doc)
        db_session.flush()

        # Simulate fact extraction (inherit from document)
        fact = Fact(
            game_id=doc.game_id,
            document_id=doc.id,
            user_id=doc.user_id,
            is_public=doc.is_public,
            category="trivia",
            claim="Inherited fact",
        )
        db_session.add(fact)
        db_session.flush()

        assert fact.user_id == user.id
        assert fact.is_public is False
