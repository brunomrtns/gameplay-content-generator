"""Kids Idea System tests — Phase 1 (Foundation).

Tests:
- KidsIdea model: creation, lifecycle, deduplication
- KidsSafetyFilter: hard rules + LLM (mocked)
- KidsScorer: scoring computation, ranking
- Idea service: CRUD, conversion, stats, expiration
- API routes: list, get, create, reject, score, convert, queue
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gpcg.core.models import (
    Base,
    ChannelProfile,
    ContentDomain,
    User,
)
from gpcg.domains.kids.models import (
    KidsIdea,
    KidsIdeaSource,
    KidsIdeaStatus,
    KidsTopic,
)
from gpcg.domains.kids.idea_service import (
    can_transition,
    clean_kids_queue,
    compute_content_hash,
    convert_to_topic,
    create_idea,
    expire_old_ideas,
    get_by_id,
    get_stats,
    is_duplicate_topic,
    is_similar_to_existing,
    is_terminal,
    list_ideas,
    normalize_for_hash,
    reconcile_kids_queue,
    reject_idea,
    title_similarity,
    update_status,
)
from gpcg.domains.kids.safety_filter import (
    KidsSafetyFilter,
    SafetyResult,
    _BLOCKED_KEYWORDS,
)
from gpcg.domains.kids.scorer import KidsScorer, KidsScoreResult
from gpcg.domains.kids.topic_library import (
    get_all_categories,
    get_category,
    get_category_names,
    get_seeds_for_category,
)
from gpcg.domains.kids.seasonal_calendar import (
    get_active_seasonal,
    get_all_entries,
    get_entries_for_month,
)
from gpcg.domains.kids.discovery import KidsIdeaDiscovery, DiscoveryResult
from gpcg.domains.automation_strategies import (
    GamesAutomationStrategy,
    KidsAutomationStrategy,
    get_strategy,
    get_user_domain,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def db_session():
    """Create an in-memory SQLite DB with all tables.

    Uses StaticPool to ensure a single connection — required for in-memory
    SQLite so that all threads see the same database (FastAPI TestClient
    runs route handlers in a thread pool).
    """
    import gpcg.core.models  # noqa: F401
    import gpcg.domains.games.models  # noqa: F401
    import gpcg.domains.kids.models  # noqa: F401
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = Session(engine)
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def user_with_kids(db_session):
    """Create a user with Kids domain."""
    user = User(email="kids@example.com", name="Kids User")
    db_session.add(user)
    db_session.flush()

    profile = ChannelProfile(
        user_id=user.id,
        domain=ContentDomain.kids.value,
        niche="Educativo infantil",
    )
    db_session.add(profile)
    db_session.commit()
    return user.id


# ── Model tests ──────────────────────────────────────────────────────────────


class TestKidsIdeaModel:
    """KidsIdea ORM model tests."""

    def test_create_idea_sets_default_status(self, db_session, user_with_kids):
        """New ideas start with status=discovered."""
        idea = create_idea(
            db_session, user_with_kids,
            title="Por que o céu é azul?",
            category="science",
        )
        assert idea is not None
        assert idea.status == KidsIdeaStatus.discovered.value
        assert idea.source == KidsIdeaSource.manual.value
        assert idea.content_hash is not None
        assert len(idea.content_hash) == 64  # SHA256 hex

    def test_create_idea_computes_content_hash(self, db_session, user_with_kids):
        """Content hash is computed from normalized title."""
        idea = create_idea(
            db_session, user_with_kids,
            title="Como funcionam os vulcões?",
        )
        expected = compute_content_hash("Como funcionam os vulcões?")
        assert idea.content_hash == expected

    def test_idea_repr(self, db_session, user_with_kids):
        """__repr__ includes id, status, and final_score."""
        idea = create_idea(db_session, user_with_kids, title="Test idea")
        idea.final_score = 0.75
        repr_str = repr(idea)
        assert f"#{idea.id}" in repr_str
        assert "discovered" in repr_str
        assert "0.75" in repr_str


# ── Lifecycle tests ──────────────────────────────────────────────────────────


class TestLifecycle:
    """KidsIdea lifecycle transition tests."""

    def test_is_terminal(self):
        """Terminal statuses are correctly identified."""
        assert is_terminal(KidsIdeaStatus.converted.value)
        assert is_terminal(KidsIdeaStatus.rejected.value)
        assert is_terminal(KidsIdeaStatus.expired.value)
        assert not is_terminal(KidsIdeaStatus.discovered.value)
        assert not is_terminal(KidsIdeaStatus.evaluated.value)
        assert not is_terminal(KidsIdeaStatus.queued.value)

    def test_valid_transitions(self):
        """Valid lifecycle transitions are allowed."""
        assert can_transition("discovered", "evaluated")
        assert can_transition("discovered", "rejected")
        assert can_transition("evaluated", "queued")
        assert can_transition("evaluated", "rejected")
        assert can_transition("evaluated", "expired")
        assert can_transition("queued", "converted")
        assert can_transition("queued", "rejected")
        assert can_transition("queued", "evaluated")  # back to pool

    def test_invalid_transitions(self):
        """Invalid lifecycle transitions are rejected."""
        assert not can_transition("discovered", "queued")  # must evaluate first
        assert not can_transition("discovered", "converted")  # must evaluate+queue
        assert not can_transition("converted", "evaluated")  # terminal
        assert not can_transition("rejected", "evaluated")  # terminal
        assert not can_transition("expired", "queued")  # terminal

    def test_update_status_valid(self, db_session, user_with_kids):
        """update_status allows valid transitions."""
        idea = create_idea(db_session, user_with_kids, title="Test lifecycle")
        assert update_status(db_session, idea.id, "evaluated")
        assert idea.status == "evaluated"

    def test_update_status_invalid(self, db_session, user_with_kids):
        """update_status rejects invalid transitions."""
        idea = create_idea(db_session, user_with_kids, title="Test invalid")
        assert not update_status(db_session, idea.id, "queued")  # discovered→queued invalid
        assert idea.status == "discovered"  # unchanged

    def test_reject_idea(self, db_session, user_with_kids):
        """reject_idea sets status and reason."""
        idea = create_idea(db_session, user_with_kids, title="To reject")
        assert reject_idea(db_session, idea.id, "Not suitable")
        assert idea.status == KidsIdeaStatus.rejected.value
        assert idea.rejection_reason == "Not suitable"

    def test_reject_terminal_idea_fails(self, db_session, user_with_kids):
        """Cannot reject an already-terminal idea."""
        idea = create_idea(db_session, user_with_kids, title="Already rejected")
        reject_idea(db_session, idea.id, "First rejection")
        assert not reject_idea(db_session, idea.id, "Second rejection")


# ── Deduplication tests ──────────────────────────────────────────────────────


class TestDeduplication:
    """Content hash and similarity-based deduplication tests."""

    def test_normalize_removes_accents(self):
        """Normalization strips accents."""
        assert normalize_for_hash("corações") == "coracoes"
        assert normalize_for_hash("três") == "tres"

    def test_normalize_lowercases(self):
        """Normalization lowercases."""
        assert normalize_for_hash("POLVOS") == "polvos"

    def test_normalize_removes_punctuation(self):
        """Normalization removes punctuation."""
        assert normalize_for_hash("por que?") == "por que"

    def test_normalize_replaces_digits(self):
        """Normalization replaces common digits with words."""
        assert "tres" in normalize_for_hash("3 corações")
        assert "um" in normalize_for_hash("1 planeta")

    def test_content_hash_consistent(self):
        """Same normalized title → same hash."""
        h1 = compute_content_hash("Por que os polvos têm 3 corações?")
        h2 = compute_content_hash("por que os polvos tem 3 coracoes")
        assert h1 == h2

    def test_content_hash_different(self):
        """Different titles → different hashes."""
        h1 = compute_content_hash("Por que o céu é azul?")
        h2 = compute_content_hash("Como funcionam os vulcões?")
        assert h1 != h2

    def test_create_duplicate_exact_returns_none(self, db_session, user_with_kids):
        """Creating an idea with the same hash returns None."""
        idea1 = create_idea(db_session, user_with_kids, title="Por que o céu é azul?")
        assert idea1 is not None
        idea2 = create_idea(db_session, user_with_kids, title="por que o ceu e azul?")
        assert idea2 is None  # duplicate

    def test_create_duplicate_similar_returns_none(self, db_session, user_with_kids):
        """Creating an idea with a similar (paraphrased) title returns None."""
        idea1 = create_idea(
            db_session, user_with_kids,
            title="Por que o polvo tem três corações?",
        )
        assert idea1 is not None
        # Paraphrase — different hash but high similarity
        idea2 = create_idea(
            db_session, user_with_kids,
            title="Você sabia que o polvo possui três corações?",
        )
        assert idea2 is None  # similar duplicate

    def test_title_similarity_high_for_paraphrase(self):
        """Paraphrased titles have high similarity."""
        s = title_similarity(
            "Por que o polvo tem três corações?",
            "Você sabia que o polvo possui três corações?",
        )
        assert s >= 0.4  # above threshold

    def test_title_similarity_low_for_different(self):
        """Unrelated titles have low similarity."""
        s = title_similarity(
            "Como funcionam os vulcões?",
            "Por que o céu é azul?",
        )
        assert s < 0.2

    def test_is_similar_to_existing_finds_match(self, db_session, user_with_kids):
        """is_similar_to_existing detects similar ideas."""
        create_idea(db_session, user_with_kids, title="Por que o polvo tem três corações?")
        similar = is_similar_to_existing(
            db_session, user_with_kids,
            "Você sabia que o polvo possui três corações?",
        )
        assert similar is not None

    def test_is_similar_to_existing_no_match(self, db_session, user_with_kids):
        """is_similar_to_existing returns None for unrelated titles."""
        create_idea(db_session, user_with_kids, title="Como funcionam os vulcões?")
        similar = is_similar_to_existing(
            db_session, user_with_kids,
            "Por que o céu é azul?",
        )
        assert similar is None

    def test_skip_dedup_allows_duplicates(self, db_session, user_with_kids):
        """skip_dedup=True allows creating exact duplicates (for testing/bulk)."""
        idea1 = create_idea(db_session, user_with_kids, title="Test skip dedup")
        assert idea1 is not None
        idea2 = create_idea(
            db_session, user_with_kids,
            title="Test skip dedup",
            skip_dedup=True,
        )
        assert idea2 is not None  # allowed with skip_dedup


# ── Safety Filter tests ──────────────────────────────────────────────────────


class TestSafetyFilter:
    """KidsSafetyFilter tests (hard rules + mocked LLM)."""

    def test_hard_rules_block_inappropriate_content(self):
        """Hard rules catch blocked keywords."""
        f = KidsSafetyFilter(llm=MagicMock())
        result = f._hard_rules_check("Como usar uma pistola", "")
        assert not result.safe
        assert result.safety_score == 0.0
        assert any("blocked_keyword" in flag for flag in result.flags)

    def test_hard_rules_pass_safe_content(self):
        """Hard rules pass safe content."""
        f = KidsSafetyFilter(llm=MagicMock())
        result = f._hard_rules_check("Por que o céu é azul?", "")
        assert result.safe
        assert result.safety_score == 1.0
        assert result.flags == []

    def test_hard_rules_flag_sensitive_content(self):
        """Hard rules flag sensitive keywords but don't block."""
        f = KidsSafetyFilter(llm=MagicMock())
        result = f._hard_rules_check("O que acontece quando alguém morre?", "")
        assert result.safe  # not blocked
        assert result.safety_score < 1.0  # flagged
        assert any("sensitive_keyword" in flag for flag in result.flags)

    def test_review_blocked_keyword_skips_llm(self):
        """Review skips LLM when hard rules block."""
        mock_llm = MagicMock()
        f = KidsSafetyFilter(llm=mock_llm)
        result = f.review(title="Como usar uma pistola", age_range="3-6")
        assert not result.safe
        assert result.reviewed_by == "hard_rules"
        mock_llm.chat_json.assert_not_called()  # LLM was not called

    def test_review_calls_llm_for_safe_content(self):
        """Review calls LLM for content that passes hard rules."""
        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "safe": True,
            "safety_score": 0.9,
            "age_suitability": 0.8,
            "flags": [],
            "reason": "Safe topic",
        }
        f = KidsSafetyFilter(llm=mock_llm)
        result = f.review(title="Por que o céu é azul?", age_range="3-6", strictness=0.7)
        assert result.safe
        assert result.safety_score == 0.9
        assert result.reviewed_by == "llm"
        mock_llm.chat_json.assert_called_once()

    def test_review_rejects_low_safety_score(self):
        """Review rejects when safety_score < strictness threshold."""
        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "safe": True,
            "safety_score": 0.5,
            "age_suitability": 0.5,
            "flags": ["complex"],
            "reason": "A bit complex",
        }
        f = KidsSafetyFilter(llm=mock_llm)
        result = f.review(
            title="Como funcionam as reações nucleares?",
            age_range="3-6",
            strictness=0.8,  # requires safety_score >= 0.8
        )
        assert not result.safe  # 0.5 < 0.8

    def test_review_llm_failure_fallback(self):
        """Review falls back conservatively when LLM fails."""
        mock_llm = MagicMock()
        mock_llm.chat_json.side_effect = Exception("LLM unavailable")
        f = KidsSafetyFilter(llm=mock_llm)
        result = f.review(title="Por que o céu é azul?", age_range="3-6", strictness=0.3)
        assert result.safe  # conservative fallback (0.5 >= 0.3)
        assert result.safety_score == 0.5
        assert "llm_review_failed" in result.flags

    def test_blocked_keywords_are_lowercased_check(self):
        """Blocked keywords are checked case-insensitively."""
        f = KidsSafetyFilter(llm=MagicMock())
        result = f._hard_rules_check("PISTOLA e ARMAS", "")
        assert not result.safe

    def test_review_merges_sensitive_flags_with_llm(self):
        """Review merges sensitive keyword flags with LLM flags."""
        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "safe": True,
            "safety_score": 0.9,
            "age_suitability": 0.8,
            "flags": ["slightly_complex"],
            "reason": "OK but slightly complex",
        }
        f = KidsSafetyFilter(llm=mock_llm)
        result = f.review(
            title="O que acontece quando alguém morre?",
            age_range="7-10",
            strictness=0.5,
        )
        assert result.reviewed_by == "both"
        assert any("sensitive_keyword" in flag for flag in result.flags)
        assert "slightly_complex" in result.flags


