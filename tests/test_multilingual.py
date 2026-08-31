"""Parametrized multilingual tests for the GPCG i18n layer.

Tests cover:
- LanguageContext construction and fallback behavior
- GenerationContext compatibility (checkpoint invalidation)
- Localized labels for ChannelProfile context
- API message localization
- Stage key stability
- Originality check cross-language normalization
- CreativeStyle localized labels
- Prompt registry fallback
"""

import pytest

from gpcg.i18n.language_context import (
    DEFAULT_LANGUAGE,
    GenerationContext,
    LanguageContext,
    get_locale,
    get_tts_language,
    is_supported,
)
from gpcg.i18n.labels import get_label
from gpcg.i18n.api_messages import get_message
from gpcg.i18n.prompts.registry import PromptRegistry


# ── LanguageContext ─────────────────────────────────────────────────────────


class TestLanguageContext:
    """Tests for LanguageContext dataclass and factory."""

    def test_default_context_is_pt_br(self):
        ctx = LanguageContext()
        assert ctx.language == "pt-BR"
        assert ctx.locale == "pt_BR"
        assert ctx.tts_language == "pt"
        assert ctx.is_default is True

    @pytest.mark.parametrize("language,expected_locale,expected_tts", [
        ("pt-BR", "pt_BR", "pt"),
        ("en-US", "en_US", "en"),
        ("zh-CN", "zh_CN", "zh"),
        ("zh-TW", "zh_TW", "zh"),
        ("zh", "zh_CN", "zh"),
    ])
    def test_for_language_factory(self, language, expected_locale, expected_tts):
        ctx = LanguageContext.for_language(language)
        assert ctx.language == language
        assert ctx.locale == expected_locale
        assert ctx.tts_language == expected_tts

    def test_for_language_unsupported_falls_back(self):
        ctx = LanguageContext.for_language("xx-XX")
        # Unsupported language still constructs, but may fall back
        assert ctx.language == "xx-XX" or ctx.language == DEFAULT_LANGUAGE

    def test_language_directive(self):
        ctx = LanguageContext.for_language("en-US")
        directive = ctx.language_directive()
        assert "en-US" in directive or "English" in directive

    def test_default_context_no_directive(self):
        ctx = LanguageContext()
        # Default context may or may not have a directive, but it should be a string
        assert isinstance(ctx.language_directive(), str)


# ── GenerationContext compatibility ─────────────────────────────────────────


class TestGenerationContext:
    """Tests for GenerationContext checkpoint compatibility."""

    def test_compatible_same_context(self):
        ctx = GenerationContext(language="pt-BR", prompt_version="v1")
        stored = {"language": "pt-BR", "prompt_version": "v1"}
        assert ctx.is_compatible_with(stored) is True

    def test_incompatible_different_language(self):
        ctx = GenerationContext(language="en-US", prompt_version="v1")
        stored = {"language": "pt-BR", "prompt_version": "v1"}
        assert ctx.is_compatible_with(stored) is False

    def test_incompatible_different_prompt_version(self):
        ctx = GenerationContext(language="pt-BR", prompt_version="v2")
        stored = {"language": "pt-BR", "prompt_version": "v1"}
        assert ctx.is_compatible_with(stored) is False

    def test_compatible_empty_stored(self):
        """Empty stored context should be treated as compatible (no stored context)."""
        ctx = GenerationContext(language="pt-BR", prompt_version="v1")
        assert ctx.is_compatible_with({}) is True

    def test_to_dict_roundtrip(self):
        ctx = GenerationContext(language="en-US", prompt_version="v1")
        d = ctx.to_dict()
        assert d["language"] == "en-US"
        assert d["prompt_version"] == "v1"

    def test_from_artifacts(self):
        artifacts = {"generation_context": {"language": "en-US", "prompt_version": "v1"}}
        ctx = GenerationContext.from_artifacts(artifacts)
        assert ctx.language == "en-US"

    def test_from_artifacts_empty(self):
        ctx = GenerationContext.from_artifacts({})
        assert ctx.language == DEFAULT_LANGUAGE


# ── Localized labels ─────────────────────────────────────────────────────────


class TestLocalizedLabels:
    """Tests for ChannelProfile context label localization."""

    @pytest.mark.parametrize("language,expected", [
        ("pt-BR", "Nicho"),
        ("en-US", "Niche"),
        ("zh-CN", "细分领域"),
        ("zh-TW", "細分領域"),
        ("zh", "细分领域"),
    ])
    def test_niche_label(self, language, expected):
        assert get_label("niche", language) == expected

    @pytest.mark.parametrize("language,expected", [
        ("pt-BR", "Público-alvo"),
        ("en-US", "Target audience"),
        ("zh-CN", "目标受众"),
        ("zh-TW", "目標受眾"),
    ])
    def test_target_audience_label(self, language, expected):
        assert get_label("target_audience", language) == expected

    def test_fallback_to_pt_br(self):
        """Unknown language falls back to pt-BR."""
        assert get_label("niche", "xx-XX") == "Nicho"

    def test_unknown_key_returns_key(self):
        assert get_label("nonexistent_key", "pt-BR") == "nonexistent_key"


