"""Tests for the V2 Story Finder module.

Tests:
  1 — find_story returns a StoryConcept with angle, frame, narrative_hook
  2 — is_story=false when the fact has no narrative potential
  3 — is_insight flag is parsed correctly
  4 — confidence is clamped to 0-1
  5 — Disabled flag returns empty concept
  6 — LLM error returns empty concept (no raise)
  7 — is_acceptable gate: is_story + confidence >= threshold
  8 — StoryConcept dataclass roundtrip (to_dict / from_dict)
  9 — No fact_id returns empty concept
  10 — EditorialPlanner uses story_concept.angle as central_idea fallback
  11 — ScriptService incorporates story_concept in draft prompt
"""

from __future__ import annotations

from typing import Optional
from unittest.mock import patch

import pytest

from gpcg.application.editorial_planner import EditorialPlanner
from gpcg.application.script_service import ScriptService
from gpcg.application.story_finder import StoryFinder
from gpcg.config import get_settings
from gpcg.domain.creative_plan import StoryConcept, VideoCreativePlan
from gpcg.core.models import ContentPlan, Fact
from gpcg.domains.games.models import Game
from gpcg.infrastructure.database import session_scope
from gpcg.infrastructure.llm import LLMError


# ── Fake LLM ─────────────────────────────────────────────────────────────────


class FakeLLMClient:
    """Fake LLM that returns scripted responses based on system prompt content."""

    def __init__(
        self,
        story_response: Optional[dict] = None,
        planner_response: Optional[dict] = None,
        script_response: Optional[dict] = None,
        error: bool = False,
    ):
        self.story_response = story_response or {}
        self.planner_response = planner_response or {}
        self.script_response = script_response or {"script": "Um script de teste."}
        self.error = error
        self.calls: list[dict] = []

    def chat_json(self, system, prompt, model=None, temperature=0.7, max_tokens=2000):
        self.calls.append({"system": system, "prompt": prompt, "model": model})
        if self.error:
            raise LLMError("fake LLM error")
        if "STORY FINDER" in system or "story finder" in system.lower():
            return self.story_response
        if "EDITORIAL PLANNER" in system or "editorial" in system.lower():
            return self.planner_response
        return self.script_response

    def vision_json(self, **kwargs):
        return {}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_fact_and_plan(claim: str = "Test fact claim for story finding") -> tuple[int, int]:
    """Create a Game, Fact, and ContentPlan in the DB. Returns (fact_id, plan_id)."""
    with session_scope() as session:
        game = Game(canonical_name="TestGame StoryFinder", user_id=None)
        session.add(game)
        session.flush()
        fact = Fact(
            game_id=game.id, claim=claim, category="curiosity",
            quality_score=80, novelty_score=70,
        )
        session.add(fact)
        session.flush()
        plan = ContentPlan(
            game_id=game.id, fact_id=fact.id, topic="Test topic",
            hook="Test hook", tone="curious",
        )
        session.add(plan)
        session.flush()
        return fact.id, plan.id


# ── Tests ────────────────────────────────────────────────────────────────────


def test_find_story_returns_concept_with_angle_and_frame():
    """find_story returns a StoryConcept with angle, curiosity_gap, narrative_hook, frame."""
    fact_id, plan_id = _make_fact_and_plan()
    fake_llm = FakeLLMClient(story_response={
        "angle": "ninguém programou aquelas quedas",
        "curiosity_gap": "por que o personagem cai daquele jeito",
        "narrative_hook": "Olha, ninguém esperava que isso fosse acontecer.",
        "frame": "o jogo pune você por tentar ajudar",
        "is_insight": True,
        "is_story": True,
        "confidence": 0.85,
    })
    with patch.object(get_settings(), "gpcg_story_finder_enabled", True):
        finder = StoryFinder(llm=fake_llm)
        with session_scope() as session:
            plan = session.get(ContentPlan, plan_id)
            concept = finder.find_story(session, plan)
            assert concept.success
            assert concept.is_story
            assert concept.angle == "ninguém programou aquelas quedas"
            assert concept.frame == "o jogo pune você por tentar ajudar"
            assert concept.narrative_hook.startswith("Olha")
            assert concept.is_insight is True
            assert concept.confidence == pytest.approx(0.85)


