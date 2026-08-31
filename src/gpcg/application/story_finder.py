"""Story Finder — transforms a fact into a story (V2 editorial architecture).

Stage: between `content_planning` and `editorial_planning`.
See docs/EDITORIAL_REFACTOR_PLAN_V2.md §4.1, Fase 2.

Responsibility: given a fact, find the editorial ANGLE that turns it into a
story. If no angle exists (the fact is just information), say is_story=false.

Input: ContentPlan + Fact
Output: StoryConcept (angle, curiosity_gap, narrative_hook, frame, is_insight,
        is_story, confidence)

Gate: if is_story=false or confidence < threshold, the pipeline tries the
next fact candidate. If no candidate yields a story, the job fails graciosamente.

Feature flag: GPCG_STORY_FINDER_ENABLED (default: false). When off, the
pipeline skips story finding and goes straight to editorial_planning.
"""

from __future__ import annotations

import time
from typing import Optional

from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.domain.creative_plan import StoryConcept
from gpcg.core.models import ContentPlan, Fact
from gpcg.domains.games.models import Game
from gpcg.domains.games.prompts import STORY_FINDER_SYSTEM as SYSTEM_PROMPT
from gpcg.i18n.prompt_adapter import adapt_system_prompt
from gpcg.infrastructure.llm import LLMClient, LLMError
from gpcg.logging import get_logger

log = get_logger(__name__)



# ── Story Finder ─────────────────────────────────────────────────────────────


class StoryFinder:
    """Finds the editorial angle that turns a fact into a story.

    Gated by GPCG_STORY_FINDER_ENABLED. When off, returns an empty
    StoryConcept (the pipeline skips story finding).
    """

    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self.llm = llm or LLMClient()
        self.settings = get_settings()

    def find_story(
        self,
        session: Session,
        content_plan: ContentPlan,
        *,
        background_game_id: Optional[int] = None,
        channel_context: str = "",
        language_context=None,
    ) -> StoryConcept:
        """Find the story angle for the fact in a content plan.

        Args:
            session: DB session (for loading the fact + game name)
            content_plan: the content plan (must have a fact_id)
            background_game_id: for curiosity shorts, the background game

        Returns:
            StoryConcept with angle, curiosity_gap, narrative_hook, frame,
            is_insight, is_story, confidence. On failure, StoryConcept.empty().
        """
        s = self.settings
        if not s.gpcg_story_finder_enabled:
            return StoryConcept.empty("story finder disabled")

        t0 = time.time()

        # Load the fact
        if not content_plan.fact_id:
            return StoryConcept.empty("no fact in content plan")
        fact = session.get(Fact, content_plan.fact_id)
        if fact is None:
            return StoryConcept.empty(f"fact #{content_plan.fact_id} not found")

        # Build context — game name if game-specific, else general curiosity
        if content_plan.game_id is not None:
            game = session.get(Game, content_plan.game_id)
            context = f"Game: {game.canonical_name}" if game else "Game: unknown"
        elif background_game_id is not None:
            bg_game = session.get(Game, background_game_id)
            context = (
                f"General curiosity (NOT about the game). "
                f"Background gameplay: {bg_game.canonical_name if bg_game else 'unknown'} (just visual filler)"
            )
        else:
            context = "General curiosity"

        user_prompt = self._build_user_prompt(fact, context, channel_context=channel_context)

        try:
            data = self.llm.chat_json(
                system=adapt_system_prompt(SYSTEM_PROMPT, language_context),
                prompt=user_prompt,
                model=s.gpcg_story_finder_model or None,
                temperature=s.gpcg_story_finder_temperature,
                max_tokens=s.gpcg_story_finder_max_tokens,
            )
        except LLMError as e:
            log.error(f"story finder LLM failed: {e}")
            return StoryConcept.empty(f"LLM error: {e}")

        concept = self._parse_concept(data, fact.claim)
        latency_ms = int((time.time() - t0) * 1000)

        log.info(
            f"story_finder: is_story={concept.is_story} is_insight={concept.is_insight} "
            f"confidence={concept.confidence:.2f} angle='{concept.angle[:50]}' "
            f"latency={latency_ms}ms"
        )

        return concept

    def _build_user_prompt(self, fact: Fact, context: str, *, channel_context: str = "") -> str:
        parts = [
            f"CONTEXT: {context}",
            f"",
            f"FACT (the raw information): {fact.claim}",
            f"CATEGORY: {fact.category}",
            f"",
        ]
        if channel_context:
            parts.append(f"CHANNEL CONTEXT (tone/narrative style to match):")
            parts.append(channel_context)
            parts.append(f"")
        parts.append(
            f"Find the editorial angle that turns this fact into a story. "
            f"If there's no genuine angle, be honest: set is_story=false."
        )
        return "\n".join(parts)

    def _parse_concept(self, data: dict, fact_claim: str) -> StoryConcept:
        """Parse the LLM JSON response into a StoryConcept."""
        if not isinstance(data, dict):
            return StoryConcept.empty("invalid story finder response")

        angle = str(data.get("angle", "")).strip()
        curiosity_gap = str(data.get("curiosity_gap", "")).strip()
        narrative_hook = str(data.get("narrative_hook", "")).strip()
        frame = str(data.get("frame", "")).strip()
        is_insight = bool(data.get("is_insight", False))
        is_story = bool(data.get("is_story", True))
        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0

        return StoryConcept(
            fact_claim=fact_claim,
            angle=angle,
            curiosity_gap=curiosity_gap,
            narrative_hook=narrative_hook,
            frame=frame,
            is_insight=is_insight,
            is_story=is_story,
            confidence=confidence,
            success=True,
        )

    def is_acceptable(self, concept: StoryConcept) -> bool:
        """Check if a StoryConcept passes the quality gate.

        Returns True if is_story=true AND confidence >= min_confidence.
        """
        if not concept.success:
            return False
        if not concept.is_story:
            return False
        return concept.confidence >= self.settings.gpcg_story_finder_min_confidence
