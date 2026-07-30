"""Tests for the editorial pipeline — EditorialPlanner + ScriptCritic.

Tests 1-6 from the spec:
  1 — Serious video (no humor): plan should set humor.enabled=false, model=gemma3
  2 — Informal video (low humor): plan should set humor.enabled=true, intensity=low
  3 — General topic video (not about a game): video_type=GENERAL_TOPIC
  4 — Game-related video: video_type=GAME_RELATED, gameplay_strategy=related
  5 — No humor: creative engine should be skipped, script should have zero jokes
  6 — Script revision: critic flags bad humor, script is revised (joke removed)

These tests use a FakeLLMClient that returns scripted JSON responses, so
they don't require Ollama to be running.
"""

from __future__ import annotations

from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from gpcg.domain.creative_plan import (
    CRITIC_VERDICT_PASS,
    CRITIC_VERDICT_REVISE,
    HUMOR_INTENSITY_LOW,
    HUMOR_INTENSITY_NONE,
    VIDEO_TYPE_GAME_RELATED,
    VIDEO_TYPE_GENERAL_TOPIC,
    HumorPlan,
    ScriptReview,
    VideoCreativePlan,
)
from gpcg.infrastructure.llm import LLMClient


# ── Fake LLM ─────────────────────────────────────────────────────────────────


class FakeLLMClient:
    """Fake LLM that returns scripted JSON responses.

    The response is selected based on the system prompt content:
    - if "EDITORIAL PLANNER" in system → planner response
    - if "SCRIPT CRITIC" in system → critic response
    - else → generic script response
    """

    def __init__(
        self,
        planner_response: Optional[dict] = None,
        critic_response: Optional[dict] = None,
        script_response: Optional[dict] = None,
    ):
        self.planner_response = planner_response or {}
        self.critic_response = critic_response or {}
        self.script_response = script_response or {"script": "Um script de teste."}
        self.calls: list[dict] = []

    def chat_json(self, system, prompt, model=None, temperature=0.7, max_tokens=2000):
        self.calls.append({"system": system, "prompt": prompt, "model": model})
        if "EDITORIAL PLANNER" in system or "editorial" in system.lower():
            return self.planner_response
        if "SCRIPT CRITIC" in system or "critic" in system.lower():
            return self.critic_response
        return self.script_response

    def vision_json(self, **kwargs):
        return {}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_plan(
    video_type=VIDEO_TYPE_GAME_RELATED,
    humor_enabled=False,
    humor_intensity=HUMOR_INTENSITY_NONE,
    model="gemma3:12b",
    central_idea="The central idea of the video",
    gameplay_strategy=None,
) -> dict:
    """Build a planner JSON response."""
    if gameplay_strategy is None:
        gameplay_strategy = "related" if video_type == VIDEO_TYPE_GAME_RELATED else "background_filler"
    return {
        "video_type": video_type,
        "central_idea": central_idea,
        "narrative_beats": [
            {"label": "hook", "description": "grab attention", "content_type": "observation"},
            {"label": "context", "description": "set up topic", "content_type": "fact"},
            {"label": "development", "description": "explore idea", "content_type": "fact"},
            {"label": "escalation", "description": "raise stakes", "content_type": "commentary"},
            {"label": "payoff", "description": "deliver promise", "content_type": "observation"},
            {"label": "conclusion", "description": "land idea", "content_type": "conclusion"},
        ],
        "tone": {
            "informative": 0.8,
            "casual": 0.5,
            "sarcastic": 0.1,
            "comedic": 0.05,
            "dramatic": 0.1,
            "nostalgic": 0.0,
            "mysterious": 0.0,
            "energetic": 0.3,
        },
        "humor": {
            "enabled": humor_enabled,
            "intensity": humor_intensity,
            "styles": ["observation", "sarcasm"] if humor_enabled else [],
            "frequency": "sparse",
        },
        "gameplay_strategy": gameplay_strategy,
        "visual_dependency": "medium",
        "gameplay_query": "character being chased" if video_type == VIDEO_TYPE_GAME_RELATED else "",
        "model_recommendation": model,
        "model_reason": f"{model} chosen for this video type",
    }


def _make_critic(verdict=CRITIC_VERDICT_PASS, score=85.0, feedback="") -> dict:
    return {
        "verdict": verdict,
        "overall_score": score,
        "dimension_scores": {
            "structure": 85,
            "naturalness": 80,
            "humor": 75,
            "coherence": 90,
            "gameplay": 70,
        },
        "issues": [],
        "feedback": feedback,
    }


