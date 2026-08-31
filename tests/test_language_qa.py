"""Tests for language consistency QA (Fase 6b).

Verifies that check_script_language_consistency correctly flags scripts
whose dominant writing system does not match the declared language.
"""

from gpcg.domain.language_qa import check_script_language_consistency


# ── Inconsistent cases ───────────────────────────────────────────────────────


def test_chinese_declared_but_portuguese_script_is_inconsistent():
    """A zh-CN job whose script is actually Portuguese must be flagged."""
    script = (
        "Você sabia que este jogo tem um segredo incrível? "
        "Muitos jogadores não conhecem essa história fascinante "
        "que mudou tudo para sempre na comunidade."
    )
    is_consistent, reason = check_script_language_consistency(script, "zh-CN")
    assert is_consistent is False
    assert "CJK" in reason or "Chinese" in reason or "Latin" in reason


def test_english_declared_but_portuguese_script_is_inconsistent():
    """An en-US job whose script has Portuguese-specific chars is flagged."""
    script = (
        "Você não vai acreditar nesta história incrível. "
        "O jogo esconde um segredo que poucos conhecem. "
        "Esta narracão revela tudo sobre a criação."
    )
    is_consistent, reason = check_script_language_consistency(script, "en-US")
    assert is_consistent is False
    assert "Portuguese" in reason


def test_portuguese_declared_but_chinese_script_is_inconsistent():
    """A pt-BR job whose script is mostly CJK must be flagged."""
    script = "这是一个关于游戏的秘密故事。很多玩家都不知道这个 fascinating 的历史。"
    is_consistent, reason = check_script_language_consistency(script, "pt-BR")
    assert is_consistent is False
    assert "CJK" in reason or "Chinese" in reason


# ── Consistent cases ─────────────────────────────────────────────────────────


def test_portuguese_declared_and_portuguese_script_is_consistent():
    """A pt-BR job with a Portuguese script is consistent."""
    script = (
        "Você sabia que este jogo tem um segredo incrível? "
        "Muitos jogadores não conhecem essa história fascinante."
    )
    is_consistent, reason = check_script_language_consistency(script, "pt-BR")
    assert is_consistent is True
    assert reason == ""


def test_english_declared_and_english_script_is_consistent():
    """An en-US job with an English script is consistent."""
    script = (
        "Did you know this game has an incredible secret? "
        "Many players do not know this fascinating story "
        "that changed everything for the community."
    )
    is_consistent, reason = check_script_language_consistency(script, "en-US")
    assert is_consistent is True
    assert reason == ""


def test_chinese_declared_and_chinese_script_is_consistent():
    """A zh-CN job with a Chinese script is consistent."""
    script = "你知道吗？这个游戏有一个惊人的秘密。很多玩家都不知道这个迷人的故事。"
    is_consistent, reason = check_script_language_consistency(script, "zh-CN")
    assert is_consistent is True
    assert reason == ""


# ── Edge cases ───────────────────────────────────────────────────────────────


def test_empty_script_is_consistent():
    """Empty script text should not be flagged (nothing to analyze)."""
    is_consistent, reason = check_script_language_consistency("", "zh-CN")
    assert is_consistent is True
    assert reason == ""


def test_whitespace_only_script_is_consistent():
    is_consistent, reason = check_script_language_consistency("   \n  ", "pt-BR")
    assert is_consistent is True
    assert reason == ""
