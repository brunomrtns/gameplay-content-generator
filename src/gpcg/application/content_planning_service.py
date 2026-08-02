"""Content planning — AI decides which fact + angle makes the best Short.

Input: a game's fact database (scored).
Output: a ContentPlan (topic, hook, tone, energy, music_mood, visual_strategy).
"""

from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.domain.models import ContentPlan, Fact, Game
from gpcg.infrastructure.llm import LLMClient, LLMError
from gpcg.logging import get_logger

log = get_logger(__name__)


SYSTEM_PROMPT = """You are a YouTube Shorts content strategist for a gaming channel.
Your job: pick ONE fact from the provided list and design a content plan for a ~60 second
vertical Short that maximizes viewer retention and curiosity.

Consider:
- Hook potential (first 3 seconds must grab attention)
- Curiosity / surprise factor
- Tellability in ~60 seconds (~800-1000 chars of narration in pt-BR)
- Visual potential (will use gameplay footage as background)
- Originality (prefer less-used facts)

Return JSON:
{
  "fact_id": <int or null>,
  "topic": "<short topic title in pt-BR>",
  "hook": "<first line of the script, the hook, in pt-BR — must be punchy>",
  "tone": "<one of: curious, dramatic, mysterious, energetic, nostalgic, tense, humorous>",
  "energy": <0.0-1.0>,
  "music_mood": "<one of: inspirational, calm, energetic, dramatic, mysterious, neutral>",
  "visual_strategy": "<one of: gameplay_compilation, slow_zoom, fast_cuts, single_clip>",
  "reasoning": "<brief>"
}"""