# ── Test 1: Serious video (no humor) ─────────────────────────────────────────


class TestSeriousVideo:
    """Test 1 — serious video: humor disabled, gemma3 model."""

    def test_serious_video_disables_humor(self):
        from gpcg.application.editorial_planner import EditorialPlanner

        llm = FakeLLMClient(planner_response=_make_plan(
            video_type=VIDEO_TYPE_GAME_RELATED,
            humor_enabled=False,
            humor_intensity=HUMOR_INTENSITY_NONE,
            model="gemma3:12b",
        ))
        planner = EditorialPlanner(llm=llm)

        # Mock session and content plan
        session = MagicMock()
        plan = MagicMock()
        plan.topic = "The history of Bully"
        plan.hook = "A school simulation"
        plan.tone = "serious"
        plan.target_duration = 60
        plan.game_id = 1
        plan.fact_id = None
        plan.game = MagicMock()
        plan.game.canonical_name = "Bully"
        plan.background_game = None

        # Mock _gather_context to return empty (no gameplay index needed)
        with patch.object(planner, "_gather_context", return_value={"gameplay_events": [], "compatibility": {}, "game_name": "Bully"}):
            result = planner.plan(session, plan, job_type="generate_short")

        assert result.success
        assert result.humor.enabled is False
        assert result.humor.intensity == HUMOR_INTENSITY_NONE
        assert "gemma3" in result.model.model

    def test_serious_video_uses_gemma_model(self):
        from gpcg.application.editorial_planner import EditorialPlanner

        llm = FakeLLMClient(planner_response=_make_plan(
            model="gemma3:12b",
            humor_enabled=False,
        ))
        planner = EditorialPlanner(llm=llm)

        session = MagicMock()
        plan = MagicMock()
        plan.topic = "Serious topic"
        plan.game_id = 1
        plan.fact_id = None
        plan.game = MagicMock()
        plan.game.canonical_name = "TestGame"
        plan.background_game = None
        plan.hook = ""
        plan.tone = "serious"
        plan.target_duration = 60

        with patch.object(planner, "_gather_context", return_value={"gameplay_events": [], "compatibility": {}, "game_name": "TestGame"}):
            result = planner.plan(session, plan)

        # Model should resolve to gemma3
        assert "gemma3" in result.model.model


# ── Test 2: Informal video (low humor) ───────────────────────────────────────


class TestInformalVideo:
    """Test 2 — informal video: low humor, qwen3 model."""

    def test_informal_video_enables_low_humor(self):
        from gpcg.application.editorial_planner import EditorialPlanner

        llm = FakeLLMClient(planner_response=_make_plan(
            humor_enabled=True,
            humor_intensity=HUMOR_INTENSITY_LOW,
            model="qwen3:14b",
        ))
        planner = EditorialPlanner(llm=llm)

        session = MagicMock()
        plan = MagicMock()
        plan.topic = "Funny Bully moments"
        plan.game_id = 1
        plan.fact_id = None
        plan.game = MagicMock()
        plan.game.canonical_name = "Bully"
        plan.background_game = None
        plan.hook = ""
        plan.tone = "humor"
        plan.target_duration = 60

        with patch.object(planner, "_gather_context", return_value={"gameplay_events": [], "compatibility": {}, "game_name": "Bully"}):
            result = planner.plan(session, plan)

        assert result.success
        assert result.humor.enabled is True
        assert result.humor.intensity == HUMOR_INTENSITY_LOW
        assert "qwen3" in result.model.model

    def test_low_humor_has_styles(self):
        from gpcg.application.editorial_planner import EditorialPlanner

        llm = FakeLLMClient(planner_response=_make_plan(
            humor_enabled=True,
            humor_intensity=HUMOR_INTENSITY_LOW,
        ))
        planner = EditorialPlanner(llm=llm)

        session = MagicMock()
        plan = MagicMock()
        plan.topic = "Test"
        plan.game_id = 1
        plan.fact_id = None
        plan.game = MagicMock()
        plan.game.canonical_name = "Test"
        plan.background_game = None
        plan.hook = ""
        plan.tone = "humor"
        plan.target_duration = 60

        with patch.object(planner, "_gather_context", return_value={"gameplay_events": [], "compatibility": {}, "game_name": "Test"}):
            result = planner.plan(session, plan)

        assert len(result.humor.styles) > 0
        # Low humor should favor observation/sarcasm, not absurdity
        assert "observation" in result.humor.styles


