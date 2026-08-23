"""Kids Idea Scorer — multi-dimensional editorial scoring for KidsIdeas.

Inspired by the Games ``CompositeScorer`` (Quality × Fit × Timing) but
adapted for the Kids domain's editorial dimensions:

    final_score = editorial_quality × age_fit × educational_value × curiosity

Unlike Games' CompositeScorer (which checks gameplay_availability as a
Fit component), Kids scoring focuses on *editorial* fit — how good is
this idea for a Kids educational channel, regardless of production
resources (assets are handled at the production stage, not scoring).

Dimensions (all 0–100 from LLM, normalized to 0.0–1.0):
- editorial_quality: intrinsic quality of the idea
- age_fit: suitability for the target age range
- educational_value: how much a child will learn
- curiosity: how much this sparks curiosity
- visual_potential: how well it can be illustrated
- simplicity: how simply it can be explained

The ``final_score`` is a weighted product of the core dimensions.
``visual_potential`` and ``simplicity`` are tracked but used as
*modifiers* rather than multiplicative factors (a great idea that's
hard to visualize is still a great idea — it just needs more asset work).

Weights are configurable and default to equal emphasis on quality,
age_fit, educational_value, and curiosity.

The scorer is designed to be extensible — new dimensions can be added
to the LLM prompt and the ``score_breakdown`` without changing the
``final_score`` formula (just add them to the weight dict).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from gpcg.infrastructure.llm import LLMClient, LLMError, get_llm
from gpcg.logging import get_logger

log = get_logger(__name__)


# ── Default weights for final_score computation ──────────────────────────────

# Core dimensions (multiplicative — all must be decent for a high score)
_CORE_WEIGHTS: dict[str, float] = {
    "editorial_quality": 0.30,
    "age_fit": 0.25,
    "educational_value": 0.25,
    "curiosity": 0.20,
}

# Modifier dimensions (additive bonus/penalty, not multiplicative)
_MODIFIER_WEIGHTS: dict[str, float] = {
    "visual_potential": 0.5,   # +5% bonus if very visual
    "simplicity": 0.5,         # +5% bonus if very simple
}

# Maximum bonus from modifiers (capped to prevent inflation)
_MAX_MODIFIER_BONUS = 0.15  # +15% max


@dataclass
class KidsScoreResult:
    """Result of scoring a KidsIdea."""
    editorial_quality: float   # 0.0–1.0
    age_fit: float             # 0.0–1.0
    educational_value: float   # 0.0–1.0
    curiosity: float           # 0.0–1.0
    visual_potential: float    # 0.0–1.0
    simplicity: float          # 0.0–1.0
    final_score: float         # 0.0–1.0 (weighted product + modifiers)
    editorial_score_0_100: float  # 0–100 (for DB storage, = final_score * 100)
    breakdown: dict = field(default_factory=dict)
    reason: str = ""

    def __repr__(self) -> str:
        return f"<KidsScore final={self.final_score:.3f} q={self.editorial_quality:.2f}>"


class KidsScorer:
    """Scores KidsIdeas on multiple editorial dimensions.

    Usage::

        scorer = KidsScorer(llm=get_llm())
        result = scorer.score(
            title="Por que os polvos têm três corações?",
            description="...",
            age_range="3-6",
            category="animals",
            channel_context="Canal educativo sobre animais...",
        )
        idea.editorial_score = result.editorial_score_0_100
        idea.final_score = result.final_score
        idea.score_breakdown = result.breakdown
    """

    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self.llm = llm

    def score(
        self,
        title: str,
        description: str = "",
        age_range: str = "3-6",
        category: str = "general",
        channel_context: str = "",
    ) -> KidsScoreResult:
        """Score an idea using LLM evaluation.

        Args:
            title: The idea title.
            description: Optional description.
            age_range: Target age range.
            category: Content category (animals, science, etc.).
            channel_context: Channel profile context for personalization.

        Returns:
            KidsScoreResult with all dimensions + final_score.
        """
        from gpcg.domains.kids.prompts import IDEA_SCORER_SYSTEM

        llm = self.llm or get_llm()

        user_prompt = f"""Idea title: {title}