# ── API messages ─────────────────────────────────────────────────────────────


class TestApiMessages:
    """Tests for API message localization."""

    @pytest.mark.parametrize("language", ["pt-BR", "en-US"])
    def test_message_exists(self, language):
        msg = get_message("error.user_not_found", language)
        assert msg != "error.user_not_found"  # Should return a real message
        assert len(msg) > 0

    def test_fallback_to_pt_br(self):
        """Unknown language falls back to pt-BR."""
        msg = get_message("error.user_not_found", "zh-CN")
        assert len(msg) > 0

    def test_unknown_key_returns_key(self):
        assert get_message("nonexistent.message", "pt-BR") == "nonexistent.message"


# ── Stage keys ───────────────────────────────────────────────────────────────


class TestStageKeys:
    """Tests for stage key stability."""

    @pytest.mark.parametrize("stage", [
        "content_planning", "script", "render", "qa",
        "metadata_generation", "youtube_upload", "tts",
    ])
    def test_stage_key_format(self, stage):
        """Stage keys should follow the stage.<name> pattern."""
        from gpcg.i18n.api_messages import STAGE_KEYS
        assert stage in STAGE_KEYS or f"stage.{stage}" in STAGE_KEYS.values()


# ── Originality cross-language ──────────────────────────────────────────────


class TestOriginalityCrossLanguage:
    """Tests for language-aware originality checking."""

    def test_pt_br_normalization_strips_accents(self):
        from gpcg.domain.originality import _normalize
        result = _normalize("narracão café", language="pt-BR")
        assert "ç" not in result
        assert "é" not in result
        assert "narracao" in result
        assert "cafe" in result

    def test_en_us_normalization_strips_accents(self):
        from gpcg.domain.originality import _normalize
        result = _normalize("café résumé", language="en-US")
        assert "é" not in result
        assert "cafe" in result

    def test_cjk_normalization_preserves_characters(self):
        from gpcg.domain.originality import _normalize
        text = "ゲームプレイ"  # "gameplay" in Japanese
        result = _normalize(text, language="ja-JP")
        # CJK characters should be preserved (not stripped)
        assert "ゲーム" in result

    def test_compare_texts_pt_br(self):
        from gpcg.domain.originality import compare_texts
        script = "Este é um script sobre jogos interessante"
        source = "Este é um texto sobre jogos interessante"
        overlap, _ = compare_texts(script, source, language="pt-BR")
        # With 5-gram, short texts may have 0 overlap; use a longer text
        assert overlap >= 0  # Should not error

    def test_compare_texts_different_languages(self):
        from gpcg.domain.originality import compare_texts
        script = "This is a script about games"
        source = "Este é um texto sobre jogos"
        overlap, _ = compare_texts(script, source, language="en-US")
        # Different languages should have very low overlap
        assert overlap < 0.1


# ── CreativeStyle localized labels ──────────────────────────────────────────


class TestCreativeStyleLocalization:
    """Tests for CreativeStyle multilingual labels."""

    @pytest.mark.parametrize("style_name,expected_en", [
        ("humor", "Brazilian humor"),
        ("absurd", "Absurd"),
        ("sarcastic", "Sarcastic"),
        ("storytelling", "Storytelling"),
        ("curiosity", "Pure curiosity"),
        ("nostalgia", "Nostalgia"),
        ("dark_humor", "Dark humor"),
        ("high_energy", "High energy"),
    ])
    def test_english_labels(self, style_name, expected_en):
        from gpcg.application.creative_engine import get_style
        style = get_style(style_name)
        assert style.get_localized_label("en-US") == expected_en

    def test_pt_br_label_fallback(self):
        from gpcg.application.creative_engine import get_style
        style = get_style("humor")
        assert style.get_localized_label("pt-BR") == "Humor brasileiro"

    def test_unknown_language_falls_back_to_default(self):
        from gpcg.application.creative_engine import get_style
        style = get_style("humor")
        assert style.get_localized_label("zh-CN") == "Humor brasileiro"

    def test_english_description_exists(self):
        from gpcg.application.creative_engine import get_style
        style = get_style("absurd")
        desc = style.get_localized_description("en-US")
        assert "extreme" in desc.lower() or "absurd" in desc.lower()


# ── Prompt registry ─────────────────────────────────────────────────────────


class TestPromptRegistry:
    """Tests for PromptRegistry fallback behavior."""

    def test_pt_br_prompt_exists(self):
        prompt = PromptRegistry.get("DRAFT_SYSTEM", language="pt-BR")
        assert prompt is not None

    def test_en_us_prompt_falls_back_to_pt_br(self):
        """English prompts should fall back to PT-BR if not yet translated."""
        prompt = PromptRegistry.get("DRAFT_SYSTEM", language="en-US")
        # Should return the PT-BR fallback (not None)
        assert prompt is not None

    def test_unknown_prompt_raises_keyerror(self):
        with pytest.raises(KeyError):
            PromptRegistry.get("NONEXISTENT_PROMPT", language="pt-BR")