# ── Test 3: General topic video ──────────────────────────────────────────────


class TestGeneralTopicVideo:
    """Test 3 — general topic video: video_type=GENERAL_TOPIC."""

    def test_curiosity_short_is_general_topic(self):
        from gpcg.application.editorial_planner import EditorialPlanner

        llm = FakeLLMClient(planner_response=_make_plan(
            video_type=VIDEO_TYPE_GENERAL_TOPIC,
            gameplay_strategy="background_filler",
        ))
        planner = EditorialPlanner(llm=llm)

        session = MagicMock()
        plan = MagicMock()
        plan.topic = "Why the sky is blue"
        plan.game_id = None  # general curiosity
        plan.fact_id = None
        plan.game = None
        plan.background_game = MagicMock()
        plan.background_game.canonical_name = "Bully"
        plan.hook = ""
        plan.tone = "curiosity"
        plan.target_duration = 60

        with patch.object(planner, "_gather_context", return_value={"gameplay_events": [], "compatibility": {}, "game_name": "Bully"}):
            result = planner.plan(session, plan, job_type="curiosity_short", background_game_id=1)

        assert result.video_type == VIDEO_TYPE_GENERAL_TOPIC
        assert result.gameplay_strategy == "background_filler"


# ── Test 4: Game-related video ───────────────────────────────────────────────


class TestGameRelatedVideo:
    """Test 4 — game-related video: video_type=GAME_RELATED."""

    def test_generate_short_is_game_related(self):
        from gpcg.application.editorial_planner import EditorialPlanner

        llm = FakeLLMClient(planner_response=_make_plan(
            video_type=VIDEO_TYPE_GAME_RELATED,
            gameplay_strategy="related",
        ))
        planner = EditorialPlanner(llm=llm)

        session = MagicMock()
        plan = MagicMock()
        plan.topic = "Bully secrets"
        plan.game_id = 1
        plan.fact_id = None
        plan.game = MagicMock()
        plan.game.canonical_name = "Bully"
        plan.background_game = None
        plan.hook = ""
        plan.tone = "curiosity"
        plan.target_duration = 60

        with patch.object(planner, "_gather_context", return_value={"gameplay_events": [], "compatibility": {}, "game_name": "Bully"}):
            result = planner.plan(session, plan, job_type="generate_short")

        assert result.video_type == VIDEO_TYPE_GAME_RELATED
        assert result.gameplay_strategy == "related"


# ── Test 5: No humor → creative engine skipped ───────────────────────────────


class TestNoHumorSkipsCreativeEngine:
    """Test 5 — no humor: creative engine should be skipped."""

    def test_creative_engine_skipped_when_humor_disabled(self, monkeypatch):
        from gpcg.application.creative_engine import CreativeEngine
        from gpcg.config import get_settings

        # Enable creative engine for this test
        monkeypatch.setenv("GPCG_CREATIVE_ENGINE_ENABLED", "true")
        get_settings.cache_clear()

        llm = FakeLLMClient()
        engine = CreativeEngine(llm=llm)

        # When humor_plan has enabled=False, engine returns empty material
        humor_plan = HumorPlan.none()
        material = engine.generate_creative_material(
            topic="test", fact="test fact", humor_plan=humor_plan
        )

        assert not material.success
        assert "humor disabled" in material.error.lower() or "no_humor" in material.style

        get_settings.cache_clear()

    def test_creative_engine_runs_when_humor_enabled(self, monkeypatch):
        from gpcg.application.creative_engine import CreativeEngine
        from gpcg.config import get_settings

        # Enable creative engine for this test
        monkeypatch.setenv("GPCG_CREATIVE_ENGINE_ENABLED", "true")
        get_settings.cache_clear()

        llm = FakeLLMClient(script_response={
            "hooks": ["hook1"],
            "angles": ["angle1"],
            "punchlines": ["punch1"],
            "observations": ["obs1"],
        })
        engine = CreativeEngine(llm=llm)

        humor_plan = HumorPlan.low()
        material = engine.generate_creative_material(
            topic="test", fact="test fact", humor_plan=humor_plan
        )

        assert material.success
        assert len(material.hooks) > 0

        get_settings.cache_clear()


# ── Test 6: Script revision (critic flags bad humor) ─────────────────────────


