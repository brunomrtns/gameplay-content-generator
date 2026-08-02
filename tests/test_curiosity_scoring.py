"""Tests for the V2 curiosity scoring module.

Tests:
  1 — compute_curiosity_score: weighted mean of 5 editorial sub-scores
  2 — visual_potential is excluded from the weighted mean
  3 — sub-scores are clamped to 0-100
  4 — CuriosityScorer.score_facts persists curiosity_score + subscores
  5 — CuriosityScorer.score_single returns a CuriosityScore
  6 — Disabled flag returns 0 / empty
  7 — LLM error returns empty result (no raise)
  8 — content_planning ranks by curiosity_score when enabled
"""

from __future__ import annotations

import os
from typing import Optional
from unittest.mock import patch

import pytest

from gpcg.application.curiosity_scorer import (
    ALL_SUBSCORES,
    CuriosityScore,
    CuriosityScorer,
    compute_curiosity_score,
)
from gpcg.config import get_settings
from gpcg.domain.models import Fact, Game
from gpcg.infrastructure.database import session_scope
from gpcg.infrastructure.llm import LLMError


# ── Fake LLM ─────────────────────────────────────────────────────────────────


class FakeLLMClient:
    """Fake LLM that returns scripted curiosity scoring responses."""

    def __init__(self, scores_response: Optional[dict] = None, error: bool = False):
        self.scores_response = scores_response or {}
        self.error = error
        self.calls: list[dict] = []

    def chat_json(self, system, prompt, model=None, temperature=0.7, max_tokens=2000):
        self.calls.append({"system": system, "prompt": prompt, "model": model})
        if self.error:
            raise LLMError("fake LLM error")
        return self.scores_response

    def vision_json(self, **kwargs):
        return {}


# ── Tests ────────────────────────────────────────────────────────────────────


def test_compute_curiosity_score_weighted_mean():
    """curiosity_score = gap*0.30 + surprise*0.25 + retention*0.20 + fam*0.15 + insight*0.10"""
    subscores = {
        "curiosity_gap": 80,
        "surprise_potential": 70,
        "retention_potential": 60,
        "familiarity": 50,
        "insight_quality": 40,
    }
    # 80*0.30 + 70*0.25 + 60*0.20 + 50*0.15 + 40*0.10
    # = 24 + 17.5 + 12 + 7.5 + 4 = 65.0
    assert compute_curiosity_score(subscores) == pytest.approx(65.0)


def test_visual_potential_excluded_from_weighted_mean():
    """visual_potential is a technical signal, NOT in the editorial weighted mean."""
    subscores = {
        "curiosity_gap": 100,
        "surprise_potential": 100,
        "retention_potential": 100,
        "familiarity": 100,
        "insight_quality": 100,
        "visual_potential": 0,  # should NOT lower the score
    }
    assert compute_curiosity_score(subscores) == pytest.approx(100.0)


def test_subscores_clamped_to_0_100():
    """Sub-scores out of range are clamped by _normalize_subscores."""
    from gpcg.application.curiosity_scorer import _normalize_subscores
    raw = {
        "curiosity_gap": 150,  # over
        "surprise_potential": -20,  # under
        "retention_potential": 50,
        "familiarity": 50,
        "insight_quality": 50,
        "visual_potential": 200,  # over
    }
    subscores = _normalize_subscores(raw)
    assert subscores["curiosity_gap"] == 100.0
    assert subscores["surprise_potential"] == 0.0
    assert subscores["visual_potential"] == 100.0
    # After clamp: 100*0.30 + 0*0.25 + 50*0.20 + 50*0.15 + 50*0.10
    # = 30 + 0 + 10 + 7.5 + 5 = 52.5
    assert compute_curiosity_score(subscores) == pytest.approx(52.5)


