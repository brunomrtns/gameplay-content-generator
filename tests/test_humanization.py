"""Tests for the V2 Humanization module.

Tests:
  1 — detect_ai_patterns finds AI-ism phrases
  2 — detect_ai_patterns finds redundancy markers
  3 — detect_ai_patterns finds repetitive sentence structures
  4 — detect_ai_patterns detects uniform rhythm
  5 — detect_ai_patterns flags missing ignorance identification
  6 — detect_ai_patterns returns empty for clean script
  7 — Humanizer.humanize returns humanized script + changes
  8 — Humanizer skips LLM pass when no issues detected
  9 — Disabled flag returns original unchanged
 10 — LLM error returns original unchanged (no raise)
 11 — HumanizationResult dataclass roundtrip
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from gpcg.application.humanization import (
    HumanizationIssue,
    HumanizationResult,
    Humanizer,
    detect_ai_patterns,
)
from gpcg.config import get_settings
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


# ── Tests: detect_ai_patterns (regex) ────────────────────────────────────────


def test_detect_ai_ism_phrases():
    """detect_ai_patterns finds AI-ism phrases."""
    script = "Você não vai acreditar no que descobri. E é aí que as coisas ficam interessantes."
    issues = detect_ai_patterns(script)
    ai_isms = [i for i in issues if i.pattern_type == "ai_ism"]
    assert len(ai_isms) >= 2
    assert any("você não vai acreditar" in i.match.lower() for i in ai_isms)


def test_detect_redundancy_markers():
    """detect_ai_patterns finds redundancy markers."""
    script = "O jogo tem 50 níveis. Ou seja, são muitas fases. Em outras palavras, é grande."
    issues = detect_ai_patterns(script)
    redundancies = [i for i in issues if i.pattern_type == "redundancy"]
    assert len(redundancies) >= 2


def test_detect_repetitive_structure():
    """detect_ai_patterns flags 3+ sentences starting with the same word."""
    script = "O jogo é legal. O jogo é difícil. O jogo é longo. O jogo é divertido."
    issues = detect_ai_patterns(script)
    repetitive = [i for i in issues if i.pattern_type == "repetitive_structure"]
    assert len(repetitive) >= 1
    assert "o" in repetitive[0].match.lower()


def test_detect_uniform_rhythm():
    """detect_ai_patterns flags when all sentences have similar length."""
    # 4 sentences all ~30 chars
    script = "O jogo tem muitos níveis mesmo. O jogo tem muitos chefes também. O jogo tem muitos segredos. O jogo tem muitos modos."
    issues = detect_ai_patterns(script)
    rhythm = [i for i in issues if i.pattern_type == "uniform_rhythm"]
    assert len(rhythm) >= 1


def test_detect_missing_ignorance_identification():
    """detect_ai_patterns flags when no ignorance identification is present."""
    script = "O jogo tem 50 níveis. Cada um com um chefão. É bem desafiador."
    issues = detect_ai_patterns(script)
    missing = [i for i in issues if i.pattern_type == "missing_ignorance_identification"]
    assert len(missing) == 1


def test_detect_clean_script_returns_few_issues():
    """A clean, varied script with ignorance ID produces fewer issues."""
    script = (
        "Demorei pra entender isso, mas vale a pena. "
        "O jogo esconde um segredo. "
        "Ninguém fala sobre ele. "
        "Mas está lá, no nível 3, esperando. "
        "Eu também não sabia até semana passada."
    )
    issues = detect_ai_patterns(script)
    # Should NOT have ai_ism, redundancy, or missing_ignorance_identification
    assert not any(i.pattern_type == "ai_ism" for i in issues)
    assert not any(i.pattern_type == "redundancy" for i in issues)
    assert not any(i.pattern_type == "missing_ignorance_identification" for i in issues)


# ── Tests: Humanizer ─────────────────────────────────────────────────────────


def test_humanize_returns_humanized_script():
    """Humanizer.humanize returns a humanized script with changes."""
    script = "Você não vai acreditar. O jogo tem 50 níveis. Ou seja, é grande."
    fake_llm = FakeLLMClient(response={
        "script": "O jogo tem 50 níveis. É grande.",
        "changes": ["removi 'você não vai acreditar'", "removi 'ou seja'"],
    })
    with patch.object(get_settings(), "gpcg_humanization_enabled", True):
        humanizer = Humanizer(llm=fake_llm)
        result = humanizer.humanize(script)
        assert result.success
        assert result.humanized == "O jogo tem 50 níveis. É grande."
        assert len(result.changes) == 2
        assert len(result.detected_issues) > 0


def test_humanize_skips_llm_when_no_issues():
    """When no AI patterns are detected, the LLM pass is skipped."""
    script = "Demorei pra entender isso. O jogo esconde um segredo. Ninguém fala sobre ele. Mas está lá."
    fake_llm = FakeLLMClient()
    with patch.object(get_settings(), "gpcg_humanization_enabled", True):
        humanizer = Humanizer(llm=fake_llm)
        result = humanizer.humanize(script)
        assert result.success
        assert result.humanized == script  # unchanged
        assert len(fake_llm.calls) == 0  # no LLM call


def test_disabled_flag_returns_original():
    """When GPCG_HUMANIZATION_ENABLED=false, returns original unchanged."""
    script = "Você não vai acreditar. Ou seja, é incrível."
    with patch.object(get_settings(), "gpcg_humanization_enabled", False):
        humanizer = Humanizer(llm=FakeLLMClient())
        result = humanizer.humanize(script)
        assert result.humanized == script
        assert "disabled" in result.error


def test_llm_error_returns_original_no_raise():
    """When the LLM fails, humanize returns the original script (no raise)."""
    script = "Você não vai acreditar. O jogo tem 50 níveis."
    fake_llm = FakeLLMClient(error=True)
    with patch.object(get_settings(), "gpcg_humanization_enabled", True):
        humanizer = Humanizer(llm=fake_llm)
        result = humanizer.humanize(script)
        assert not result.success
        assert "LLM error" in result.error
        assert result.humanized == script  # original kept


def test_humanization_result_roundtrip():
    """HumanizationResult.to_dict / from_dict roundtrip."""
    result = HumanizationResult(
        original="original text",
        humanized="humanized text",
        changes=["change 1", "change 2"],
        detected_issues=[HumanizationIssue(
            pattern_type="ai_ism", match="você não vai acreditar",
            location="char 0", suggestion="remove it",
        )],
        latency_ms=150,
    )
    d = result.to_dict()
    result2 = HumanizationResult.from_dict(d)
    assert result2.original == result.original
    assert result2.humanized == result.humanized
    assert result2.changes == result.changes
    assert len(result2.detected_issues) == 1
    assert result2.detected_issues[0].pattern_type == "ai_ism"
    assert result2.latency_ms == 150


def test_humanize_empty_llm_response_keeps_original():
    """When the LLM returns an empty script, the original is kept."""
    script = "Você não vai acreditar. O jogo tem 50 níveis."
    fake_llm = FakeLLMClient(response={"script": "", "changes": []})
    with patch.object(get_settings(), "gpcg_humanization_enabled", True):
        humanizer = Humanizer(llm=fake_llm)
        result = humanizer.humanize(script)
        assert result.success
        assert result.humanized == script  # original kept