class TestScriptRevision:
    """Test 6 — script critic flags bad humor, script is revised."""

    def test_critic_returns_revise_verdict(self):
        from gpcg.application.script_critic import ScriptCritic

        llm = FakeLLMClient(critic_response=_make_critic(
            verdict=CRITIC_VERDICT_REVISE,
            score=55.0,
            feedback="Remove the joke 'imagine um jogo onde' — it's a forced AI humor pattern.",
        ))
        critic = ScriptCritic(llm=llm)

        plan = VideoCreativePlan(humor=HumorPlan.low())
        review = critic.review("Script with bad joke.", plan)

        assert review.verdict == CRITIC_VERDICT_REVISE
        assert review.overall_score < 70
        assert "remove" in review.feedback.lower()

    def test_critic_returns_pass_verdict(self):
        from gpcg.application.script_critic import ScriptCritic

        llm = FakeLLMClient(critic_response=_make_critic(
            verdict=CRITIC_VERDICT_PASS,
            score=88.0,
        ))
        critic = ScriptCritic(llm=llm)

        plan = VideoCreativePlan()
        review = critic.review("Good natural script.", plan)

        assert review.verdict == CRITIC_VERDICT_PASS
        assert review.passed
        assert review.overall_score >= 70

    def test_should_revise_when_verdict_revise_and_under_max(self):
        from gpcg.application.script_critic import ScriptCritic

        critic = ScriptCritic(llm=FakeLLMClient())
        review = ScriptReview(verdict=CRITIC_VERDICT_REVISE, overall_score=55.0)

        assert critic.should_revise(review, current_revisions=0) is True
        assert critic.should_revise(review, current_revisions=1) is True

    def test_should_not_revise_when_verdict_pass(self):
        from gpcg.application.script_critic import ScriptCritic

        critic = ScriptCritic(llm=FakeLLMClient())
        review = ScriptReview(verdict=CRITIC_VERDICT_PASS, overall_score=88.0)

        assert critic.should_revise(review, current_revisions=0) is False

    def test_should_not_revise_when_at_max_revisions(self):
        from gpcg.application.script_critic import ScriptCritic

        critic = ScriptCritic(llm=FakeLLMClient())
        # Patch max_revisions to 2
        critic.settings.gpcg_script_critic_max_revisions = 2
        review = ScriptReview(verdict=CRITIC_VERDICT_REVISE, overall_score=55.0)

        assert critic.should_revise(review, current_revisions=2) is False
        assert critic.should_revise(review, current_revisions=1) is True


# ── Dataclass tests ──────────────────────────────────────────────────────────


class TestCreativePlanDataclasses:
    """Serialization and factory tests for creative plan dataclasses."""

    def test_humor_plan_none_factory(self):
        h = HumorPlan.none()
        assert h.enabled is False
        assert h.intensity == HUMOR_INTENSITY_NONE
        assert h.styles == []

    def test_humor_plan_low_factory(self):
        h = HumorPlan.low()
        assert h.enabled is True
        assert h.intensity == HUMOR_INTENSITY_LOW
        assert "observation" in h.styles

    def test_video_creative_plan_roundtrip(self):
        plan = VideoCreativePlan(
            video_type=VIDEO_TYPE_GAME_RELATED,
            central_idea="Test idea",
            humor=HumorPlan.low(),
        )
        d = plan.to_dict()
        plan2 = VideoCreativePlan.from_dict(d)
        assert plan2.video_type == plan.video_type
        assert plan2.central_idea == plan.central_idea
        assert plan2.humor.enabled == plan.humor.enabled

    def test_script_review_passed_property(self):
        review_pass = ScriptReview(verdict=CRITIC_VERDICT_PASS)
        review_revise = ScriptReview(verdict=CRITIC_VERDICT_REVISE)
        assert review_pass.passed is True
        assert review_revise.passed is False

    def test_script_review_has_high_issues(self):
        from gpcg.domain.creative_plan import CriticIssue
        review = ScriptReview(
            verdict=CRITIC_VERDICT_REVISE,
            issues=[
                CriticIssue(dimension="humor", severity="high", description="bad joke"),
                CriticIssue(dimension="structure", severity="low", description="minor issue"),
            ],
        )
        assert review.has_high_issues is True

    def test_video_creative_plan_empty_factory(self):
        plan = VideoCreativePlan.empty("test error")
        assert plan.success is False
        assert plan.error == "test error"