class ContentPlanningService:
    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self.llm = llm
        self.settings = get_settings()

    def plan_for_game(
        self,
        session: Session,
        game_id: int,
        fact_id: Optional[int] = None,
        avoid_topics: Optional[list[str]] = None,
    ) -> Optional[ContentPlan]:
        """Pick the best unused fact and create a ContentPlan.

        Args:
            session: DB session
            game_id: Game to plan for
            fact_id: If provided, use this specific fact (from editorial decision)
            avoid_topics: Recent topics to avoid repeating (editorial memory)
        """
        game = session.get(Game, game_id)
        if game is None:
            raise ValueError(f"game #{game_id} not found")

        # If a specific fact_id is provided (editorial decision), try it first
        preselected_fact = None
        if fact_id is not None:
            preselected_fact = session.get(Fact, int(fact_id))
            if preselected_fact is None or preselected_fact.game_id != game_id:
                preselected_fact = None
                fact_id = None

        # V2: rank by curiosity_score when enabled, else legacy quality*novelty
        if self.settings.gpcg_curiosity_scoring_enabled:
            facts = session.execute(
                select(Fact)
                .where(Fact.game_id == game_id)
                .where(Fact.quality_score > 0)
                .where(Fact.curiosity_score >= self.settings.gpcg_curiosity_min_threshold)
                .order_by(
                    (Fact.curiosity_score * 0.5 + Fact.quality_score * 0.3 + Fact.novelty_score * 0.2).desc(),
                    Fact.used_count.asc(),
                )
            ).scalars().all()
        else:
            facts = session.execute(
                select(Fact)
                .where(Fact.game_id == game_id)
                .where(Fact.quality_score > 0)
                .order_by((Fact.quality_score * Fact.novelty_score).desc(), Fact.used_count.asc())
            ).scalars().all()

        if not facts and preselected_fact is None:
            log.warning(f"no scored facts for game '{game.canonical_name}'")
            return None

        # If we have a preselected fact, make sure it's in the list
        if preselected_fact and preselected_fact not in facts:
            facts = [preselected_fact] + list(facts)

        # Build fact list for the LLM (top 15 candidates)
        if self.settings.gpcg_curiosity_scoring_enabled:
            candidates = [
                {"id": f.id, "category": f.category, "claim": f.claim,
                 "quality": f.quality_score, "novelty": f.novelty_score,
                 "curiosity": f.curiosity_score, "used": f.used_count}
                for f in facts[:15]
            ]
        else:
            candidates = [
                {"id": f.id, "category": f.category, "claim": f.claim, "quality": f.quality_score, "novelty": f.novelty_score, "used": f.used_count}
                for f in facts[:15]
            ]

        # Build prompt with editorial memory (avoid_topics)
        avoid_str = ""
        if avoid_topics:
            avoid_str = (
                f"\n\n## Topics already covered (AVOID repeating these)\n"
                f"{json.dumps(avoid_topics[-10:], ensure_ascii=False)}\n"
                f"Choose a DIFFERENT angle or fact.\n"
            )
        preselect_str = ""
        if preselected_fact:
            preselect_str = (
                f"\n\n## Editorial direction\n"
                f"The editorial AI suggested this fact: #{preselected_fact.id} "
                f"— {preselected_fact.claim[:100]}\n"
                f"Use it unless you find a clearly better alternative.\n"
            )

        prompt = (
            f"Game: {game.canonical_name}\n"
            f"Target duration: {self.settings.gpcg_default_target_duration}s\n"
            f"Format: {self.settings.gpcg_default_format}\n\n"
            f"Available facts (sorted by potential):\n{candidates}\n"
            f"{preselect_str}{avoid_str}\n"
            "Pick the best one for a new Short and design the plan."
        )

        llm = self.llm or LLMClient()
        try:
            data = llm.chat_json(SYSTEM_PROMPT, prompt, temperature=0.6, max_tokens=1024)
        except LLMError as e:
            log.error(f"content planning failed: {e}")
            return None

        fact_id = data.get("fact_id")
        # Validate fact_id belongs to this game
        fact = None
        if fact_id is not None:
            fact = session.get(Fact, int(fact_id))
            if fact is None or fact.game_id != game_id:
                fact_id = None
                fact = None
        # Fallback: pick the top fact
        if fact is None and facts:
            fact = facts[0]
            fact_id = fact.id

        plan = ContentPlan(
            game_id=game_id,
            fact_id=fact_id,
            format=self.settings.gpcg_default_format,
            target_duration=self.settings.gpcg_default_target_duration,
            topic=(data.get("topic") or "").strip(),
            hook=(data.get("hook") or "").strip(),
            tone=(data.get("tone") or "curious").strip().lower(),
            energy=max(0.0, min(1.0, float(data.get("energy", 0.7)))),
            music_mood=(data.get("music_mood") or "neutral").strip().lower(),
            visual_strategy=(data.get("visual_strategy") or "gameplay_compilation").strip().lower(),
            metadata_json={"reasoning": data.get("reasoning", "")},
        )
        session.add(plan)
        session.flush()

        # Mark fact as used
        if fact is not None:
            fact.used_count += 1

        log.info(
            f"content plan #{plan.id} for '{game.canonical_name}': "
            f"topic='{plan.topic[:50]}' tone={plan.tone} mood={plan.music_mood}"
        )
        return plan

    def plan_for_general_curiosity(
        self,
        session: Session,
        background_game_id: int,
        fact_id: Optional[int] = None,
    ) -> Optional[ContentPlan]:
        """Create a ContentPlan for a general curiosity (not game-specific).

        The fact comes from the general pool (game_id=NULL).
        The background_game_id is the game whose gameplay will run in the background.
        If fact_id is provided, use that specific fact; otherwise auto-pick the best.
        """
        bg_game = session.get(Game, background_game_id)
        if bg_game is None:
            raise ValueError(f"background game #{background_game_id} not found")

        # Get scored general facts (game_id IS NULL)
        if fact_id is not None:
            facts = [session.get(Fact, fact_id)]
            if facts[0] is None or facts[0].game_id is not None:
                raise ValueError(f"fact #{fact_id} not found or not a general fact")
        elif self.settings.gpcg_curiosity_scoring_enabled:
            facts = session.execute(
                select(Fact)
                .where(Fact.game_id.is_(None))
                .where(Fact.quality_score > 0)
                .where(Fact.curiosity_score >= self.settings.gpcg_curiosity_min_threshold)
                .order_by(
                    (Fact.curiosity_score * 0.5 + Fact.quality_score * 0.3 + Fact.novelty_score * 0.2).desc(),
                    Fact.used_count.asc(),
                )
            ).scalars().all()
        else:
            facts = session.execute(
                select(Fact)
                .where(Fact.game_id.is_(None))
                .where(Fact.quality_score > 0)
                .order_by((Fact.quality_score * Fact.novelty_score).desc(), Fact.used_count.asc())
            ).scalars().all()

        if not facts:
            log.warning("no scored general facts available")
            return None

        if self.settings.gpcg_curiosity_scoring_enabled:
            candidates = [
                {"id": f.id, "category": f.category, "claim": f.claim,
                 "quality": f.quality_score, "novelty": f.novelty_score,
                 "curiosity": f.curiosity_score, "used": f.used_count}
                for f in facts[:15]
            ]
            prompt = (
                f"Context: General curiosity (NOT about a specific game)\n"
                f"Background gameplay: {bg_game.canonical_name} (just visual filler, not topically related)\n"
                f"Target duration: {self.settings.gpcg_default_target_duration}s\n"
                f"Format: {self.settings.gpcg_default_format}\n\n"
                f"Available facts (sorted by curiosity potential — "
                f"curiosity_score weighs curiosity gap, surprise, retention, "
                f"familiarity of the TOPIC, and insight quality):\n{candidates}\n\n"
                "Pick the fact with the best STORY potential (highest curiosity). "
                "The script will be about the CURIOSITY, not the game — the gameplay is just background visual."
            )
        else:
            candidates = [
                {"id": f.id, "category": f.category, "claim": f.claim, "quality": f.quality_score, "novelty": f.novelty_score, "used": f.used_count}
                for f in facts[:15]
            ]
            prompt = (
                f"Context: General curiosity (NOT about a specific game)\n"
                f"Background gameplay: {bg_game.canonical_name} (just visual filler, not topically related)\n"
                f"Target duration: {self.settings.gpcg_default_target_duration}s\n"
                f"Format: {self.settings.gpcg_default_format}\n\n"
                f"Available facts (sorted by potential):\n{candidates}\n\n"
                "Pick the best one for a new Short and design the plan. "
                "The script will be about the CURIOSITY, not the game — the gameplay is just background visual."
            )

        llm = self.llm or LLMClient()
        try:
            data = llm.chat_json(SYSTEM_PROMPT, prompt, temperature=0.6, max_tokens=1024)
        except LLMError as e:
            log.error(f"content planning failed: {e}")
            return None

        chosen_fact_id = fact_id or data.get("fact_id")
        fact = None
        if chosen_fact_id is not None:
            fact = session.get(Fact, int(chosen_fact_id))
            if fact is None or fact.game_id is not None:
                chosen_fact_id = None
                fact = None
        if fact is None and facts:
            fact = facts[0]
            chosen_fact_id = fact.id

        plan = ContentPlan(
            game_id=None,  # general curiosity — not about a game
            background_game_id=background_game_id,
            fact_id=chosen_fact_id,
            format=self.settings.gpcg_default_format,
            target_duration=self.settings.gpcg_default_target_duration,
            topic=(data.get("topic") or "").strip(),
            hook=(data.get("hook") or "").strip(),
            tone=(data.get("tone") or "curious").strip().lower(),
            energy=max(0.0, min(1.0, float(data.get("energy", 0.7)))),
            music_mood=(data.get("music_mood") or "neutral").strip().lower(),
            visual_strategy=(data.get("visual_strategy") or "gameplay_compilation").strip().lower(),
            metadata_json={"reasoning": data.get("reasoning", ""), "mode": "curiosity_short"},
        )
        session.add(plan)
        session.flush()

        if fact is not None:
            fact.used_count += 1

        log.info(
            f"content plan #{plan.id} [curiosity] bg='{bg_game.canonical_name}': "
            f"topic='{plan.topic[:50]}' tone={plan.tone} mood={plan.music_mood}"
        )
        return plan