def test_is_story_false_when_no_narrative_potential():
    """When the LLM says is_story=false, the concept reflects that."""
    fact_id, plan_id = _make_fact_and_plan(claim="O jogo tem 50 níveis.")
    fake_llm = FakeLLMClient(story_response={
        "angle": "",
        "curiosity_gap": "",
        "narrative_hook": "",
        "frame": "",
        "is_insight": False,
        "is_story": False,
        "confidence": 0.2,
    })
    with patch.object(get_settings(), "gpcg_story_finder_enabled", True):
        finder = StoryFinder(llm=fake_llm)
        with session_scope() as session:
            plan = session.get(ContentPlan, plan_id)
            concept = finder.find_story(session, plan)
            assert concept.success
            assert not concept.is_story
            assert concept.confidence == pytest.approx(0.2)


def test_is_insight_flag_parsed_correctly():
    """is_insight is parsed as a boolean."""
    fact_id, plan_id = _make_fact_and_plan()
    fake_llm = FakeLLMClient(story_response={
        "angle": "test", "curiosity_gap": "test", "narrative_hook": "test",
        "frame": "test", "is_insight": False, "is_story": True, "confidence": 0.7,
    })
    with patch.object(get_settings(), "gpcg_story_finder_enabled", True):
        finder = StoryFinder(llm=fake_llm)
        with session_scope() as session:
            plan = session.get(ContentPlan, plan_id)
            concept = finder.find_story(session, plan)
            assert concept.is_insight is False


def test_confidence_clamped_to_0_1():
    """confidence out of range is clamped to [0, 1]."""
    fact_id, plan_id = _make_fact_and_plan()
    fake_llm = FakeLLMClient(story_response={
        "angle": "test", "curiosity_gap": "test", "narrative_hook": "test",
        "frame": "test", "is_insight": True, "is_story": True, "confidence": 1.5,
    })
    with patch.object(get_settings(), "gpcg_story_finder_enabled", True):
        finder = StoryFinder(llm=fake_llm)
        with session_scope() as session:
            plan = session.get(ContentPlan, plan_id)
            concept = finder.find_story(session, plan)
            assert concept.confidence == pytest.approx(1.0)


def test_disabled_flag_returns_empty_concept():
    """When GPCG_STORY_FINDER_ENABLED=false, find_story returns empty concept."""
    fact_id, plan_id = _make_fact_and_plan()
    with patch.object(get_settings(), "gpcg_story_finder_enabled", False):
        finder = StoryFinder(llm=FakeLLMClient())
        with session_scope() as session:
            plan = session.get(ContentPlan, plan_id)
            concept = finder.find_story(session, plan)
            assert not concept.success
            assert "disabled" in concept.error


def test_llm_error_returns_empty_no_raise():
    """When the LLM fails, find_story returns an empty concept (no raise)."""
    fact_id, plan_id = _make_fact_and_plan()
    fake_llm = FakeLLMClient(error=True)
    with patch.object(get_settings(), "gpcg_story_finder_enabled", True):
        finder = StoryFinder(llm=fake_llm)
        with session_scope() as session:
            plan = session.get(ContentPlan, plan_id)
            concept = finder.find_story(session, plan)
            assert not concept.success
            assert "LLM error" in concept.error


def test_is_acceptable_gate():
    """is_acceptable returns True only if is_story=true AND confidence >= threshold."""
    finder = StoryFinder(llm=FakeLLMClient())
    # is_story=true, confidence=0.8 → acceptable (threshold default 0.5)
    good = StoryConcept(is_story=True, confidence=0.8, success=True)
    assert finder.is_acceptable(good)
    # is_story=false → not acceptable
    no_story = StoryConcept(is_story=False, confidence=0.9, success=True)
    assert not finder.is_acceptable(no_story)
    # confidence below threshold → not acceptable
    low_conf = StoryConcept(is_story=True, confidence=0.3, success=True)
    assert not finder.is_acceptable(low_conf)
    # not success → not acceptable
    failed = StoryConcept(is_story=True, confidence=0.9, success=False)
    assert not finder.is_acceptable(failed)


