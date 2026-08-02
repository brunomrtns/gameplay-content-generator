"""Tests for the V2 section-based Script Critic.

Tests:
  1 — review_sections returns a ScriptReview with per-section issues merged
  2 — _split_into_sections splits by narrative beats when available
  3 — _split_into_sections falls back to 3-section split without beats
  4 — _split_into_sections returns single section for short scripts
  5 — Falls back to holistic review when section_based=false
  6 — Disabled critic returns PASS
  7 — LLM error returns PASS (non-blocking)
  8 — Per-section issues are merged with location prefixed by section label
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from gpcg.application.script_critic import ScriptCritic
from gpcg.config import get_settings
from gpcg.domain.creative_plan import (
    CRITIC_VERDICT_PASS,
    CRITIC_VERDICT_REVISE,
    HumorPlan,
    NarrativeBeat,
    VideoCreativePlan,
)
from gpcg.infrastructure.llm import LLMError


# ── Fake LLM ─────────────────────────────────────────────────────────────────


class FakeLLMClient:
    def __init__(self, response: dict | None = None, error: bool = False):
        self.response = response or {}
        self.error = error
        self.calls: list[dict] = []

    def chat_json(self, system, prompt, model=None, temperature=0.7, max_tokens=2000):
        self.calls.append({"system": system, "prompt": prompt})
        if self.error:
            raise LLMError("fake LLM error")
        return self.response

    def vision_json(self, **kwargs):
        return {}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_plan(beats: list[NarrativeBeat] | None = None) -> VideoCreativePlan:
    return VideoCreativePlan(
        video_type="GAME_RELATED",
        central_idea="test central idea",
        narrative_beats=beats or [],
        tone=__import__("gpcg.domain.creative_plan", fromlist=["ToneWeights"]).ToneWeights(),
        humor=HumorPlan.low(),
        success=True,
    )


# ── Tests ────────────────────────────────────────────────────────────────────


def test_review_sections_returns_review_with_merged_issues():
    """review_sections returns a ScriptReview with per-section issues merged in."""
    plan = _make_plan()
    fake_llm = FakeLLMClient(response={
        "verdict": "REVISE",
        "overall_score": 60,
        "sections": [
            {"label": "hook", "text": "...", "scores": {"naturalness": 80},
             "issues": [{"dimension": "naturalness", "severity": "low",
                         "description": "generic hook", "suggestion": "be specific"}]},
            {"label": "payoff", "text": "...", "scores": {"naturalness": 40},
             "issues": [{"dimension": "coherence", "severity": "high",
                         "description": "doesn't deliver", "suggestion": "fix payoff"}]},
        ],
        "dimension_scores": {
            "structure": 60, "naturalness": 60, "humor": 70,
            "coherence": 50, "gameplay": 70, "factual_accuracy": 80,
        },
        "issues": [],
        "feedback": "Fix the payoff section.",
    })
    with patch.object(get_settings(), "gpcg_script_critic_enabled", True), \
         patch.object(get_settings(), "gpcg_script_critic_section_based", True):
        critic = ScriptCritic(llm=fake_llm)
        review = critic.review_sections("Test script.", plan)
        assert review.verdict == CRITIC_VERDICT_REVISE
        assert review.overall_score == 60.0
        # Per-section issues should be merged in (2 from sections)
        assert len(review.issues) == 2
        # Section issues should have location prefixed with section label
        locations = [i.location for i in review.issues]
        assert any("[hook]" in loc for loc in locations)
        assert any("[payoff]" in loc for loc in locations)


def test_split_into_sections_by_beats():
    """_split_into_sections aligns sections with narrative beats when available."""
    beats = [
        NarrativeBeat(label="hook", description="grab", content_type="observation"),
        NarrativeBeat(label="development", description="explore", content_type="fact"),
        NarrativeBeat(label="payoff", description="deliver", content_type="observation"),
    ]
    plan = _make_plan(beats=beats)
    script = "First sentence. Second sentence. Third sentence. Fourth. Fifth. Sixth."
    critic = ScriptCritic(llm=FakeLLMClient())
    sections = critic._split_into_sections(script, plan)
    assert len(sections) == 3
    assert sections[0]["label"] == "hook"
    assert sections[1]["label"] == "development"
    assert sections[2]["label"] == "payoff"


def test_split_into_sections_fallback_3_sections():
    """Without beats, splits into hook/development/payoff."""
    plan = _make_plan(beats=[])
    script = "First. Second. Third. Fourth. Fifth. Sixth. Seventh. Eighth. Ninth."
    critic = ScriptCritic(llm=FakeLLMClient())
    sections = critic._split_into_sections(script, plan)
    assert len(sections) == 3
    assert sections[0]["label"] == "hook"
    assert sections[1]["label"] == "development"
    assert sections[2]["label"] == "payoff"


def test_split_into_sections_short_script():
    """Short scripts (fewer than 3 sentences) return a single section."""
    plan = _make_plan(beats=[])
    script = "Only one sentence."
    critic = ScriptCritic(llm=FakeLLMClient())
    sections = critic._split_into_sections(script, plan)
    assert len(sections) == 1
    assert sections[0]["label"] == "full"


def test_falls_back_to_holistic_when_section_based_off():
    """When gpcg_script_critic_section_based=false, falls back to review()."""
    plan = _make_plan()
    fake_llm = FakeLLMClient(response={
        "verdict": "PASS", "overall_score": 85,
        "dimension_scores": {"structure": 80, "naturalness": 85, "humor": 80,
                             "coherence": 85, "gameplay": 85, "factual_accuracy": 90},
        "issues": [], "feedback": "",
    })
    with patch.object(get_settings(), "gpcg_script_critic_enabled", True), \
         patch.object(get_settings(), "gpcg_script_critic_section_based", False):
        critic = ScriptCritic(llm=fake_llm)
        review = critic.review_sections("Test script.", plan)
        assert review.verdict == CRITIC_VERDICT_PASS
        # Should have used the holistic CRITIC_SYSTEM, not SECTION_CRITIC_SYSTEM
        assert "SECTION-BASED" not in fake_llm.calls[0]["system"]


def test_disabled_critic_returns_pass():
    """When gpcg_script_critic_enabled=false, review_sections returns PASS."""
    plan = _make_plan()
    with patch.object(get_settings(), "gpcg_script_critic_enabled", False):
        critic = ScriptCritic(llm=FakeLLMClient())
        review = critic.review_sections("Test.", plan)
        assert review.verdict == CRITIC_VERDICT_PASS
        assert review.overall_score == 100.0


def test_llm_error_returns_pass_non_blocking():
    """When the LLM fails, review_sections returns PASS (non-blocking)."""
    plan = _make_plan()
    fake_llm = FakeLLMClient(error=True)
    with patch.object(get_settings(), "gpcg_script_critic_enabled", True), \
         patch.object(get_settings(), "gpcg_script_critic_section_based", True):
        critic = ScriptCritic(llm=fake_llm)
        review = critic.review_sections("Test.", plan)
        assert review.verdict == CRITIC_VERDICT_PASS
        assert "section critic failed" in review.feedback


def test_section_prompt_includes_sections_and_source_fact():
    """The section-based prompt includes section labels and the source fact."""
    plan = _make_plan()
    fake_llm = FakeLLMClient(response={
        "verdict": "PASS", "overall_score": 80,
        "dimension_scores": {"structure": 80, "naturalness": 80, "humor": 80,
                             "coherence": 80, "gameplay": 80, "factual_accuracy": 80},
        "sections": [], "issues": [], "feedback": "",
    })
    with patch.object(get_settings(), "gpcg_script_critic_enabled", True), \
         patch.object(get_settings(), "gpcg_script_critic_section_based", True):
        critic = ScriptCritic(llm=fake_llm)
        critic.review_sections("Test script.", plan, source_fact="The source fact.")
        prompt = fake_llm.calls[0]["prompt"]
        assert "SOURCE FACT" in prompt
        assert "The source fact." in prompt
        assert "SCRIPT SECTIONS" in prompt