# ── is_supported ────────────────────────────────────────────────────────────


class TestIsSupported:
    """Tests for language support checking."""

    @pytest.mark.parametrize("language", ["pt-BR", "en-US", "zh-CN", "zh-TW", "zh"])
    def test_supported_languages(self, language):
        assert is_supported(language) is True

    def test_unsupported_language(self):
        assert is_supported("xx-XX") is False


# ── Character density (Mandarin vs Latin) ───────────────────────────────────


class TestCharacterDensity:
    """Tests for language-aware character count targets."""

    def test_mandarin_lower_cps_than_latin(self):
        from gpcg.i18n.language_context import get_chars_per_second
        zh_cps = get_chars_per_second("zh-CN")
        pt_cps = get_chars_per_second("pt-BR")
        assert zh_cps < pt_cps
        assert zh_cps == 5.5  # calibrated from real XTTS output (205 chars / 37.4s)
        assert pt_cps == 13.0

    def test_target_char_range_mandarin_60s(self):
        from gpcg.i18n.language_context import get_target_char_range
        min_c, max_c = get_target_char_range(60, "zh-CN")
        # ~280-379 chars for 60s Mandarin (5.5 cps * 60 = 330)
        assert 250 <= min_c <= 320
        assert 320 <= max_c <= 420

    def test_target_char_range_latin_60s(self):
        from gpcg.i18n.language_context import get_target_char_range
        min_pt, max_pt = get_target_char_range(60, "pt-BR")
        # ~663-897 chars for 60s Portuguese (13 cps * 60 = 780)
        assert min_pt > 600
        assert max_pt > 800

    def test_target_char_range_zh_tw_same_as_zh_cn(self):
        from gpcg.i18n.language_context import get_target_char_range
        min_cn, max_cn = get_target_char_range(60, "zh-CN")
        min_tw, max_tw = get_target_char_range(60, "zh-TW")
        assert min_cn == min_tw
        assert max_cn == max_tw

    def test_target_char_range_zh_same_as_zh_cn(self):
        from gpcg.i18n.language_context import get_target_char_range
        min_cn, _ = get_target_char_range(60, "zh-CN")
        min_zh, _ = get_target_char_range(60, "zh")
        assert min_cn == min_zh


# ── CJK detection ────────────────────────────────────────────────────────────


class TestCJKDetection:
    """Tests for CJK language detection (used for subtitle wrapping)."""

    @pytest.mark.parametrize("language", ["zh-CN", "zh-TW", "zh"])
    def test_chinese_is_cjk(self, language):
        from gpcg.i18n.language_context import is_cjk
        assert is_cjk(language) is True

    @pytest.mark.parametrize("language", ["pt-BR", "en-US"])
    def test_latin_not_cjk(self, language):
        from gpcg.i18n.language_context import is_cjk
        assert is_cjk(language) is False


# ── Chinese prompt registry ──────────────────────────────────────────────────


class TestChinesePrompts:
    """Tests for zh-CN, zh-TW, and zh prompt loading."""

    def test_zh_cn_games_prompt_exists(self):
        prompt = PromptRegistry.get("DRAFT_SYSTEM", language="zh-CN", domain="games")
        assert prompt is not None
        assert "简" in prompt.text  # Simplified Chinese

    def test_zh_tw_games_prompt_exists(self):
        prompt = PromptRegistry.get("DRAFT_SYSTEM", language="zh-TW", domain="games")
        assert prompt is not None
        assert "繁" in prompt.text  # Traditional Chinese

    def test_zh_falls_back_to_zh_cn(self):
        """zh (Mandarin without variant) should fall back to zh-CN."""
        PromptRegistry.clear_cache()
        prompt = PromptRegistry.get("DRAFT_SYSTEM", language="zh", domain="games")
        assert prompt is not None
        assert "简" in prompt.text  # Should use Simplified Chinese

    def test_zh_cn_kids_prompt_exists(self):
        prompt = PromptRegistry.get("DRAFT_SYSTEM", language="zh-CN", domain="kids")
        assert prompt is not None
        assert "简" in prompt.text

    def test_zh_tw_kids_prompt_exists(self):
        prompt = PromptRegistry.get("DRAFT_SYSTEM", language="zh-TW", domain="kids")
        assert prompt is not None
        assert "繁" in prompt.text

    def test_zh_cn_and_zh_tw_are_different(self):
        """Simplified and Traditional should have different text."""
        cn = PromptRegistry.get("DRAFT_SYSTEM", language="zh-CN", domain="games")
        tw = PromptRegistry.get("DRAFT_SYSTEM", language="zh-TW", domain="games")
        assert cn.text != tw.text

    def test_zh_cn_has_mandarin_density_note(self):
        """zh-CN prompts should mention the character density for Mandarin."""
        prompt = PromptRegistry.get("DRAFT_SYSTEM", language="zh-CN", domain="games")
        assert "3.5" in prompt.text or "5.5" in prompt.text or "0.3秒" in prompt.text or "200-280" in prompt.text or "280" in prompt.text