def test_story_concept_dataclass_roundtrip():
    """StoryConcept.to_dict / from_dict roundtrip."""
    sc = StoryConcept(
        fact_claim="test claim",
        angle="test angle",
        curiosity_gap="test gap",
        narrative_hook="test hook",
        frame="test frame",
        is_insight=True,
        is_story=True,
        confidence=0.75,
    )
    d = sc.to_dict()
    sc2 = StoryConcept.from_dict(d)
    assert sc2.fact_claim == sc.fact_claim
    assert sc2.angle == sc.angle
    assert sc2.curiosity_gap == sc.curiosity_gap
    assert sc2.narrative_hook == sc.narrative_hook
    assert sc2.frame == sc.frame
    assert sc2.is_insight == sc.is_insight
    assert sc2.is_story == sc.is_story
    assert sc2.confidence == sc.confidence


def test_no_fact_id_returns_empty_concept():
    """When the content plan has no fact_id, find_story returns empty concept."""
    with session_scope() as session:
        game = Game(canonical_name="TestGame NoFact", user_id=None)
        session.add(game)
        session.flush()
        plan = ContentPlan(game_id=game.id, fact_id=None, topic="Test", hook="hook")
        session.add(plan)
        session.flush()
        plan_id = plan.id

    with patch.object(get_settings(), "gpcg_story_finder_enabled", True):
        finder = StoryFinder(llm=FakeLLMClient())
        with session_scope() as session:
            plan = session.get(ContentPlan, plan_id)
            concept = finder.find_story(session, plan)
            assert not concept.success
            assert "no fact" in concept.error.lower()


def test_editorial_planner_uses_story_concept_angle_as_central_idea():
    """When the LLM produces a weak central_idea, the story_concept.angle fills in."""
    fact_id, plan_id = _make_fact_and_plan()
    fake_llm = FakeLLMClient(
        planner_response={
            "video_type": "GAME_RELATED",
            "central_idea": "short",  # weak — will be replaced by angle
            "narrative_beats": [{"label": "hook", "description": "d", "content_type": "fact"}],
            "tone": {"informative": 0.8, "casual": 0.5},
            "humor": {"enabled": False, "intensity": "none", "styles": [], "frequency": "sparse"},
            "gameplay_strategy": "background_filler",
            "visual_dependency": "low",
            "gameplay_query": "",
            "model_recommendation": "gemma3:12b",
            "model_reason": "serious",
        },
        story_response={
            "angle": "The real angle from story finder",
            "curiosity_gap": "gap",
            "narrative_hook": "hook line",
            "frame": "the frame",
            "is_insight": True,
            "is_story": True,
            "confidence": 0.9,
        },
    )
    story_concept = StoryConcept(
        fact_claim="test", angle="The real angle from story finder",
        curiosity_gap="gap", narrative_hook="hook line", frame="the frame",
        is_insight=True, is_story=True, confidence=0.9, success=True,
    )
    with patch.object(get_settings(), "gpcg_editorial_planning_enabled", True):
        planner = EditorialPlanner(llm=fake_llm)
        with session_scope() as session:
            plan = session.get(ContentPlan, plan_id)
            creative_plan = planner.plan(session, plan, story_concept=story_concept)
            # The weak central_idea ("short") should be replaced by the angle
            assert creative_plan.central_idea == "The real angle from story finder"


def test_script_service_incorporates_story_concept_in_draft_prompt():
    """The draft prompt includes the story concept angle and narrative_hook."""
    fake_llm = FakeLLMClient(script_response={"script": "Script with story angle."})
    story_concept = StoryConcept(
        fact_claim="test", angle="my special angle",
        curiosity_gap="the gap", narrative_hook="Opening line from story",
        frame="the frame", is_insight=True, is_story=True, confidence=0.9, success=True,
    )
    svc = ScriptService(llm=fake_llm)
    # Test the helper directly
    formatted = svc._format_story_concept(story_concept)
    assert "my special angle" in formatted
    assert "Opening line from story" in formatted
    assert "the frame" in formatted
