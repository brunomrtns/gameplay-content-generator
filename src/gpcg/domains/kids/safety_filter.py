"""Kids Safety Filter — deterministic + LLM-based safety review for KidsIdeas.

This is a two-layer filter:

1. **Hard rules (deterministic):** keyword blocklist that catches obvious
   inappropriate topics without any LLM call. Fast, predictable, and
   impossible to bypass by prompt injection.

2. **LLM classification:** contextual evaluation of age suitability,
   sensitive content, language complexity, misinterpretation risk.
   The LLM can catch subtle issues that keywords miss (e.g. "why do people
   fight wars" has no blocked keyword but is sensitive for 3-6 year olds).

The filter returns a ``SafetyResult`` with ``safety_score`` (0.0–1.0),
``flags``, and ``safe`` boolean. Ideas with ``safe=False`` or
``safety_score < threshold`` are rejected.

The threshold is configurable per-channel via
``ChannelProfile.metadata_json["kids_safety_strictness"]`` (0.0–1.0).
Default: 0.7.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from gpcg.infrastructure.llm import LLMClient, LLMError, get_llm
from gpcg.logging import get_logger

log = get_logger(__name__)


# ── Hard rules: keyword blocklist ────────────────────────────────────────────

# These are substrings (case-insensitive) that immediately flag an idea.
# The idea is NOT auto-rejected just for matching — it gets safety_score=0.0
# and safe=False from the hard layer. The LLM layer is skipped.
#
# Categories:
# - violence/harm: weapons, fighting, killing
# - adult themes: sexuality, drugs, alcohol
# - scary/disturbing: death, horror, trauma
# - dangerous activities: things kids might imitate
# - political/religious: topics that are divisive for kids content
_BLOCKED_KEYWORDS: list[str] = [
    # violence / harm
    "matar", "assassinar", "esfaquear", "atirar", "arma de fogo",
    "pistola", "revólver", "espingarda", "facada", "sangue",
    "tortura", "abuso", "violência doméstica", "briga de verdade",
    # adult themes
    "sexo", "sexual", "pornô", "pornográfico", "drogas",
    "maconha", "cocaína", "álcool", "bebida alcoólica",
    "cigarro", "fumar", "apostar", "cassino", "prostituição",
    # scary / disturbing
    "assombração", "fantasma assustador", "demônio", "ritual",
    "morte violenta", "suicídio", "assassinato",
    # dangerous activities
    "brincar com fogo", "andar no trânsito", "pular de altura",
    "experimento perigoso", "misturar produtos químicos",
    # political / religious (divisive for kids)
    "eleição", "partido político", "debate político",
]

# Keywords that are *sensitive* (not blocked, but flag for LLM review).
# These reduce the hard safety_score but don't auto-reject.
_SENSITIVE_KEYWORDS: list[str] = [
    "morte", "morreu", "morrer", "morre", "doença grave", "câncer",
    "guerra", "exército", "soldado", "bomba",
    "medo", "assustador", "pesadelo",
    "luto", "perda", "sepultamento", "funeral",
    "bullying", "discriminação", "racismo",
]


@dataclass
class SafetyResult:
    """Result of a safety review."""
    safe: bool
    safety_score: float  # 0.0–1.0
    flags: list[str] = field(default_factory=list)
    age_suitability: float = 0.5  # 0.0–1.0
    reason: str = ""
    reviewed_by: str = ""  # "hard_rules", "llm", "both"

    def __repr__(self) -> str:
        return f"<SafetyResult safe={self.safe} score={self.safety_score:.2f} flags={self.flags}>"


class KidsSafetyFilter:
    """Two-layer safety filter for KidsIdeas.

    Layer 1 (hard rules): keyword blocklist — fast, deterministic.
    Layer 2 (LLM): contextual evaluation — catches subtle issues.

    Usage::

        filter = KidsSafetyFilter(llm=get_llm())
        result = filter.review(
            title="Por que os polvos têm três corações?",
            description="...",
            age_range="3-6",
            strictness=0.8,
        )
        if not result.safe:
            # reject the idea
    """

    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self.llm = llm

    def review(
        self,
        title: str,
        description: str = "",
        age_range: str = "3-6",
        strictness: float = 0.7,
    ) -> SafetyResult:
        """Review an idea for safety.

        Args:
            title: The idea title.
            description: Optional description/context.
            age_range: Target age range ("3-6", "7-10", "all").
            strictness: 0.0 (lenient) to 1.0 (very strict).

        Returns:
            SafetyResult with safe, safety_score, flags, age_suitability.
        """
        # Layer 1: hard rules
        hard_result = self._hard_rules_check(title, description)
        if not hard_result.safe:
            # Hard block — no need for LLM
            hard_result.reviewed_by = "hard_rules"
            log.info(
                f"safety.hard_block: title='{title[:50]}', "
                f"flags={hard_result.flags}"
            )
            return hard_result

        # If hard rules passed but flagged sensitive keywords,
        # lower the starting score for the LLM layer.
        sensitive_flags = hard_result.flags

        # Layer 2: LLM review
        llm_result = self._llm_review(title, description, age_range, strictness)
        llm_result.reviewed_by = "llm"

        # Merge: if hard rules found sensitive keywords, add them to flags
        # and penalize the score
        if sensitive_flags:
            llm_result.flags = list(set(llm_result.flags + sensitive_flags))
            llm_result.safety_score = min(
                llm_result.safety_score,
                max(0.0, llm_result.safety_score - 0.2),
            )
            llm_result.reviewed_by = "both"

        # Apply threshold
        threshold = 1.0 - strictness  # strictness 0.8 → threshold 0.2
        # Actually: higher strictness = higher minimum safety_score required
        # strictness 0.8 → need safety_score >= 0.8
        min_required = strictness
        if llm_result.safety_score < min_required:
            llm_result.safe = False
            if not llm_result.reason:
                llm_result.reason = (
                    f"safety_score {llm_result.safety_score:.2f} "
                    f"< required {min_required:.2f}"
                )

        return llm_result

    # ── Layer 1: Hard rules ────────────────────────────────────────────────

    def _hard_rules_check(self, title: str, description: str) -> SafetyResult:
        """Deterministic keyword check. Fast, no LLM."""
        text = f"{title} {description}".lower()

        # Check blocked keywords → auto-reject
        for kw in _BLOCKED_KEYWORDS:
            if kw in text:
                return SafetyResult(
                    safe=False,
                    safety_score=0.0,
                    flags=[f"blocked_keyword:{kw}"],
                    reason=f"Title/description contains blocked keyword: '{kw}'",
                )

        # Check sensitive keywords → flag but don't reject
        sensitive_found = []
        for kw in _SENSITIVE_KEYWORDS:
            if kw in text:
                sensitive_found.append(f"sensitive_keyword:{kw}")

        if sensitive_found:
            return SafetyResult(
                safe=True,
                safety_score=0.6,  # reduced, LLM will evaluate further
                flags=sensitive_found,
                reason="Sensitive keywords detected — requires LLM review",
            )

        # Clean — no flags
        return SafetyResult(
            safe=True,
            safety_score=1.0,
            flags=[],
            reason="No blocked or sensitive keywords detected",
        )

    # ── Layer 2: LLM review ────────────────────────────────────────────────

    def _llm_review(
        self,
        title: str,
        description: str,
        age_range: str,
        strictness: float,
    ) -> SafetyResult:
        """LLM-based contextual safety evaluation."""
        from gpcg.domains.kids.prompts import SAFETY_FILTER_SYSTEM

        llm = self.llm or get_llm()

        user_prompt = f"""Idea title: {title}
