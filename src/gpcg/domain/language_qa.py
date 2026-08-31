"""Language consistency QA — verify the script's writing system matches the
declared language.

A common failure mode in multilingual generation: the job declares
``zh-CN`` but the LLM falls back to Portuguese/English (especially with
models that have weak CJK capability). This module detects such mismatches
by analyzing the dominant script/writing system of the final script text
and comparing it to the declared BCP-47 language tag.

This is a SOFT check — it flags inconsistencies for review but does NOT
fail the job. The caller decides what to do with the result (typically
logs a WARNING and persists it to ``job.artifacts["language_qa"]``).
"""

from __future__ import annotations

import unicodedata


# ── Unicode range helpers ────────────────────────────────────────────────────

def _is_cjk(ch: str) -> bool:
    """True if ``ch`` is a CJK ideograph (common + ext-A + ext-B)."""
    code = ord(ch)
    # CJK Unified Ideographs (common)
    if 0x4E00 <= code <= 0x9FFF:
        return True
    # CJK Unified Ideographs Extension A
    if 0x3400 <= code <= 0x4DBF:
        return True
    # CJK Unified Ideographs Extension B
    if 0x20000 <= code <= 0x2A6DF:
        return True
    return False


def _is_latin(ch: str) -> bool:
    """True if ``ch`` is a basic Latin letter (A-Z, a-z)."""
    code = ord(ch)
    return (0x41 <= code <= 0x5A) or (0x61 <= code <= 0x7A)


# Portuguese-specific characters (with diacritics). These are strong signals
# that the text is Portuguese rather than English/Spanish.
# We compare against the NFD-decomposed base + combining mark, so we detect
# the precomposed forms (ã, õ, ç, é, á, í, ó, ú, â, ê, ô).
_PORTUGUESE_ACCENTED = set("ãõçéáíóúâêôÃÕÇÉÁÍÓÚÂÊÔ")


def _is_portuguese_specific(ch: str) -> bool:
    """True if ``ch`` is a character strongly associated with Portuguese."""
    return ch in _PORTUGUESE_ACCENTED


# ── Character counting ───────────────────────────────────────────────────────


def _count_scripts(text: str) -> dict[str, int]:
    """Count characters by writing system.

    Returns a dict with keys: ``cjk``, ``latin``, ``portuguese_specific``,
    ``total_letters`` (cjk + latin + accented Latin letters).
    """
    cjk = 0
    latin = 0
    pt_specific = 0
    for ch in text:
        if _is_cjk(ch):
            cjk += 1
            continue
        if _is_portuguese_specific(ch):
            pt_specific += 1
            latin += 1  # accented Latin letters count as Latin too
            continue
        if _is_latin(ch):
            latin += 1
            continue
    total_letters = cjk + latin
    return {
        "cjk": cjk,
        "latin": latin,
        "portuguese_specific": pt_specific,
        "total_letters": total_letters,
    }


# ── Public API ───────────────────────────────────────────────────────────────


def check_script_language_consistency(
    script_text: str, declared_language: str
) -> tuple[bool, str]:
    """Check whether ``script_text``'s dominant script matches ``declared_language``.

    Args:
        script_text: The final narration script text.
        declared_language: BCP-47 language tag (e.g. ``zh-CN``, ``pt-BR``,
            ``en-US``).

    Returns:
        ``(is_consistent, reason)``. ``is_consistent`` is True when the
        script's writing system is compatible with the declared language.
        ``reason`` is a human-readable explanation (empty string when
        consistent).
    """
    if not script_text or not script_text.strip():
        return True, ""  # nothing to check

    base = (declared_language or "").split("-")[0].lower()
    counts = _count_scripts(script_text)
    total = counts["total_letters"]
    if total == 0:
        return True, ""  # no letters to analyze

    cjk_ratio = counts["cjk"] / total
    pt_ratio = counts["portuguese_specific"] / total

    # ── Declared Chinese ──────────────────────────────────────────────────
    if base == "zh":
        if cjk_ratio < 0.30:
            return (
                False,
                (
                    f"Declared language '{declared_language}' but script is "
                    f"only {cjk_ratio:.0%} CJK characters "
                    f"(expected ≥30%). The script appears to be mostly "
                    f"Latin/Portuguese — the LLM likely fell back to a "
                    f"non-Chinese language."
                ),
            )
        return True, ""

    # ── Declared Portuguese or English (Latin scripts) ────────────────────
    if base in ("pt", "en"):
        if cjk_ratio > 0.50:
            return (
                False,
                (
                    f"Declared language '{declared_language}' but script is "
                    f"{cjk_ratio:.0%} CJK characters. The script appears to "
                    f"be mostly Chinese — language mismatch."
                ),
            )

    # ── Declared English but Portuguese-specific characters present ───────
    # English text essentially never contains ã/õ/ç/é/á/í/ó/ú/â/ê/ô, so even
    # a modest ratio (~5%) is a strong signal the script is Portuguese.
    if base == "en":
        if pt_ratio > 0.05:
            return (
                False,
                (
                    f"Declared language '{declared_language}' but script "
                    f"contains {pt_ratio:.0%} Portuguese-specific characters "
                    f"(ã, õ, ç, é, á, etc.). The script is likely "
                    f"Portuguese, not English."
                ),
            )

    return True, ""