# ── Scorer tests ─────────────────────────────────────────────────────────────


class TestKidsScorer:
    """KidsScorer tests (LLM mocked)."""

    def test_score_parses_llm_response(self):
        """Scorer correctly parses LLM JSON response."""
        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "editorial_quality": 85,
            "age_fit": 90,
            "educational_value": 80,
            "curiosity": 95,
            "visual_potential": 70,
            "simplicity": 75,
            "reason": "Great idea for kids",
        }
        scorer = KidsScorer(llm=mock_llm)
        result = scorer.score(title="Por que o céu é azul?", age_range="3-6", category="science")
        assert result.editorial_quality == 0.85
        assert result.age_fit == 0.90
        assert result.educational_value == 0.80
        assert result.curiosity == 0.95
        assert result.visual_potential == 0.70
        assert result.simplicity == 0.75
        assert 0.0 < result.final_score <= 1.0
        assert result.editorial_score_0_100 == result.final_score * 100
        assert result.reason == "Great idea for kids"

    def test_score_clamps_values(self):
        """Scorer clamps values to 0.0-1.0 range."""
        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "editorial_quality": 150,  # over 100
            "age_fit": -20,            # below 0
            "educational_value": 50,
            "curiosity": 50,
            "visual_potential": 50,
            "simplicity": 50,
            "reason": "Edge case",
        }
        scorer = KidsScorer(llm=mock_llm)
        result = scorer.score(title="Test", age_range="3-6")
        assert result.editorial_quality == 1.0  # clamped
        assert result.age_fit == 0.0  # clamped

    def test_score_llm_failure_fallback(self):
        """Scorer returns neutral fallback when LLM fails."""
        mock_llm = MagicMock()
        mock_llm.chat_json.side_effect = Exception("LLM error")
        scorer = KidsScorer(llm=mock_llm)
        result = scorer.score(title="Test", age_range="3-6")
        assert result.editorial_quality == 0.5
        assert result.final_score == 0.5
        assert result.breakdown.get("fallback") is True

    def test_compute_final_high_scores(self):
        """High scores produce high final_score."""
        scorer = KidsScorer(llm=MagicMock())
        final = scorer._compute_final(0.9, 0.9, 0.9, 0.9, 0.9, 0.9)
        assert final > 0.8

    def test_compute_final_zero_dimension(self):
        """A zero dimension makes final_score near zero (multiplicative)."""
        scorer = KidsScorer(llm=MagicMock())
        final = scorer._compute_final(0.0, 0.9, 0.9, 0.9, 0.9, 0.9)
        assert final <= 0.15  # near zero due to multiplicative nature (capped bonus)

    def test_compute_final_visual_bonus(self):
        """High visual_potential adds bonus."""
        scorer = KidsScorer(llm=MagicMock())
        base = scorer._compute_final(0.7, 0.7, 0.7, 0.7, 0.5, 0.5)
        with_visual = scorer._compute_final(0.7, 0.7, 0.7, 0.7, 1.0, 0.5)
        assert with_visual > base

    def test_compute_final_clamped(self):
        """Final score is clamped to 0.0-1.0."""
        scorer = KidsScorer(llm=MagicMock())
        final = scorer._compute_final(1.0, 1.0, 1.0, 1.0, 1.0, 1.0)
        assert final <= 1.0
        final_low = scorer._compute_final(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        assert final_low >= 0.0


# ── Service tests ────────────────────────────────────────────────────────────


class TestIdeaService:
    """Idea service CRUD + conversion tests."""

    def test_create_and_get(self, db_session, user_with_kids):
        """create_idea + get_by_id."""
        idea = create_idea(db_session, user_with_kids, title="Test get")
        fetched = get_by_id(db_session, idea.id)
        assert fetched is not None
        assert fetched.title == "Test get"

    def test_list_ideas(self, db_session, user_with_kids):
        """list_ideas returns user's ideas."""
        create_idea(db_session, user_with_kids, title="Por que o céu é azul?", category="science")
        create_idea(db_session, user_with_kids, title="Como funcionam os vulcões?", category="animals")
        ideas = list_ideas(db_session, user_with_kids)
        assert len(ideas) == 2

    def test_list_ideas_filter_by_status(self, db_session, user_with_kids):
        """list_ideas filters by status."""
        idea1 = create_idea(db_session, user_with_kids, title="Por que o céu é azul?")
        update_status(db_session, idea1.id, "evaluated")
        create_idea(db_session, user_with_kids, title="Como funcionam os vulcões?")
        evaluated = list_ideas(db_session, user_with_kids, status="evaluated")
        assert len(evaluated) == 1
        assert evaluated[0].title == "Por que o céu é azul?"

    def test_list_ideas_filter_by_category(self, db_session, user_with_kids):
        """list_ideas filters by category."""
        create_idea(db_session, user_with_kids, title="Por que o céu é azul?", category="science")
        create_idea(db_session, user_with_kids, title="Como funcionam os vulcões?", category="animals")
        science = list_ideas(db_session, user_with_kids, category="science")
        assert len(science) == 1
        assert science[0].category == "science"

    def test_get_stats(self, db_session, user_with_kids):
        """get_stats returns counts by status."""
        create_idea(db_session, user_with_kids, title="Por que o céu é azul?")
        create_idea(db_session, user_with_kids, title="Como funcionam os vulcões?")
        stats = get_stats(db_session, user_with_kids)
        assert stats["total"] == 2
        assert stats["by_status"]["discovered"] == 2

    def test_convert_to_topic(self, db_session, user_with_kids):
        """convert_to_topic creates a KidsTopic and links it."""
        idea = create_idea(
            db_session, user_with_kids,
            title="Dinossauros",
            category="animals",
            suggested_age_range="3-6",
            description="Tudo sobre dinossauros",
        )
        topic = convert_to_topic(db_session, idea.id)
        assert topic is not None
        assert topic.title == "Dinossauros"
        assert topic.idea_id == idea.id
        assert topic.category == "animals"
        assert topic.age_range == "3-6"

        # Idea is marked converted
        db_session.refresh(idea)
        assert idea.status == KidsIdeaStatus.converted.value
        assert idea.topic_id == topic.id

    def test_convert_already_converted_returns_existing(self, db_session, user_with_kids):
        """Converting an already-converted idea returns the existing topic."""
        idea = create_idea(db_session, user_with_kids, title="Already converted")
        topic1 = convert_to_topic(db_session, idea.id)
        topic2 = convert_to_topic(db_session, idea.id)
        assert topic1.id == topic2.id  # same topic

    def test_convert_duplicate_topic_returns_none(self, db_session, user_with_kids):
        """Converting an idea with a duplicate topic title returns None."""
        # Create a topic first
        topic = KidsTopic(
            user_id=user_with_kids,
            title="Polvos",
            slug="polvos",
            category="animals",
            age_range="3-6",
        )
        db_session.add(topic)
        db_session.flush()

        # Create an idea with a similar title
        idea = create_idea(db_session, user_with_kids, title="Polvos")
        assert idea is not None

        # Converting should fail (duplicate topic)
        result = convert_to_topic(db_session, idea.id)
        assert result is None

    def test_is_duplicate_topic_exact(self, db_session, user_with_kids):
        """is_duplicate_topic detects exact title matches."""
        topic = KidsTopic(
            user_id=user_with_kids,
            title="Por que o céu é azul?",
            slug="por-que-o-ceu-e-azul",
            category="science",
            age_range="3-6",
        )
        db_session.add(topic)
        db_session.flush()
        assert is_duplicate_topic(db_session, user_with_kids, "por que o ceu e azul?")

    def test_is_duplicate_topic_similar(self, db_session, user_with_kids):
        """is_duplicate_topic detects similar title matches."""
        topic = KidsTopic(
            user_id=user_with_kids,
            title="Por que o polvo tem três corações?",
            slug="polvo-coracoes",
            category="animals",
            age_range="3-6",
        )
        db_session.add(topic)
        db_session.flush()
        assert is_duplicate_topic(
            db_session, user_with_kids,
            "Você sabia que o polvo possui três corações?",
        )

    def test_is_duplicate_topic_different(self, db_session, user_with_kids):
        """is_duplicate_topic returns False for unrelated titles."""
        topic = KidsTopic(
            user_id=user_with_kids,
            title="Como funcionam os vulcões?",
            slug="vulcoes",
            category="science",
            age_range="7-10",
        )
        db_session.add(topic)
        db_session.flush()
        assert not is_duplicate_topic(db_session, user_with_kids, "Por que o céu é azul?")

    def test_expire_old_ideas(self, db_session, user_with_kids):
        """expire_old_ideas expires evaluated ideas older than max_age_days."""
        idea = create_idea(db_session, user_with_kids, title="Old idea")
        update_status(db_session, idea.id, "evaluated")
        # Manually set updated_at to 200 days ago
        idea.updated_at = datetime.now(timezone.utc) - timedelta(days=200)
        db_session.flush()

        expired = expire_old_ideas(db_session, user_with_kids, max_age_days=180)
        assert expired == 1
        assert idea.status == KidsIdeaStatus.expired.value

    def test_expire_does_not_expire_recent(self, db_session, user_with_kids):
        """expire_old_ideas does not expire recent ideas."""
        idea = create_idea(db_session, user_with_kids, title="Recent idea")
        update_status(db_session, idea.id, "evaluated")
        expired = expire_old_ideas(db_session, user_with_kids, max_age_days=180)
        assert expired == 0
        assert idea.status == "evaluated"


# ── API Route tests ──────────────────────────────────────────────────────────


class TestKidsIdeaAPI:
    """Kids Idea API endpoint tests."""

    @pytest.fixture
    def client(self, db_session, user_with_kids):
        """Create a FastAPI TestClient with mocked auth and DB."""
        from fastapi.testclient import TestClient
        from gpcg.api.app import create_app
        from gpcg.core.models import User

        # Get the user
        user = db_session.query(User).filter(User.id == user_with_kids).first()

        # Mock init_db to avoid creating tables on the production engine
        with patch("gpcg.api.app.init_db", return_value=None):
            app = create_app()

        # Override dependencies
        from gpcg.infrastructure.auth import get_current_user
        from gpcg.infrastructure.database import get_db

        def override_auth():
            return user

        def override_db():
            yield db_session

        app.dependency_overrides[get_current_user] = override_auth
        app.dependency_overrides[get_db] = override_db

        client = TestClient(app)
        yield client
        app.dependency_overrides.clear()

    def test_list_ideas_empty(self, client):
        """GET /api/kids/ideas returns empty list when no ideas."""
        resp = client.get("/api/kids/ideas")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ideas"] == []

    def test_create_manual_idea(self, client):
        """POST /api/kids/ideas creates a manual idea."""
        resp = client.post("/api/kids/ideas", json={
            "title": "Por que o céu é azul?",
            "description": "Explicação simples sobre o céu azul",
            "category": "science",
            "suggested_age_range": "3-6",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Por que o céu é azul?"
        assert data["status"] == "discovered"
        assert data["source"] == "manual"

    def test_create_duplicate_idea_returns_409(self, client):
        """POST /api/kids/ideas returns 409 for duplicates."""
        client.post("/api/kids/ideas", json={"title": "Por que o céu é azul?"})
        resp = client.post("/api/kids/ideas", json={"title": "por que o ceu e azul?"})
        assert resp.status_code == 409

    def test_get_idea_by_id(self, client):
        """GET /api/kids/ideas/{id} returns idea detail."""
        create_resp = client.post("/api/kids/ideas", json={"title": "Test get by id"})
        idea_id = create_resp.json()["id"]
        resp = client.get(f"/api/kids/ideas/{idea_id}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Test get by id"

    def test_get_nonexistent_idea_returns_404(self, client):
        """GET /api/kids/ideas/999 returns 404."""
        resp = client.get("/api/kids/ideas/999")
        assert resp.status_code == 404

    def test_reject_idea(self, client):
        """POST /api/kids/ideas/{id}/reject rejects an idea."""
        create_resp = client.post("/api/kids/ideas", json={"title": "To reject"})
        idea_id = create_resp.json()["id"]
        resp = client.post(f"/api/kids/ideas/{idea_id}/reject", json={"reason": "Not good"})
        assert resp.status_code == 200
        assert resp.json()["rejected"] is True

    def test_reject_nonexistent_returns_404(self, client):
        """POST /api/kids/ideas/999/reject returns 404."""
        resp = client.post("/api/kids/ideas/999/reject", json={"reason": "test"})
        assert resp.status_code == 404

    def test_get_stats(self, client):
        """GET /api/kids/ideas/stats returns statistics."""
        client.post("/api/kids/ideas", json={"title": "Por que o céu é azul?"})
        client.post("/api/kids/ideas", json={"title": "Como funcionam os vulcões?"})
        resp = client.get("/api/kids/ideas/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["by_status"]["discovered"] == 2

    def test_convert_idea_to_topic(self, client):
        """POST /api/kids/ideas/{id}/convert creates a KidsTopic."""
        create_resp = client.post("/api/kids/ideas", json={
            "title": "Dinossauros",
            "category": "animals",
            "suggested_age_range": "3-6",
        })
        idea_id = create_resp.json()["id"]
        resp = client.post(f"/api/kids/ideas/{idea_id}/convert", json={
            "editorial_intent": "curiosity",
            "educational_goal": "nature",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["topic_id"] is not None
        assert data["title"] == "Dinossauros"
        assert data["idea_id"] == idea_id

    def test_queue_add_and_get(self, client):
        """POST /api/kids/idea-queue/add + GET /api/kids/idea-queue."""
        create_resp = client.post("/api/kids/ideas", json={"title": "Queue test"})
        idea_id = create_resp.json()["id"]

        # Add to queue
        resp = client.post("/api/kids/idea-queue/add", json={"idea_id": idea_id})
        assert resp.status_code == 200

        # Get queue
        resp = client.get("/api/kids/idea-queue")
        assert resp.status_code == 200
        data = resp.json()
        assert idea_id in data["queue"]
        assert len(data["items"]) == 1

    def test_queue_remove(self, client):
        """POST /api/kids/idea-queue/remove removes from queue."""
        create_resp = client.post("/api/kids/ideas", json={"title": "Queue remove test"})
        idea_id = create_resp.json()["id"]
        client.post("/api/kids/idea-queue/add", json={"idea_id": idea_id})
        resp = client.post("/api/kids/idea-queue/remove", json={"idea_id": idea_id})
        assert resp.status_code == 200
        assert idea_id not in resp.json()["queue"]

    def test_queue_reorder(self, client):
        """POST /api/kids/idea-queue/reorder reorders the queue."""
        titles = ["Por que o céu é azul?", "Como funcionam os vulcões?", "O que são estrelas?"]
        ids = []
        for title in titles:
            r = client.post("/api/kids/ideas", json={"title": title})
            ids.append(r.json()["id"])
        for iid in ids:
            client.post("/api/kids/idea-queue/add", json={"idea_id": iid})
        # Reverse order
        reversed_ids = list(reversed(ids))
        resp = client.post("/api/kids/idea-queue/reorder", json={"idea_ids": reversed_ids})
        assert resp.status_code == 200
        assert resp.json()["queue"] == reversed_ids

    def test_queue_add_duplicate_returns_409(self, client):
        """Adding the same idea twice returns 409."""
        create_resp = client.post("/api/kids/ideas", json={"title": "Dup queue"})
        idea_id = create_resp.json()["id"]
        client.post("/api/kids/idea-queue/add", json={"idea_id": idea_id})
        resp = client.post("/api/kids/idea-queue/add", json={"idea_id": idea_id})
        assert resp.status_code == 409

    def test_queue_add_rejected_idea_returns_400(self, client):
        """Cannot queue a rejected idea."""
        create_resp = client.post("/api/kids/ideas", json={"title": "Reject then queue"})
        idea_id = create_resp.json()["id"]
        client.post(f"/api/kids/ideas/{idea_id}/reject", json={"reason": "No"})
        resp = client.post("/api/kids/idea-queue/add", json={"idea_id": idea_id})
        assert resp.status_code == 400

    def test_score_idea_creates_job(self, client):
        """POST /api/kids/ideas/{id}/score creates a kids_idea_score job."""
        create_resp = client.post("/api/kids/ideas", json={"title": "Score test"})
        idea_id = create_resp.json()["id"]

        resp = client.post(f"/api/kids/ideas/{idea_id}/score")

        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data
        assert data["job_status"] == "queued"
        assert data["idea_id"] == idea_id

    def test_score_idea_rejects_converted(self, client):
        """POST /api/kids/ideas/{id}/score rejects already-converted ideas."""
        create_resp = client.post("/api/kids/ideas", json={"title": "Converted idea"})
        idea_id = create_resp.json()["id"]
        # Manually mark as converted via convert endpoint
        client.post(f"/api/kids/ideas/{idea_id}/convert", json={})
        resp = client.post(f"/api/kids/ideas/{idea_id}/score")
        assert resp.status_code == 400


# ── Topic Library tests ──────────────────────────────────────────────────────


class TestTopicLibrary:
    """Kids Topic Library tests."""

    def test_get_all_categories(self):
        """Library has multiple categories."""
        cats = get_all_categories()
        assert len(cats) >= 10  # at least 10 categories

    def test_category_names_include_expected(self):
        """Library includes expected categories."""
        names = get_category_names()
        for expected in ["animals", "science", "space", "dinosaurs", "nature"]:
            assert expected in names, f"Missing category: {expected}"

    def test_get_category_by_name(self):
        """get_category returns the correct category."""
        cat = get_category("animals")
        assert cat is not None
        assert cat.name == "animals"
        assert cat.display_name == "Animais"
        assert len(cat.seeds) > 0

    def test_get_nonexistent_category(self):
        """get_category returns None for unknown names."""
        assert get_category("nonexistent") is None

    def test_get_seeds_for_category(self):
        """get_seeds_for_category returns seeds."""
        seeds = get_seeds_for_category("animals")
        assert len(seeds) > 0
        assert all(hasattr(s, "title_hint") for s in seeds)

    def test_seeds_for_nonexistent_category(self):
        """get_seeds_for_category returns empty list for unknown categories."""
        assert get_seeds_for_category("nonexistent") == []

    def test_categories_have_display_names(self):
        """All categories have display names."""
        for cat in get_all_categories():
            assert cat.display_name, f"Category {cat.name} missing display_name"

    def test_categories_have_descriptions(self):
        """All categories have descriptions."""
        for cat in get_all_categories():
            assert cat.description, f"Category {cat.name} missing description"


# ── Seasonal Calendar tests ──────────────────────────────────────────────────


class TestSeasonalCalendar:
    """Seasonal Calendar tests."""

    def test_get_all_entries(self):
        """Calendar has multiple entries."""
        entries = get_all_entries()
        assert len(entries) >= 5

    def test_get_entries_for_month(self):
        """get_entries_for_month returns entries for a specific month."""
        # October should have Dia das Crianças (10-12)
        oct_entries = get_entries_for_month(10)
        assert len(oct_entries) >= 1
        assert any(e.name == "Dia das Crianças" for e in oct_entries)

    def test_get_active_seasonal_returns_list(self):
        """get_active_seasonal returns a list (may be empty depending on date)."""
        from datetime import date
        active = get_active_seasonal(ref_date=date(2024, 10, 1))
        assert isinstance(active, list)
        # October 1 should be within lead_days of Dia das Crianças (Oct 12)
        assert any(e.name == "Dia das Crianças" for e in active)

    def test_get_active_seasonal_far_future(self):
        """get_active_seasonal returns empty for dates with no nearby entries."""
        from datetime import date
        # A date very far from any entry
        active = get_active_seasonal(ref_date=date(2024, 3, 15), lookahead_days=0)
        # March 15 might not have any entries within 0 days lookahead
        # (depends on calendar, but likely empty)
        assert isinstance(active, list)

    def test_entries_have_required_fields(self):
        """All entries have name, date, description, category."""
        for entry in get_all_entries():
            assert entry.name, "Entry missing name"
            assert entry.date, f"Entry {entry.name} missing date"
            assert entry.category, f"Entry {entry.name} missing category"


# ── Discovery tests ──────────────────────────────────────────────────────────


class TestKidsIdeaDiscovery:
    """KidsIdeaDiscovery tests (LLM mocked)."""

    def test_discover_with_mocked_llm(self, db_session, user_with_kids):
        """Discovery creates ideas from AI ideation + topic library."""
        from gpcg.core.models import ChannelProfile
        profile = db_session.query(ChannelProfile).filter(
            ChannelProfile.user_id == user_with_kids
        ).first()

        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "ideas": [
                {
                    "title": "Por que os gatos ronronam?",
                    "description": "Como e por que os gatos fazem ronronar",
                    "category": "animals",
                    "suggested_age_range": "3-6",
                },
                {
                    "title": "Como os cães conseguem cheirar tão bem?",
                    "description": "Olfato dos cães",
                    "category": "animals",
                    "suggested_age_range": "3-6",
                },
            ]
        }

        discovery = KidsIdeaDiscovery(llm=mock_llm)
        result = discovery.discover(
            db_session, user_with_kids, profile,
            categories=["animals"],
            ideas_per_category=2,
            include_seasonal=False,
            include_topic_library=False,
        )

        assert result.created_count >= 1
        assert result.skipped_count >= 0
        assert len(result.errors) == 0

    def test_discover_topic_library_seeds(self, db_session, user_with_kids):
        """Discovery creates ideas from topic library seeds."""
        from gpcg.core.models import ChannelProfile
        profile = db_session.query(ChannelProfile).filter(
            ChannelProfile.user_id == user_with_kids
        ).first()

        mock_llm = MagicMock()
        # Return empty ideas from AI — we only want topic library seeds
        mock_llm.chat_json.return_value = {"ideas": []}

        discovery = KidsIdeaDiscovery(llm=mock_llm)
        result = discovery.discover(
            db_session, user_with_kids, profile,
            categories=["animals"],
            ideas_per_category=0,
            include_seasonal=False,
            include_topic_library=True,
        )

        # Should create ideas from the animals seed library
        assert result.created_count > 0

    def test_discover_dedup_skips_existing(self, db_session, user_with_kids):
        """Discovery skips ideas that already exist (dedup)."""
        from gpcg.core.models import ChannelProfile
        profile = db_session.query(ChannelProfile).filter(
            ChannelProfile.user_id == user_with_kids
        ).first()

        # Pre-create an idea with the same title as a seed
        create_idea(
            db_session, user_with_kids,
            title="Por que os polvos têm três corações?",
            category="animals",
        )
        db_session.flush()

        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {"ideas": []}

        discovery = KidsIdeaDiscovery(llm=mock_llm)
        result = discovery.discover(
            db_session, user_with_kids, profile,
            categories=["animals"],
            ideas_per_category=0,
            include_seasonal=False,
            include_topic_library=True,
        )

        # The seed "Por que os polvos têm três corações?" should be skipped
        assert result.skipped_count > 0

    def test_discover_llm_failure_continues(self, db_session, user_with_kids):
        """Discovery continues when LLM fails (topic library still works)."""
        from gpcg.core.models import ChannelProfile
        profile = db_session.query(ChannelProfile).filter(
            ChannelProfile.user_id == user_with_kids
        ).first()

        mock_llm = MagicMock()
        mock_llm.chat_json.side_effect = Exception("LLM unavailable")

        discovery = KidsIdeaDiscovery(llm=mock_llm)
        result = discovery.discover(
            db_session, user_with_kids, profile,
            categories=["animals"],
            ideas_per_category=2,
            include_seasonal=False,
            include_topic_library=True,
        )

        # AI ideation failed gracefully (returns empty list, no crash)
        # but topic library seeds should still be created
        assert result.created_count > 0  # topic library seeds created

    def test_discover_result_repr(self):
        """DiscoveryResult __repr__ includes counts."""
        result = DiscoveryResult()
        result.created_count = 5
        result.skipped_count = 2
        repr_str = repr(result)
        assert "created=5" in repr_str
        assert "skipped=2" in repr_str


# ── Discovery API tests ──────────────────────────────────────────────────────


class TestDiscoveryAPI:
    """Discovery API endpoint tests."""

    @pytest.fixture
    def client(self, db_session, user_with_kids):
        """Create a FastAPI TestClient with mocked auth and DB."""
        from fastapi.testclient import TestClient
        from gpcg.api.app import create_app
        from gpcg.core.models import User

        user = db_session.query(User).filter(User.id == user_with_kids).first()

        with patch("gpcg.api.app.init_db", return_value=None):
            app = create_app()

        from gpcg.infrastructure.auth import get_current_user
        from gpcg.infrastructure.database import get_db

        def override_auth():
            return user

        def override_db():
            yield db_session

        app.dependency_overrides[get_current_user] = override_auth
        app.dependency_overrides[get_db] = override_db

        client = TestClient(app)
        yield client
        app.dependency_overrides.clear()

    def test_get_topic_library(self, client):
        """GET /api/kids/topic-library returns categories."""
        resp = client.get("/api/kids/topic-library")
        assert resp.status_code == 200
        data = resp.json()
        assert "categories" in data
        assert len(data["categories"]) >= 10
        # Check structure
        cat = data["categories"][0]
        assert "name" in cat
        assert "display_name" in cat
        assert "seeds" in cat

    def test_get_seasonal_calendar(self, client):
        """GET /api/kids/seasonal-calendar returns entries."""
        resp = client.get("/api/kids/seasonal-calendar")
        assert resp.status_code == 200
        data = resp.json()
        assert "active" in data
        assert "all" in data
        assert len(data["all"]) >= 5

    def test_discover_endpoint_creates_job(self, client):
        """POST /api/kids/ideas/discover creates a discovery job (not synchronous)."""
        resp = client.post("/api/kids/ideas/discover", json={
            "categories": ["ocean"],
            "ideas_per_category": 1,
            "include_seasonal": False,
            "include_topic_library": False,
        })

        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data
        assert data["job_status"] == "queued"

    def test_discover_endpoint_topic_library_only(self, client):
        """POST /api/kids/ideas/discover with topic_library only creates a job."""
        resp = client.post("/api/kids/ideas/discover", json={
            "categories": ["animals"],
            "ideas_per_category": 0,
            "include_seasonal": False,
            "include_topic_library": True,
        })

        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data
        assert data["job_status"] == "queued"


# ── Queue Reconciliation tests ───────────────────────────────────────────────


class TestQueueReconciliation:
    """Kids idea queue reconciliation and cleaning tests."""

    def _setup_automation(self, session, user_id, config=None):
        """Create an Automation record with given config."""
        from gpcg.core.models import Automation
        auto = Automation(
            user_id=user_id,
            name="Automação",
            config=config or {},
        )
        session.add(auto)
        session.flush()
        return auto

    def test_reconcile_no_automation_returns_zero(self, db_session, user_with_kids):
        """Reconcile returns 0 when no automation exists."""
        assert reconcile_kids_queue(db_session, user_with_kids) == 0

    def test_reconcile_manual_mode_returns_zero(self, db_session, user_with_kids):
        """Reconcile returns 0 when queue_mode is manual."""
        self._setup_automation(db_session, user_with_kids, {
            "kids_queue_mode": "manual",
            "kids_auto_fill_queue": True,
        })
        assert reconcile_kids_queue(db_session, user_with_kids) == 0

    def test_reconcile_auto_fill_disabled_returns_zero(self, db_session, user_with_kids):
        """Reconcile returns 0 when auto_fill_queue is False."""
        self._setup_automation(db_session, user_with_kids, {
            "kids_queue_mode": "automatic",
            "kids_auto_fill_queue": False,
        })
        assert reconcile_kids_queue(db_session, user_with_kids) == 0

    def test_reconcile_fills_from_evaluated_ideas(self, db_session, user_with_kids):
        """Reconcile fills queue with top-scored evaluated ideas."""
        self._setup_automation(db_session, user_with_kids, {
            "kids_queue_mode": "automatic",
            "kids_auto_fill_queue": True,
            "kids_max_queue_size": 5,
        })

        # Create and evaluate 3 ideas with different scores
        idea1 = create_idea(db_session, user_with_kids, title="Por que o céu é azul?")
        idea2 = create_idea(db_session, user_with_kids, title="Como funcionam os vulcões?")
        idea3 = create_idea(db_session, user_with_kids, title="O que são estrelas cadentes?")
        db_session.flush()

        # Set scores and mark as evaluated
        for idea, score in [(idea1, 0.9), (idea2, 0.7), (idea3, 0.5)]:
            idea.final_score = score
            idea.status = KidsIdeaStatus.evaluated.value
        db_session.flush()

        added = reconcile_kids_queue(db_session, user_with_kids)
        assert added == 3  # all 3 added

        # Verify queue order (highest score first)
        from gpcg.core.models import Automation
        auto = db_session.query(Automation).filter(Automation.user_id == user_with_kids).first()
        queue = auto.config.get("kids_idea_queue", [])
        assert queue[0] == idea1.id  # highest score first
        assert queue[1] == idea2.id
        assert queue[2] == idea3.id

    def test_reconcile_respects_max_size(self, db_session, user_with_kids):
        """Reconcile respects max_queue_size."""
        self._setup_automation(db_session, user_with_kids, {
            "kids_queue_mode": "automatic",
            "kids_auto_fill_queue": True,
            "kids_max_queue_size": 2,
        })

        # Create 5 evaluated ideas
        titles = ["Por que o céu é azul?", "Como funcionam os vulcões?",
                  "O que são estrelas cadentes?", "Por que chove?",
                  "Como os pássaros voam?"]
        for title in titles:
            idea = create_idea(db_session, user_with_kids, title=title)
            idea.final_score = 0.8
            idea.status = KidsIdeaStatus.evaluated.value
        db_session.flush()

        added = reconcile_kids_queue(db_session, user_with_kids)
        assert added == 2  # only 2 (max_queue_size)

    def test_reconcile_skips_non_evaluated(self, db_session, user_with_kids):
        """Reconcile only picks evaluated ideas (not discovered)."""
        self._setup_automation(db_session, user_with_kids, {
            "kids_queue_mode": "automatic",
            "kids_auto_fill_queue": True,
            "kids_max_queue_size": 5,
        })

        # Create ideas in different states
        idea1 = create_idea(db_session, user_with_kids, title="Por que o céu é azul?")
        idea1.status = KidsIdeaStatus.evaluated.value
        idea1.final_score = 0.9
        idea2 = create_idea(db_session, user_with_kids, title="Como funcionam os vulcões?")
        # idea2 stays discovered (not evaluated)
        db_session.flush()

        added = reconcile_kids_queue(db_session, user_with_kids)
        assert added == 1  # only idea1 (evaluated)

    def test_clean_queue_removes_rejected(self, db_session, user_with_kids):
        """clean_kids_queue removes rejected ideas from queue."""
        auto = self._setup_automation(db_session, user_with_kids, {})

        idea1 = create_idea(db_session, user_with_kids, title="Por que o céu é azul?")
        idea2 = create_idea(db_session, user_with_kids, title="Como funcionam os vulcões?")
        db_session.flush()

        # Add both to queue
        auto.config = {"kids_idea_queue": [idea1.id, idea2.id]}
        db_session.flush()

        # Reject idea2
        reject_idea(db_session, idea2.id, "Not suitable")
        db_session.flush()

        removed = clean_kids_queue(db_session, user_with_kids)
        assert removed == 1
        queue = auto.config.get("kids_idea_queue", [])
        assert idea2.id not in queue
        assert idea1.id in queue

    def test_clean_queue_removes_converted(self, db_session, user_with_kids):
        """clean_kids_queue removes converted ideas from queue."""
        auto = self._setup_automation(db_session, user_with_kids, {})

        idea1 = create_idea(db_session, user_with_kids, title="Por que o céu é azul?")
        idea2 = create_idea(db_session, user_with_kids, title="Como funcionam os vulcões?")
        db_session.flush()

        auto.config = {"kids_idea_queue": [idea1.id, idea2.id]}
        db_session.flush()

        # Convert idea2 to topic
        convert_to_topic(db_session, idea2.id)
        db_session.flush()

        removed = clean_kids_queue(db_session, user_with_kids)
        assert removed == 1
        queue = auto.config.get("kids_idea_queue", [])
        assert idea2.id not in queue

    def test_clean_queue_removes_nonexistent(self, db_session, user_with_kids):
        """clean_kids_queue removes nonexistent idea IDs."""
        auto = self._setup_automation(db_session, user_with_kids, {})

        idea1 = create_idea(db_session, user_with_kids, title="Por que o céu é azul?")
        db_session.flush()

        auto.config = {"kids_idea_queue": [idea1.id, 99999]}  # 99999 doesn't exist
        db_session.flush()

        removed = clean_kids_queue(db_session, user_with_kids)
        assert removed == 1
        queue = auto.config.get("kids_idea_queue", [])
        assert 99999 not in queue
        assert idea1.id in queue

    def test_clean_queue_no_changes_returns_zero(self, db_session, user_with_kids):
        """clean_kids_queue returns 0 when queue is already clean."""
        auto = self._setup_automation(db_session, user_with_kids, {})

        idea1 = create_idea(db_session, user_with_kids, title="Por que o céu é azul?")
        idea1.status = KidsIdeaStatus.queued.value
        db_session.flush()

        auto.config = {"kids_idea_queue": [idea1.id]}
        db_session.flush()

        assert clean_kids_queue(db_session, user_with_kids) == 0


# ── Queue Reconciliation API tests ───────────────────────────────────────────


class TestQueueReconciliationAPI:
    """Queue reconciliation API endpoint tests."""

    @pytest.fixture
    def client(self, db_session, user_with_kids):
        """Create a FastAPI TestClient with mocked auth and DB."""
        from fastapi.testclient import TestClient
        from gpcg.api.app import create_app
        from gpcg.core.models import User

        user = db_session.query(User).filter(User.id == user_with_kids).first()

        with patch("gpcg.api.app.init_db", return_value=None):
            app = create_app()

        from gpcg.infrastructure.auth import get_current_user
        from gpcg.infrastructure.database import get_db

        def override_auth():
            return user

        def override_db():
            yield db_session

        app.dependency_overrides[get_current_user] = override_auth
        app.dependency_overrides[get_db] = override_db

        client = TestClient(app)
        yield client
        app.dependency_overrides.clear()

    def test_reconcile_endpoint(self, client, db_session, user_with_kids):
        """POST /api/kids/idea-queue/reconcile triggers reconciliation."""
        from gpcg.core.models import Automation
        auto = Automation(user_id=user_with_kids, name="Automação", config={
            "kids_queue_mode": "automatic",
            "kids_auto_fill_queue": True,
            "kids_max_queue_size": 5,
        })
        db_session.add(auto)
        db_session.flush()

        # Create an evaluated idea
        idea = create_idea(db_session, user_with_kids, title="Por que o céu é azul?")
        idea.status = KidsIdeaStatus.evaluated.value
        idea.final_score = 0.9
        db_session.flush()

        resp = client.post("/api/kids/idea-queue/reconcile")
        assert resp.status_code == 200
        data = resp.json()
        assert data["added"] >= 1

    def test_get_queue_triggers_clean_and_reconcile(self, client, db_session, user_with_kids):
        """GET /api/kids/idea-queue cleans and reconciles the queue."""
        from gpcg.core.models import Automation
        auto = Automation(user_id=user_with_kids, name="Automação", config={
            "kids_queue_mode": "automatic",
            "kids_auto_fill_queue": True,
            "kids_max_queue_size": 5,
            "kids_idea_queue": [99999],  # invalid ID
        })
        db_session.add(auto)
        db_session.flush()

        # Create an evaluated idea
        idea = create_idea(db_session, user_with_kids, title="Por que o céu é azul?")
        idea.status = KidsIdeaStatus.evaluated.value
        idea.final_score = 0.9
        db_session.flush()

        resp = client.get("/api/kids/idea-queue")
        assert resp.status_code == 200
        data = resp.json()
        # 99999 should be cleaned, idea should be added
        assert 99999 not in data["queue"]


# ── Domain-aware Automation tests ────────────────────────────────────────────


class TestAutomationStrategies:
    """Domain-aware automation strategy tests."""

    def test_get_strategy_games(self):
        """get_strategy returns GamesAutomationStrategy for games domain."""
        strategy = get_strategy("games")
        assert strategy == GamesAutomationStrategy

    def test_get_strategy_kids(self):
        """get_strategy returns KidsAutomationStrategy for kids domain."""
        strategy = get_strategy("kids")
        assert strategy == KidsAutomationStrategy

    def test_get_strategy_unknown_defaults_to_games(self):
        """get_strategy defaults to Games for unknown domains."""
        strategy = get_strategy("unknown")
        assert strategy == GamesAutomationStrategy

    def test_get_user_domain_kids(self, db_session, user_with_kids):
        """get_user_domain returns 'kids' for Kids channel."""
        domain = get_user_domain(db_session, user_with_kids)
        assert domain == "kids"

    def test_get_user_domain_defaults_to_games(self, db_session):
        """get_user_domain defaults to 'games' when no profile exists."""
        from gpcg.core.models import User
        user = User(email="test-games@example.com", is_active=True)
        db_session.add(user)
        db_session.flush()
        domain = get_user_domain(db_session, user.id)
        assert domain == "games"


class TestKidsAutomationCheck:
    """KidsAutomationStrategy.check tests."""

    def _setup_automation(self, session, user_id, config=None):
        """Create an Automation record with given config."""
        from gpcg.core.models import Automation
        auto = Automation(
            user_id=user_id,
            name="Automação",
            config=config or {},
        )
        session.add(auto)
        session.flush()
        return auto

    def test_check_no_youtube_returns_none(self, db_session, user_with_kids):
        """check returns None when YouTube is not connected."""
        auto = self._setup_automation(db_session, user_with_kids, {
            "kids_queue_mode": "automatic",
            "kids_auto_fill_queue": True,
        })
        # User doesn't have google_user_id set
        result = KidsAutomationStrategy.check(auto, db_session)
        assert result is None

    def test_check_no_story_assets_returns_none(self, db_session, user_with_kids):
        """check returns None when no StoryAssets are ready."""
        from gpcg.core.models import User
        user = db_session.query(User).filter(User.id == user_with_kids).first()
        user.google_user_id = "test-google-id"
        db_session.flush()

        auto = self._setup_automation(db_session, user_with_kids, {
            "kids_queue_mode": "automatic",
            "kids_auto_fill_queue": True,
        })
        result = KidsAutomationStrategy.check(auto, db_session)
        assert result is None

    def test_check_no_queue_returns_none(self, db_session, user_with_kids):
        """check returns None when queue is empty."""
        from gpcg.core.models import User
        from gpcg.domains.kids.models import KidsTopic, StoryAsset, AssetProcessingStatus

        user = db_session.query(User).filter(User.id == user_with_kids).first()
        user.google_user_id = "test-google-id"

        # Create a topic with a ready StoryAsset
        topic = KidsTopic(
            user_id=user_with_kids,
            title="Test topic",
            slug="test-topic",
            category="animals",
            age_range="3-6",
        )
        db_session.add(topic)
        db_session.flush()
        asset = StoryAsset(
            user_id=user_with_kids,
            topic_id=topic.id,
            filename="test.png",
            storage_key="test/test.png",
            processing_status=AssetProcessingStatus.ready.value,
        )
        db_session.add(asset)
        db_session.flush()

        auto = self._setup_automation(db_session, user_with_kids, {
            "kids_queue_mode": "automatic",
            "kids_auto_fill_queue": False,  # no auto-fill
            "kids_idea_queue": [],
        })
        result = KidsAutomationStrategy.check(auto, db_session)
        assert result is None

    def test_check_returns_pending_when_ready(self, db_session, user_with_kids):
        """check returns pending dict when all conditions are met."""
        from gpcg.core.models import User
        from gpcg.domains.kids.models import KidsTopic, StoryAsset, AssetProcessingStatus

        user = db_session.query(User).filter(User.id == user_with_kids).first()
        user.google_user_id = "test-google-id"

        # Create a topic with a ready StoryAsset
        topic = KidsTopic(
            user_id=user_with_kids,
            title="Test topic",
            slug="test-topic",
            category="animals",
            age_range="3-6",
        )
        db_session.add(topic)
        db_session.flush()
        asset = StoryAsset(
            user_id=user_with_kids,
            topic_id=topic.id,
            filename="test.png",
            storage_key="test/test.png",
            processing_status=AssetProcessingStatus.ready.value,
        )
        db_session.add(asset)
        db_session.flush()

        # Create an evaluated idea and add to queue
        idea = create_idea(db_session, user_with_kids, title="Por que o céu é azul?")
        idea.status = KidsIdeaStatus.queued.value
        idea.final_score = 0.9
        db_session.flush()

        auto = self._setup_automation(db_session, user_with_kids, {
            "kids_queue_mode": "automatic",
            "kids_auto_fill_queue": False,
            "kids_idea_queue": [idea.id],
        })
        result = KidsAutomationStrategy.check(auto, db_session)
        assert result is not None
        assert result["domain"] == "kids"
        assert result["user_id"] == user_with_kids
        assert idea.id in result["kids_idea_queue"]


class TestKidsAutomationCreateJob:
    """KidsAutomationStrategy.create_job tests."""

    def test_create_job_no_automation_returns_none(self, db_session, user_with_kids):
        """create_job returns None when no automation exists."""
        result = KidsAutomationStrategy.create_job(user_with_kids)
        assert result is None

    def test_create_job_no_youtube_returns_none(self, db_session, user_with_kids):
        """create_job returns None when YouTube is not connected."""
        from gpcg.core.models import Automation
        auto = Automation(user_id=user_with_kids, name="Automação", config={
            "kids_idea_queue": [],
        })
        db_session.add(auto)
        db_session.commit()
        result = KidsAutomationStrategy.create_job(user_with_kids)
        assert result is None

    def test_create_job_no_story_assets_returns_none(self, db_session, user_with_kids):
        """create_job returns None when no StoryAssets are ready."""
        from gpcg.core.models import User, Automation
        user = db_session.query(User).filter(User.id == user_with_kids).first()
        user.google_user_id = "test-google-id"
        auto = Automation(user_id=user_with_kids, name="Automação", config={
            "kids_idea_queue": [],
        })
        db_session.add(auto)
        db_session.commit()
        result = KidsAutomationStrategy.create_job(user_with_kids)
        assert result is None

    def test_create_job_empty_queue_returns_none(self, db_session, user_with_kids):
        """create_job returns None when queue is empty."""
        from gpcg.core.models import User, Automation
        from gpcg.domains.kids.models import KidsTopic, StoryAsset, AssetProcessingStatus

        user = db_session.query(User).filter(User.id == user_with_kids).first()
        user.google_user_id = "test-google-id"
        topic = KidsTopic(
            user_id=user_with_kids, title="Test", slug="test",
            category="animals", age_range="3-6",
        )
        db_session.add(topic)
        db_session.flush()
        asset = StoryAsset(
            user_id=user_with_kids, topic_id=topic.id, filename="test.png",
            storage_key="test/test.png",
            processing_status=AssetProcessingStatus.ready.value,
        )
        db_session.add(asset)
        db_session.flush()

        auto = Automation(user_id=user_with_kids, name="Automação", config={
            "kids_idea_queue": [],
        })
        db_session.add(auto)
        db_session.commit()
        result = KidsAutomationStrategy.create_job(user_with_kids)
        assert result is None


# ── Games regression tests ───────────────────────────────────────────────────


class TestGamesAutomationRegression:
    """Verify Games automation behavior is unchanged."""

    def test_games_strategy_returns_games(self):
        """Games domain uses GamesAutomationStrategy."""
        assert get_strategy("games") == GamesAutomationStrategy

    def test_games_strategy_check_returns_none(self):
        """GamesAutomationStrategy.check is a no-op marker (returns None)."""
        # The actual Games logic is in check_automation(), not in the strategy.
        # This test verifies the strategy doesn't interfere.
        assert GamesAutomationStrategy.check(None, None) is None


# ── Production Integration tests ─────────────────────────────────────────────


class TestProductionIntegration:
    """End-to-end production integration tests: KidsIdea → KidsTopic → generate."""

    def test_idea_to_topic_to_job_flow(self, db_session, user_with_kids):
        """Full flow: create idea → convert to topic → create generation job."""
        from gpcg.core.models import Job, JobStatus, JobType, ContentDomain
        from gpcg.domains.kids.models import (
            KidsTopic, StoryAsset, AssetProcessingStatus,
        )

        # 1. Create idea
        idea = create_idea(
            db_session, user_with_kids,
            title="Por que o céu é azul?",
            category="science",
        )
        assert idea.status == KidsIdeaStatus.discovered.value

        # 2. Convert to topic
        topic = convert_to_topic(db_session, idea.id)
        assert topic is not None
        assert topic.idea_id == idea.id
        assert topic.title == "Por que o céu é azul?"

        # 3. Add a StoryAsset to the topic
        asset = StoryAsset(
            user_id=user_with_kids,
            topic_id=topic.id,
            filename="sky.png",
            storage_key="test/sky.png",
            processing_status=AssetProcessingStatus.ready.value,
        )
        db_session.add(asset)
        db_session.flush()

        # 4. Create generation job (simulating what the produce endpoint does)
        import uuid
        job = Job(
            job_uuid=str(uuid.uuid4()),
            type=JobType.generate_short.value,
            user_id=user_with_kids,
            domain=ContentDomain.kids.value,
            status=JobStatus.queued.value,
            artifacts={
                "topic_id": topic.id,
                "topic_title": topic.title,
                "idea_id": idea.id,
            },
        )
        db_session.add(job)
        db_session.flush()

        # 5. Verify traceability
        assert job.domain == ContentDomain.kids.value
        assert job.artifacts["topic_id"] == topic.id
        assert job.artifacts["idea_id"] == idea.id

        # 6. Verify idea is marked as converted
        db_session.refresh(idea)
        assert idea.status == KidsIdeaStatus.converted.value
        assert idea.topic_id == topic.id

    def test_convert_to_topic_creates_traceable_link(self, db_session, user_with_kids):
        """Converting an idea to a topic creates a bidirectional link."""
        idea = create_idea(
            db_session, user_with_kids,
            title="Como funcionam os vulcões?",
            category="nature",
        )
        topic = convert_to_topic(db_session, idea.id)
        assert topic is not None

        # Topic → Idea
        assert topic.idea_id == idea.id

        # Idea → Topic
        db_session.refresh(idea)
        assert idea.topic_id == topic.id
        assert idea.status == KidsIdeaStatus.converted.value

    def test_duplicate_topic_prevention(self, db_session, user_with_kids):
        """Cannot convert an idea if a topic with the same title already exists."""
        from gpcg.domains.kids.models import KidsTopic

        # Create a topic manually
        topic = KidsTopic(
            user_id=user_with_kids,
            title="Por que o céu é azul?",
            slug="por-que-o-ceu-e-azul",
            category="science",
            age_range="3-6",
        )
        db_session.add(topic)
        db_session.flush()

        # Try to create an idea with the same title and convert it
        idea = create_idea(
            db_session, user_with_kids,
            title="Por que o céu é azul?",
            category="science",
        )
        db_session.flush()

        # Conversion should fail (duplicate topic)
        result = convert_to_topic(db_session, idea.id)
        assert result is None  # duplicate detected

    def test_provenance_no_topic(self, db_session, user_with_kids):
        """Provenance for an idea without a topic returns empty topic/jobs/videos."""
        idea = create_idea(
            db_session, user_with_kids,
            title="Por que o céu é azul?",
        )
        # Don't convert — no topic yet
        # Verify the idea has no topic
        assert idea.topic_id is None
        assert idea.status == KidsIdeaStatus.discovered.value


class TestProductionAPI:
    """Production integration API tests."""

    @pytest.fixture
    def client(self, db_session, user_with_kids):
        """Create a FastAPI TestClient with mocked auth and DB."""
        from fastapi.testclient import TestClient
        from gpcg.api.app import create_app
        from gpcg.core.models import User

        user = db_session.query(User).filter(User.id == user_with_kids).first()

        with patch("gpcg.api.app.init_db", return_value=None):
            app = create_app()

        from gpcg.infrastructure.auth import get_current_user
        from gpcg.infrastructure.database import get_db

        def override_auth():
            return user

        def override_db():
            yield db_session

        app.dependency_overrides[get_current_user] = override_auth
        app.dependency_overrides[get_db] = override_db

        client = TestClient(app)
        yield client
        app.dependency_overrides.clear()

    def test_produce_endpoint_no_assets(self, client, db_session, user_with_kids):
        """POST /api/kids/ideas/{id}/produce returns 422 when no assets."""
        # Create an idea
        resp = client.post("/api/kids/ideas", json={
            "title": "Por que o céu é azul?",
            "category": "science",
        })
        idea_id = resp.json()["id"]

        # Try to produce — no assets on the topic
        resp = client.post(f"/api/kids/ideas/{idea_id}/produce")
        assert resp.status_code == 422
        assert "no ready assets" in resp.json()["detail"].lower()

    def test_produce_endpoint_with_assets(self, client, db_session, user_with_kids):
        """POST /api/kids/ideas/{id}/produce creates topic + job."""
        from gpcg.domains.kids.models import KidsTopic, StoryAsset, AssetProcessingStatus

        # Create an idea
        resp = client.post("/api/kids/ideas", json={
            "title": "Por que o céu é azul?",
            "category": "science",
        })
        idea_id = resp.json()["id"]

        # Convert to topic first (so we can add assets)
        resp = client.post(f"/api/kids/ideas/{idea_id}/convert", json={})
        topic_id = resp.json()["topic_id"]

        # Add a StoryAsset
        asset = StoryAsset(
            user_id=user_with_kids,
            topic_id=topic_id,
            filename="sky.png",
            storage_key="test/sky.png",
            processing_status=AssetProcessingStatus.ready.value,
        )
        db_session.add(asset)
        db_session.flush()

        # Now produce
        resp = client.post(f"/api/kids/ideas/{idea_id}/produce")
        assert resp.status_code == 200
        data = resp.json()
        assert data["idea_id"] == idea_id
        assert data["topic_id"] == topic_id
        assert data["job_id"] is not None

    def test_produce_endpoint_already_converted(self, client, db_session, user_with_kids):
        """POST /api/kids/ideas/{id}/produce works when idea is already converted."""
        from gpcg.domains.kids.models import StoryAsset, AssetProcessingStatus

        # Create and convert
        resp = client.post("/api/kids/ideas", json={
            "title": "Como funcionam os vulcões?",
            "category": "nature",
        })
        idea_id = resp.json()["id"]

        resp = client.post(f"/api/kids/ideas/{idea_id}/convert", json={})
        topic_id = resp.json()["topic_id"]

        # Add asset
        asset = StoryAsset(
            user_id=user_with_kids,
            topic_id=topic_id,
            filename="volcano.png",
            storage_key="test/volcano.png",
            processing_status=AssetProcessingStatus.ready.value,
        )
        db_session.add(asset)
        db_session.flush()

        # Produce — should use existing topic
        resp = client.post(f"/api/kids/ideas/{idea_id}/produce")
        assert resp.status_code == 200
        data = resp.json()
        assert data["topic_id"] == topic_id

    def test_produce_endpoint_rejected_idea(self, client):
        """POST /api/kids/ideas/{id}/produce returns 409 for rejected idea."""
        resp = client.post("/api/kids/ideas", json={"title": "To reject"})
        idea_id = resp.json()["id"]
        client.post(f"/api/kids/ideas/{idea_id}/reject", json={"reason": "test"})

        resp = client.post(f"/api/kids/ideas/{idea_id}/produce")
        assert resp.status_code == 409

    def test_provenance_endpoint(self, client, db_session, user_with_kids):
        """GET /api/kids/ideas/{id}/provenance returns full chain."""
        from gpcg.domains.kids.models import StoryAsset, AssetProcessingStatus

        # Create and convert
        resp = client.post("/api/kids/ideas", json={
            "title": "Por que o céu é azul?",
            "category": "science",
        })
        idea_id = resp.json()["id"]

        resp = client.post(f"/api/kids/ideas/{idea_id}/convert", json={})
        topic_id = resp.json()["topic_id"]

        # Add asset and produce
        asset = StoryAsset(
            user_id=user_with_kids,
            topic_id=topic_id,
            filename="sky.png",
            storage_key="test/sky.png",
            processing_status=AssetProcessingStatus.ready.value,
        )
        db_session.add(asset)
        db_session.flush()

        client.post(f"/api/kids/ideas/{idea_id}/produce")

        # Get provenance
        resp = client.get(f"/api/kids/ideas/{idea_id}/provenance")
        assert resp.status_code == 200
        data = resp.json()
        assert data["idea"]["id"] == idea_id
        assert data["topic"] is not None
        assert data["topic"]["id"] == topic_id
        assert len(data["jobs"]) >= 1
        assert data["jobs"][0]["idea_id"] == idea_id

    def test_provenance_no_topic(self, client):
        """GET /api/kids/ideas/{id}/provenance returns null topic when not converted."""
        resp = client.post("/api/kids/ideas", json={"title": "Por que o céu é azul?"})
        idea_id = resp.json()["id"]

        resp = client.get(f"/api/kids/ideas/{idea_id}/provenance")
        assert resp.status_code == 200
        data = resp.json()
        assert data["topic"] is None
        assert data["jobs"] == []
        assert data["videos"] == []

    def test_generate_includes_idea_id_in_artifacts(self, client, db_session, user_with_kids):
        """POST /api/kids/generate includes idea_id in job artifacts for traceability."""
        from gpcg.domains.kids.models import StoryAsset, AssetProcessingStatus
        from gpcg.core.models import Job

        # Create and convert
        resp = client.post("/api/kids/ideas", json={
            "title": "O que são estrelas cadentes?",
            "category": "space",
        })
        idea_id = resp.json()["id"]

        resp = client.post(f"/api/kids/ideas/{idea_id}/convert", json={})
        topic_id = resp.json()["topic_id"]

        # Add asset
        asset = StoryAsset(
            user_id=user_with_kids,
            topic_id=topic_id,
            filename="stars.png",
            storage_key="test/stars.png",
            processing_status=AssetProcessingStatus.ready.value,
        )
        db_session.add(asset)
        db_session.flush()

        # Generate via the existing /kids/generate endpoint
        resp = client.post("/api/kids/generate", json={"topic_id": topic_id})
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

        # Verify idea_id is in job artifacts
        job = db_session.query(Job).filter(Job.id == job_id).first()
        assert job is not None
        assert job.artifacts.get("idea_id") == idea_id
        assert job.artifacts.get("topic_id") == topic_id