Description: {description or "(no description)"}
Target age range: {age_range}
Safety strictness: {strictness}

Evaluate this idea for safety and appropriateness for children."""

        try:
            data = llm.chat_json(
                system=SAFETY_FILTER_SYSTEM,
                prompt=user_prompt,
                temperature=0.1,  # low temperature for consistency
                max_tokens=500,
            )
        except (LLMError, Exception) as e:
            log.warning(f"safety.llm_failed: {e}, using conservative fallback")
            # Conservative fallback: if LLM fails, assume borderline safe
            # but flag for manual review
            return SafetyResult(
                safe=True,
                safety_score=0.5,
                flags=["llm_review_failed"],
                age_suitability=0.5,
                reason=f"LLM review failed ({e}), conservative fallback",
            )

        # Parse LLM response
        safe = bool(data.get("safe", False))
        safety_score = float(data.get("safety_score", 0.5))
        safety_score = max(0.0, min(1.0, safety_score))
        age_suitability = float(data.get("age_suitability", 0.5))
        age_suitability = max(0.0, min(1.0, age_suitability))
        flags = data.get("flags", [])
        if not isinstance(flags, list):
            flags = [str(flags)] if flags else []
        reason = data.get("reason", "")

        return SafetyResult(
            safe=safe,
            safety_score=safety_score,
            flags=[str(f) for f in flags],
            age_suitability=age_suitability,
            reason=reason,
        )