def test_score_facts_persists_curiosity_score():
    """CuriosityScorer.score_facts writes curiosity_score + subscores to Fact rows."""
    with session_scope() as session:
        game = Game(canonical_name="TestGame Curiosity", user_id=None)
        session.add(game)
        session.flush()
        fact = Fact(
            game_id=game.id, claim="Test curiosity claim for scoring",
            category="curiosity", quality_score=80, novelty_score=70,
            curiosity_score=0.0,  # unscored
        )
        session.add(fact)
        session.flush()
        fact_id = fact.id

    fake_llm = FakeLLMClient(scores_response={
        "scores": [{
            "id": fact_id,
            "curiosity_gap": 90,
            "surprise_potential": 80,
            "retention_potential": 70,
            "familiarity": 60,
            "insight_quality": 50,
            "visual_potential": 85,
        }]
    })

    with patch.object(get_settings(), "gpcg_curiosity_scoring_enabled", True):
        scorer = CuriosityScorer(llm=fake_llm)
        with session_scope() as session:
            n = scorer.score_facts(session, game.id, llm=fake_llm)
            assert n == 1
            fact = session.get(Fact, fact_id)
            assert fact.curiosity_score > 0
            assert "curiosity_gap" in fact.curiosity_subscores
            assert fact.curiosity_subscores["visual_potential"] == 85.0


def test_score_single_returns_curiosity_score():
    """CuriosityScorer.score_single returns a CuriosityScore without persisting."""
    fact = Fact(id=1, claim="Test", category="curiosity")
    fake_llm = FakeLLMClient(scores_response={
        "scores": [{
            "id": 1,
            "curiosity_gap": 100,
            "surprise_potential": 100,
            "retention_potential": 100,
            "familiarity": 100,
            "insight_quality": 100,
            "visual_potential": 50,
        }]
    })
    with patch.object(get_settings(), "gpcg_curiosity_scoring_enabled", True):
        scorer = CuriosityScorer(llm=fake_llm)
        result = scorer.score_single(fact, game_name="TestGame", llm=fake_llm)
        assert result.success
        assert result.curiosity_score == pytest.approx(100.0)
        assert result.subscores["visual_potential"] == 50.0


def test_disabled_flag_returns_zero():
    """When GPCG_CURIOSITY_SCORING_ENABLED=false, score_facts returns 0."""
    with patch.object(get_settings(), "gpcg_curiosity_scoring_enabled", False):
        scorer = CuriosityScorer(llm=FakeLLMClient())
        with session_scope() as session:
            n = scorer.score_facts(session, None, llm=FakeLLMClient())
            assert n == 0


def test_llm_error_returns_empty_no_raise():
    """When the LLM fails, score_single returns an empty CuriosityScore (no raise)."""
    fact = Fact(id=1, claim="Test", category="curiosity")
    fake_llm = FakeLLMClient(error=True)
    with patch.object(get_settings(), "gpcg_curiosity_scoring_enabled", True):
        scorer = CuriosityScorer(llm=fake_llm)
        result = scorer.score_single(fact, game_name="TestGame", llm=fake_llm)
        assert not result.success
        assert "LLM error" in result.error


def test_all_subscores_set_contains_expected_keys():
    """ALL_SUBSCORES = 5 editorial + 1 technical = 6 keys."""
    assert ALL_SUBSCORES == {
        "curiosity_gap", "surprise_potential", "retention_potential",
        "familiarity", "insight_quality", "visual_potential",
    }


def test_curiosity_score_dataclass_roundtrip():
    """CuriosityScore.to_dict / from_dict roundtrip."""
    cs = CuriosityScore(
        curiosity_score=72.5,
        subscores={"curiosity_gap": 80, "surprise_potential": 70},
        latency_ms=120,
    )
    d = cs.to_dict()
    cs2 = CuriosityScore.from_dict(d)
    assert cs2.curiosity_score == cs.curiosity_score
    assert cs2.subscores == cs.subscores
    assert cs2.latency_ms == cs.latency_ms
