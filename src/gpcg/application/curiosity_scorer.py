"""Curiosity scorer — quantitative editorial potential scoring for facts.

Part of the V2 editorial architecture (see docs/EDITORIAL_REFACTOR_PLAN_V2.md
§3.1, §4.2). Computes a `curiosity_score` (0-100) from 5 editorial sub-scores
plus 1 technical sub-score:

  - curiosity_gap (0-100): does the fact create a knowledge gap the viewer
    wants to fill?
  - surprise_potential (0-100): does the fact break a common expectation?
  - retention_potential (0-100): can the fact hold attention for ~60s?
  - familiarity (0-100): does the fact connect to something the viewer
    already knows? (game-specific: familiarity of the game; general
    curiosity: familiarity of the topic — NOT the background game).
    Based on Loewenstein's inverted-U curve: curiosity requires a base
    of knowledge; too little or too much familiarity kills curiosity.
  - insight_quality (0-100): is the fact an "insight" (a piece that
    illuminates the whole) or "trivia" (an isolated detail)?
    Loewenstein: insight > trivia for curiosity.
  - visual_potential (0-100, TECHNICAL — not in the weighted mean):
    can the fact be illustrated with gameplay? Used by the editorial
    planner / gameplay retriever, not for ranking candidates.

curiosity_score = curiosity_gap * 0.30
                + surprise_potential * 0.25
                + retention_potential * 0.20
                + familiarity * 0.15
                + insight_quality * 0.10

Runs during fact extraction (after quality/novelty scoring). Gated by
GPCG_CURIOSITY_SCORING_ENABLED. When off, content planning falls back to
the legacy ranking (quality_score * novelty_score).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.core.models import Fact
from gpcg.domains.games.models import Game
from gpcg.domains.games.prompts import CURIOSITY_SCORER_SYSTEM as SYSTEM_PROMPT
from gpcg.i18n.prompts.registry import PromptRegistry
from gpcg.infrastructure.llm import LLMClient, LLMError
from gpcg.logging import get_logger

log = get_logger(__name__)


# Direct imports kept as fallback when the registry has no translation.
_DIRECT_IMPORTS = {
    "CURIOSITY_SCORER_SYSTEM": SYSTEM_PROMPT,
}


def _get_prompt(name: str, language_context=None) -> str:
    """Resolve a prompt via the PromptRegistry when a language context is
    available, falling back to the direct import otherwise."""
    if language_context is not None:
        try:
            lang = getattr(language_context, "language", str(language_context))
            return PromptRegistry.get(name, domain="games", language=lang).text
        except (KeyError, Exception):
            pass
    return _DIRECT_IMPORTS.get(name, "")


# ── Sub-score weights (sum to 1.0 for the 5 editorial sub-scores) ────────────

WEIGHTS = {
    "curiosity_gap": 0.30,
    "surprise_potential": 0.25,
    "retention_potential": 0.20,
    "familiarity": 0.15,
    "insight_quality": 0.10,
}

# visual_potential is NOT in the weighted mean — it's a technical signal
# used downstream by the editorial planner / gameplay retriever.
TECHNICAL_SUBSCORES = {"visual_potential"}
EDITORIAL_SUBSCORES = set(WEIGHTS.keys())
ALL_SUBSCORES = EDITORIAL_SUBSCORES | TECHNICAL_SUBSCORES


@dataclass
class CuriosityScore:
    """The result of scoring a single fact."""
    curiosity_score: float = 0.0
    subscores: dict[str, float] = field(default_factory=dict)
    latency_ms: int = 0
    success: bool = True
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "curiosity_score": self.curiosity_score,
            "subscores": self.subscores,
            "latency_ms": self.latency_ms,
            "success": self.success,
            "error": self.error,
        }

    @classmethod
    def empty(cls, error: str = "") -> CuriosityScore:
        return cls(success=False, error=error)

    @classmethod
    def from_dict(cls, d: dict) -> CuriosityScore:
        return cls(
            curiosity_score=float(d.get("curiosity_score", 0.0)),
            subscores={k: float(v) for k, v in (d.get("subscores") or {}).items()},
            latency_ms=int(d.get("latency_ms", 0)),
            success=bool(d.get("success", True)),
            error=str(d.get("error", "")),
        )



# ── Scorer ───────────────────────────────────────────────────────────────────


def compute_curiosity_score(subscores: dict[str, float]) -> float:
    """Compute the weighted curiosity_score from editorial sub-scores.

    visual_potential is excluded (technical signal, not editorial).
    """
    total = 0.0
    for key, weight in WEIGHTS.items():
        total += float(subscores.get(key, 0.0)) * weight
    return max(0.0, min(100.0, total))


def _clamp(v) -> float:
    try:
        return max(0.0, min(100.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _normalize_subscores(raw: dict) -> dict[str, float]:
    """Extract and clamp the 6 sub-scores from a raw LLM payload."""
    out: dict[str, float] = {}
    for key in ALL_SUBSCORES:
        out[key] = _clamp(raw.get(key, 0.0))
    return out


class CuriosityScorer:
    """Scores facts for editorial curiosity potential.

    Batches up to 10 facts per LLM call (same pattern as fact_service.score_facts).
    Persists curiosity_score + curiosity_subscores on each Fact.
    """

    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self.llm = llm
        self.settings = get_settings()

    def score_facts(self, session: Session, game_id: int | None, llm: Optional[LLMClient] = None, *, language_context=None) -> int:
        """Score unscored facts (curiosity_score == 0) for a game or general pool.

        Returns the number of facts scored.
        """
        s = self.settings
        if not s.gpcg_curiosity_scoring_enabled:
            return 0

        client = llm or self.llm or LLMClient()

        # Load unscored facts (curiosity_score == 0) for this game_id (or NULL)
        from sqlalchemy import select
        if game_id is not None:
            facts = session.execute(
                select(Fact).where(Fact.game_id == game_id).where(Fact.curiosity_score == 0.0)
            ).scalars().all()
            game = session.get(Game, game_id)
            game_name = game.canonical_name if game else "unknown"
        else:
            facts = session.execute(
                select(Fact).where(Fact.game_id.is_(None)).where(Fact.curiosity_score == 0.0)
            ).scalars().all()
            game_name = "general curiosity"

        if not facts:
            return 0

        scored = 0
        for i in range(0, len(facts), 10):
            batch = facts[i : i + 10]
            claims = [{"id": f.id, "claim": f.claim, "category": f.category} for f in batch]
            prompt = (
                f"Context: {game_name}\n\n"
                f"Score these facts for curiosity potential. For game-specific facts, "
                f"score familiarity based on how well-known the game is. For general "
                f"curiosity facts, score familiarity based on the TOPIC (not any game).\n\n"
                f"Facts: {claims}\n\n"
                f"Return the scores JSON."
            )
            try:
                data = client.chat_json(
                    _get_prompt("CURIOSITY_SCORER_SYSTEM", language_context), prompt,
                    model=s.gpcg_curiosity_scorer_model or None,
                    temperature=s.gpcg_curiosity_scorer_temperature,
                    max_tokens=s.gpcg_curiosity_scorer_max_tokens,
                )
            except LLMError as e:
                log.error(f"curiosity scoring failed: {e}")
                continue

            scores = data.get("scores", []) if isinstance(data, dict) else []
            for sc in scores:
                if not isinstance(sc, dict):
                    continue
                fid = sc.get("id")
                fact = next((f for f in batch if f.id == fid), None)
                if fact is None:
                    continue
                subscores = _normalize_subscores(sc)
                fact.curiosity_subscores = subscores
                fact.curiosity_score = compute_curiosity_score(subscores)
                scored += 1
        session.flush()
        log.info(f"curiosity-scored {scored}/{len(facts)} facts for '{game_name}'")
        return scored

    def score_single(self, fact: Fact, game_name: str = "", llm: Optional[LLMClient] = None, *, language_context=None) -> CuriosityScore:
        """Score a single fact and return a CuriosityScore (does NOT persist).

        Useful for testing / CLI smoke tests.
        """
        s = self.settings
        client = llm or self.llm or LLMClient()
        prompt = (
            f"Context: {game_name or 'general curiosity'}\n\n"
            f"Score this fact for curiosity potential:\n"
            f"{{\"id\": {fact.id}, \"claim\": \"{fact.claim}\", \"category\": \"{fact.category}\"}}\n\n"
            f"Return the scores JSON."
        )
        try:
            data = client.chat_json(
                _get_prompt("CURIOSITY_SCORER_SYSTEM", language_context), prompt,
                model=s.gpcg_curiosity_scorer_model or None,
                temperature=s.gpcg_curiosity_scorer_temperature,
                max_tokens=s.gpcg_curiosity_scorer_max_tokens,
            )
        except LLMError as e:
            return CuriosityScore.empty(f"LLM error: {e}")

        scores = data.get("scores", []) if isinstance(data, dict) else []
        if not scores or not isinstance(scores[0], dict):
            return CuriosityScore.empty("no scores in response")
        subscores = _normalize_subscores(scores[0])
        return CuriosityScore(
            curiosity_score=compute_curiosity_score(subscores),
            subscores=subscores,
            success=True,
        )