Description: {description or "(no description)"}
Target age range: {age_range}
Category: {category}
Channel context: {channel_context or "(no specific context)"}

Score this idea."""

        try:
            data = llm.chat_json(
                system=IDEA_SCORER_SYSTEM,
                prompt=user_prompt,
                temperature=0.2,  # low temperature for consistency
                max_tokens=500,
            )
        except (LLMError, Exception) as e:
            log.warning(f"scorer.llm_failed: {e}, using neutral fallback")
            return self._neutral_fallback(str(e))

        # Parse scores (0-100 → normalize to 0.0-1.0)
        def _parse_score(key: str) -> float:
            val = data.get(key, 50)
            try:
                val = float(val)
            except (TypeError, ValueError):
                val = 50.0
            return max(0.0, min(1.0, val / 100.0))

        editorial_quality = _parse_score("editorial_quality")
        age_fit = _parse_score("age_fit")
        educational_value = _parse_score("educational_value")
        curiosity = _parse_score("curiosity")
        visual_potential = _parse_score("visual_potential")
        simplicity = _parse_score("simplicity")
        reason = data.get("reason", "")

        # Compute final_score
        final_score = self._compute_final(
            editorial_quality, age_fit, educational_value, curiosity,
            visual_potential, simplicity,
        )

        breakdown = {
            "editorial_quality": editorial_quality,
            "age_fit": age_fit,
            "educational_value": educational_value,
            "curiosity": curiosity,
            "visual_potential": visual_potential,
            "simplicity": simplicity,
            "final_score": final_score,
            "reason": reason,
        }

        return KidsScoreResult(
            editorial_quality=editorial_quality,
            age_fit=age_fit,
            educational_value=educational_value,
            curiosity=curiosity,
            visual_potential=visual_potential,
            simplicity=simplicity,
            final_score=final_score,
            editorial_score_0_100=final_score * 100,
            breakdown=breakdown,
            reason=reason,
        )

    def _compute_final(
        self,
        editorial_quality: float,
        age_fit: float,
        educational_value: float,
        curiosity: float,
        visual_potential: float,
        simplicity: float,
    ) -> float:
        """Compute final_score from dimensions.

        Core: weighted product of quality × age_fit × educational × curiosity.
        Modifiers: additive bonus from visual_potential and simplicity (capped).
        """
        # Core: weighted geometric mean (product preserves the multiplicative
        # nature — if any dimension is 0, the score is 0)
        core = (
            editorial_quality ** _CORE_WEIGHTS["editorial_quality"]
            * age_fit ** _CORE_WEIGHTS["age_fit"]
            * educational_value ** _CORE_WEIGHTS["educational_value"]
            * curiosity ** _CORE_WEIGHTS["curiosity"]
        )

        # Modifiers: bonus for visual_potential and simplicity
        # Each modifier contributes up to its weight * (score - 0.5)
        # Positive if score > 0.5, negative if < 0.5
        visual_bonus = _MODIFIER_WEIGHTS["visual_potential"] * (visual_potential - 0.5)
        simplicity_bonus = _MODIFIER_WEIGHTS["simplicity"] * (simplicity - 0.5)
        total_bonus = visual_bonus + simplicity_bonus
        total_bonus = max(-_MAX_MODIFIER_BONUS, min(_MAX_MODIFIER_BONUS, total_bonus))

        final = core + total_bonus
        return max(0.0, min(1.0, final))

    def _neutral_fallback(self, error: str) -> KidsScoreResult:
        """Return a neutral score when LLM fails (all 0.5)."""
        return KidsScoreResult(
            editorial_quality=0.5,
            age_fit=0.5,
            educational_value=0.5,
            curiosity=0.5,
            visual_potential=0.5,
            simplicity=0.5,
            final_score=0.5,
            editorial_score_0_100=50.0,
            breakdown={"error": error, "fallback": True},
            reason=f"LLM scoring failed, neutral fallback: {error}",
        )
