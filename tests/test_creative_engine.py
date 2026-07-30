"""Tests for the CreativeEngine (Qwen3-14B creative layer).

These tests use a fake LLM client to avoid requiring the real Qwen3-14B
model. The same pattern (inject a mock LLMClient) is used across the
codebase for testing LLM-dependent services.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from gpcg.application.creative_engine import (
    CREATIVE_PRESETS,
    CreativeEngine,
    CreativeEngineError,
    CreativeMaterial,
    CreativeStyle,
    get_style,
)
from gpcg.infrastructure.llm import LLMError


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _enable_creative_engine(monkeypatch):
    """Most tests need the engine enabled. Disable per-test when needed."""
    monkeypatch.setenv("GPCG_CREATIVE_ENGINE_ENABLED", "true")
    monkeypatch.setenv("GPCG_CREATIVE_ENGINE_FALLBACK", "true")
    monkeypatch.setenv("GPCG_CREATIVE_ENGINE_MODEL", "qwen3:14b")
    monkeypatch.setenv("GPCG_CREATIVE_ENGINE_STYLE", "humor")
    from gpcg.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class FakeLLM:
    """A fake LLMClient that returns a canned chat_json response."""

    def __init__(self, response: dict | list | None = None, error: str | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def chat_json(self, system, prompt, *, model=None, temperature=0.5, max_tokens=2048):
        self.calls.append(
            {"system": system, "prompt": prompt, "model": model,
             "temperature": temperature, "max_tokens": max_tokens}
        )
        if self.error:
            raise LLMError(self.error)
        if self.response is None:
            return {}
        return self.response


GOOD_RESPONSE = {
    "hooks": ["Hook 1", "Hook 2", "Hook 3", "Hook 4", "Hook 5"],
    "angles": ["Ângulo 1", "Ângulo 2", "Ângulo 3", "Ângulo 4", "Ângulo 5"],
    "punchlines": ["Punch 1", "Punch 2", "Punch 3", "Punch 4", "Punch 5"],
    "observations": ["Obs 1", "Obs 2", "Obs 3", "Obs 4", "Obs 5"],
}


# ─────────────────────────────────────────────────────────────────────────────
# Style presets
# ─────────────────────────────────────────────────────────────────────────────


class TestStylePresets:
    def test_all_presets_present(self):
        expected = {
            "humor", "absurd", "sarcastic", "storytelling", "curiosity",
            "nostalgia", "dark_humor", "high_energy",
        }
        assert expected.issubset(set(CREATIVE_PRESETS.keys()))

    def test_get_style_known(self):
        s = get_style("humor")
        assert s.name == "humor"
        assert 0.0 <= s.energy <= 1.0
        assert 0.0 <= s.absurdity <= 1.0

    def test_get_style_unknown_falls_back_to_humor(self):
        s = get_style("nonexistent_style")
        assert s.name == "humor"

    def test_style_is_frozen_dataclass(self):
        s = get_style("absurd")
        with pytest.raises((AttributeError, Exception)):
            s.energy = 0.5  # frozen dataclass


# ─────────────────────────────────────────────────────────────────────────────
# CreativeEngine — initialization & disabled state
# ─────────────────────────────────────────────────────────────────────────────


class TestCreativeEngineInit:
    def test_disabled_returns_empty(self, monkeypatch):
        monkeypatch.setenv("GPCG_CREATIVE_ENGINE_ENABLED", "false")
        from gpcg.config import get_settings
        get_settings.cache_clear()

        engine = CreativeEngine(llm=FakeLLM(response=GOOD_RESPONSE))
        m = engine.generate_creative_material(topic="Bully", fact="fact")
        assert m.success is False
        assert m.error == "creative engine disabled"
        assert m.hooks == []

    def test_uses_configured_model(self):
        fake = FakeLLM(response=GOOD_RESPONSE)
        engine = CreativeEngine(llm=fake)
        engine.generate_creative_material(topic="t", fact="f")
        assert fake.calls[0]["model"] == "qwen3:14b"

    def test_uses_configured_temperature(self, monkeypatch):
        monkeypatch.setenv("GPCG_CREATIVE_ENGINE_TEMPERATURE", "0.42")
        from gpcg.config import get_settings
        get_settings.cache_clear()

        fake = FakeLLM(response=GOOD_RESPONSE)
        engine = CreativeEngine(llm=fake)
        engine.generate_creative_material(topic="t", fact="f")
        assert abs(fake.calls[0]["temperature"] - 0.42) < 0.01


# ─────────────────────────────────────────────────────────────────────────────
# CreativeEngine — happy path
# ─────────────────────────────────────────────────────────────────────────────


class TestCreativeEngineHappyPath:
    def test_generates_all_fields(self):
        engine = CreativeEngine(llm=FakeLLM(response=GOOD_RESPONSE))
        m = engine.generate_creative_material(topic="Bully", fact="banhos de privada")
        assert m.success is True
        assert len(m.hooks) == 5
        assert len(m.angles) == 5
        assert len(m.punchlines) == 5
        assert len(m.observations) == 5
        assert m.style == "humor"
        assert m.model == "qwen3:14b"
        assert m.latency_ms >= 0

    def test_granular_helpers(self):
        engine = CreativeEngine(llm=FakeLLM(response=GOOD_RESPONSE))
        hooks = engine.generate_hooks(topic="t", fact="f")
        assert hooks == GOOD_RESPONSE["hooks"]
        angles = engine.generate_angles(topic="t", fact="f")
        assert angles == GOOD_RESPONSE["angles"]
        punchlines = engine.generate_punchlines(topic="t", fact="f")
        assert punchlines == GOOD_RESPONSE["punchlines"]

    def test_style_override(self):
        fake = FakeLLM(response=GOOD_RESPONSE)
        engine = CreativeEngine(llm=fake)
        engine.generate_creative_material(
            topic="t", fact="f", style=get_style("absurd")
        )
        # The system prompt should mention the absurd style description
        assert "absurdo" in fake.calls[0]["system"].lower() or "extremo" in fake.calls[0]["system"].lower()

    def test_to_dict_roundtrip(self):
        engine = CreativeEngine(llm=FakeLLM(response=GOOD_RESPONSE))
        m = engine.generate_creative_material(topic="t", fact="f")
        d = m.to_dict()
        assert d["hooks"] == GOOD_RESPONSE["hooks"]
        assert d["success"] is True
        # Reconstruct
        m2 = CreativeMaterial(**{k: d[k] for k in [
            "hooks", "angles", "punchlines", "observations",
            "style", "model", "latency_ms", "success", "error"
        ]})
        assert m2.hooks == m.hooks


# ─────────────────────────────────────────────────────────────────────────────
# CreativeEngine — fallback & errors
# ─────────────────────────────────────────────────────────────────────────────


class TestCreativeEngineFallback:
    def test_llm_error_with_fallback(self):
        engine = CreativeEngine(llm=FakeLLM(error="connection refused"))
        m = engine.generate_creative_material(topic="t", fact="f")
        assert m.success is False
        assert "connection refused" in m.error
        assert m.hooks == []

    def test_llm_error_without_fallback_raises(self, monkeypatch):
        monkeypatch.setenv("GPCG_CREATIVE_ENGINE_FALLBACK", "false")
        from gpcg.config import get_settings
        get_settings.cache_clear()

        engine = CreativeEngine(llm=FakeLLM(error="oom"))
        with pytest.raises(CreativeEngineError):
            engine.generate_creative_material(topic="t", fact="f")

    def test_empty_response_with_fallback(self):
        engine = CreativeEngine(llm=FakeLLM(response={}))
        m = engine.generate_creative_material(topic="t", fact="f")
        assert m.success is False
        assert "empty" in m.error.lower()

    def test_all_fields_empty_with_fallback(self):
        engine = CreativeEngine(llm=FakeLLM(response={"hooks": [], "angles": []}))
        m = engine.generate_creative_material(topic="t", fact="f")
        assert m.success is False

    def test_non_dict_response(self):
        engine = CreativeEngine(llm=FakeLLM(response=["not", "a", "dict"]))
        m = engine.generate_creative_material(topic="t", fact="f")
        assert m.success is False


# ─────────────────────────────────────────────────────────────────────────────
# CreativeEngine — robust parsing
# ─────────────────────────────────────────────────────────────────────────────


class TestCreativeEngineParsing:
    def test_partial_fields(self):
        response = {"hooks": ["h1"], "angles": [], "punchlines": ["p1"], "observations": []}
        engine = CreativeEngine(llm=FakeLLM(response=response))
        m = engine.generate_creative_material(topic="t", fact="f")
        assert m.success is True
        assert m.hooks == ["h1"]
        assert m.punchlines == ["p1"]
        assert m.angles == []
        assert m.observations == []

    def test_non_string_items_coerced(self):
        response = {"hooks": [1, 2, "real"], "angles": [], "punchlines": [], "observations": []}
        engine = CreativeEngine(llm=FakeLLM(response=response))
        m = engine.generate_creative_material(topic="t", fact="f")
        assert m.success is True
        assert m.hooks == ["1", "2", "real"]

    def test_single_string_instead_of_list(self):
        response = {"hooks": "only one hook", "angles": [], "punchlines": [], "observations": []}
        engine = CreativeEngine(llm=FakeLLM(response=response))
        m = engine.generate_creative_material(topic="t", fact="f")
        assert m.success is True
        assert m.hooks == ["only one hook"]

    def test_whitespace_items_stripped(self):
        response = {"hooks": ["  spaced  ", "", "good"], "angles": [], "punchlines": [], "observations": []}
        engine = CreativeEngine(llm=FakeLLM(response=response))
        m = engine.generate_creative_material(topic="t", fact="f")
        assert m.hooks == ["spaced", "good"]


# ─────────────────────────────────────────────────────────────────────────────
# CreativeEngine — prompt construction
# ─────────────────────────────────────────────────────────────────────────────


class TestPromptConstruction:
    def test_user_prompt_contains_topic_and_fact(self):
        fake = FakeLLM(response=GOOD_RESPONSE)
        engine = CreativeEngine(llm=fake)
        engine.generate_creative_material(topic="GTA San Andreas", fact="Bigfoot exists")
        prompt = fake.calls[0]["prompt"]
        assert "GTA San Andreas" in prompt
        assert "Bigfoot exists" in prompt

    def test_user_prompt_contains_context(self):
        fake = FakeLLM(response=GOOD_RESPONSE)
        engine = CreativeEngine(llm=fake)
        engine.generate_creative_material(topic="t", fact="f", context="Game: Bully")
        prompt = fake.calls[0]["prompt"]
        assert "Bully" in prompt

    def test_system_prompt_contains_style_description(self):
        fake = FakeLLM(response=GOOD_RESPONSE)
        engine = CreativeEngine(llm=fake)
        engine.generate_creative_material(topic="t", fact="f", style=get_style("nostalgia"))
        system = fake.calls[0]["system"]
        assert "saudade" in system.lower() or "nostalgia" in system.lower()

    def test_system_prompt_avoids_cliches(self):
        fake = FakeLLM(response=GOOD_RESPONSE)
        engine = CreativeEngine(llm=fake)
        engine.generate_creative_material(topic="t", fact="f")
        system = fake.calls[0]["system"]
        # The "Creative Bible" should explicitly forbid these cliches
        assert "você sabia que" in system.lower()
        assert "incrível, não é" in system.lower()


# ─────────────────────────────────────────────────────────────────────────────
# CreativeMaterial dataclass
# ─────────────────────────────────────────────────────────────────────────────


class TestCreativeMaterial:
    def test_empty_factory(self):
        m = CreativeMaterial.empty(style="humor", error="test")
        assert m.success is False
        assert m.error == "test"
        assert m.hooks == []
        assert m.style == "humor"

    def test_summary_string(self):
        m = CreativeMaterial(hooks=["a"], angles=["b"], punchlines=["c"], observations=["d"], style="humor")
        s = m.summary()
        assert "hooks=1" in s
        assert "angles=1" in s
        assert "humor" in s

    def test_to_dict_has_all_fields(self):
        m = CreativeMaterial(hooks=["a"], style="humor", model="qwen3:14b", latency_ms=100)
        d = m.to_dict()
        assert set(d.keys()) == {
            "hooks", "angles", "punchlines", "observations",
            "style", "model", "latency_ms", "success", "error"
        }


# ─────────────────────────────────────────────────────────────────────────────
# Integration: ScriptService + CreativeMaterial
# ─────────────────────────────────────────────────────────────────────────────


class TestScriptServiceIntegration:
    """Verify that ScriptService accepts creative_material and formats it
    into the draft prompt. We test the formatter directly to avoid needing
    a full DB + LLM setup."""

    def test_format_creative_material_adds_section(self):
        from gpcg.application.script_service import ScriptService

        svc = ScriptService()
        m = CreativeMaterial(
            hooks=["hook1"], angles=["angle1"], punchlines=["punch1"],
            observations=["obs1"], style="humor", success=True,
        )
        formatted = svc._format_creative_material(m)
        assert "MATERIAL CRIATIVO" in formatted
        assert "hook1" in formatted
        assert "angle1" in formatted
        assert "punch1" in formatted
        assert "obs1" in formatted

    def test_format_creative_material_empty_lists(self):
        from gpcg.application.script_service import ScriptService

        svc = ScriptService()
        m = CreativeMaterial(success=True)
        formatted = svc._format_creative_material(m)
        assert "MATERIAL CRIATIVO" in formatted
        assert "(nenhum)" in formatted
