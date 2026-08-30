"""Content planning — AI decides which fact + angle makes the best Short.

Input: a game's fact database (scored).
Output: a ContentPlan (topic, hook, tone, energy, music_mood, visual_strategy).

V2: When GPCG_CONTENT_INTELLIGENCE_ENABLED is on, also considers
KnowledgeItems (external content from RSS/Wikipedia) alongside Facts.
The LLM receives a unified list of "ideas" without knowing the source.
See ARCHITECTURE_V2.md §7.4, §9.1.
"""

from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.core.models import (
    ContentPlan,
    Fact,
    KnowledgeItem,
    KnowledgeItemStatus,
)
from gpcg.domains.games.models import ContentScope, Game
from gpcg.domains.games.prompts import CONTENT_PLANNING_SYSTEM as SYSTEM_PROMPT
from gpcg.infrastructure.llm import LLMClient, LLMError
from gpcg.logging import get_logger

log = get_logger(__name__)



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
        *,
        scope: str = ContentScope.game.value,
        user_id: Optional[int] = None,
        channel_context: str = "",
        language_context=None,
    ) -> Optional[ContentPlan]:
        """Pick the best unused fact/item and create a ContentPlan.

        Args:
            session: DB session
            game_id: Game to plan for
            fact_id: If provided, use this specific fact (from editorial decision)
            avoid_topics: Recent topics to avoid repeating (editorial memory)
            scope: V2 content scope — "game", "franchise", or "developer".
                    Only used when GPCG_CONTENT_INTELLIGENCE_ENABLED is on.
            user_id: REFACTORY_V2 — consumer user for content visibility filter.
                     When provided, only facts/KIs visible to this user are
                     considered (own + shared pool + public of others).
        """
        game = session.get(Game, game_id)
        if game is None:
            raise ValueError(f"game #{game_id} not found")

        # REFACTORY_V2: visibility filter for hybrid content pool
        from gpcg.domain.visibility import visible_to_user
        fact_vis = visible_to_user(Fact.user_id, Fact.is_public, user_id)

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
                .where(fact_vis)
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
                .where(fact_vis)
                .order_by((Fact.quality_score * Fact.novelty_score).desc(), Fact.used_count.asc())
            ).scalars().all()

        # V2: collect KnowledgeItems if content intelligence is enabled
        knowledge_items: list[KnowledgeItem] = []
        if self.settings.gpcg_content_intelligence_enabled:
            knowledge_items = self._get_knowledge_items(session, game_id, scope, user_id=user_id)
            # Fallback: if no game-specific KIs, include general KIs (game_id=None)
            if not knowledge_items:
                knowledge_items = self._get_general_knowledge_items(session, user_id=user_id)
                if knowledge_items:
                    log.info(f"No game-specific KIs for '{game.canonical_name}', using {len(knowledge_items)} general KIs")

        if not facts and preselected_fact is None and not knowledge_items:
            log.warning(f"no scored facts or knowledge items for game '{game.canonical_name}'")
            return None

        # If we have a preselected fact, make sure it's in the list
        if preselected_fact and preselected_fact not in facts:
            facts = [preselected_fact] + list(facts)

        # V2: Build unified candidate list (Facts + KnowledgeItems)
        candidates = self._build_unified_candidates(facts, knowledge_items)

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

        channel_str = ""
        if channel_context:
            channel_str = (
                f"\n\n## Channel context\n{channel_context}\n"
                f"Align the topic and tone with this channel identity.\n"
            )

        prompt = (
            f"Game: {game.canonical_name}\n"
            f"Target duration: {self.settings.gpcg_default_target_duration}s\n"
            f"Format: {self.settings.gpcg_default_format}\n\n"
            f"Available ideas (sorted by potential):\n{candidates}\n"
            f"{preselect_str}{avoid_str}{channel_str}\n"
            "Pick the best one for a new Short and design the plan."
        )

        llm = self.llm or LLMClient()
        try:
            data = llm.chat_json(SYSTEM_PROMPT, prompt, temperature=0.6, max_tokens=1024)
        except LLMError as e:
            log.error(f"content planning failed: {e}")
            return None

        # V2: LLM may return fact_id and/or knowledge_item_id
        fact_id = data.get("fact_id")
        knowledge_item_id = data.get("knowledge_item_id")

        # Validate fact_id belongs to this game
        fact = None
        if fact_id is not None:
            fact = session.get(Fact, int(fact_id))
            if fact is None or fact.game_id != game_id:
                fact_id = None
                fact = None

        # V2: Validate knowledge_item_id
        ki = None
        if knowledge_item_id is not None:
            ki = session.get(KnowledgeItem, int(knowledge_item_id))
            if ki is None or ki.status != KnowledgeItemStatus.fresh.value:
                knowledge_item_id = None
                ki = None

        # Fallback: pick the top fact if neither was selected
        if fact is None and ki is None and facts:
            fact = facts[0]
            fact_id = fact.id

        plan = ContentPlan(
            game_id=game_id,
            fact_id=fact_id,
            user_id=user_id,
            format=self.settings.gpcg_default_format,
            target_duration=self.settings.gpcg_default_target_duration,
            target_language=language_context.language if language_context else "pt-BR",
            topic=(data.get("topic") or "").strip(),
            hook=(data.get("hook") or "").strip(),
            tone=(data.get("tone") or "curious").strip().lower(),
            energy=max(0.0, min(1.0, float(data.get("energy", 0.7)))),
            music_mood=(data.get("music_mood") or "neutral").strip().lower(),
            visual_strategy=(data.get("visual_strategy") or "gameplay_compilation").strip().lower(),
            metadata_json={
                "reasoning": data.get("reasoning", ""),
                # V2: track which knowledge item was used (if any)
                "knowledge_item_id": knowledge_item_id,
                # REFACTORY_V2: provenance chain — idea → source → facts → script
                # Tracks the origin of the content idea for auditability.
                "provenance": {
                    "source_type": "knowledge_item" if knowledge_item_id else "fact",
                    "source_id": knowledge_item_id or (fact_id if fact_id else None),
                    "source_claim": fact.claim if fact else None,
                    "source_document_id": fact.document_id if fact else None,
                    "game_name": game.canonical_name,
                    "planning_scope": scope,
                },
            },
        )
        session.add(plan)
        session.flush()

        # Mark fact as used
        if fact is not None:
            fact.used_count += 1

        # V3: Record per-consumer usage of the knowledge item.
        # For public KIs, only creates a usage record (global status stays
        # fresh). For private KIs, also marks global status=used.
        if ki is not None:
            from gpcg.application.knowledge_item_service import record_usage as _record_usage
            _record_usage(session, ki.id, plan.user_id)

        log.info(
            f"content plan #{plan.id} for '{game.canonical_name}': "
            f"topic='{plan.topic[:50]}' tone={plan.tone} mood={plan.music_mood}"
            + (f" [ki=#{ki.id}]" if ki else "")
        )
        return plan

    def plan_for_knowledge_item(
        self,
        session: Session,
        knowledge_item_id: int,
        background_game_id: Optional[int] = None,
        *,
        user_id: Optional[int] = None,
        channel_context: str = "",
        language_context=None,
    ) -> Optional[ContentPlan]:
        """Create a ContentPlan from a specific KnowledgeItem.

        This is used when the user has queued a specific idea (KI) for
        production. The KI's content is used directly as the basis for the
        video — no LLM selection is needed.

        If background_game_id is provided, the plan is a curiosity_short
        (general idea with background gameplay). Otherwise, it's a
        generate_short for the KI's game (if it has one).
        """
        ki = session.get(KnowledgeItem, knowledge_item_id)
        if ki is None:
            raise ValueError(f"KnowledgeItem #{knowledge_item_id} not found")
        if ki.status != KnowledgeItemStatus.fresh.value:
            log.warning(f"KI #{knowledge_item_id} is not fresh (status={ki.status})")
            # Still allow it — the user explicitly queued it

        # Determine the game context
        game_id = ki.game_id if ki.game_id else None
        bg_game = None
        if background_game_id:
            bg_game = session.get(Game, background_game_id)

        # Build the plan directly from the KI
        topic = ki.title[:200] if ki.title else "Untitled"
        plan = ContentPlan(
            game_id=game_id,
            background_game_id=background_game_id,
            topic=topic,
            hook=(ki.content[:200] if ki.content else topic),
            tone="curious",
            music_mood="energetic",
            fact_id=None,
            user_id=user_id,
            format=self.settings.gpcg_default_format,
            target_duration=self.settings.gpcg_default_target_duration,
            target_language=language_context.language if language_context else "pt-BR",
            metadata_json={
                "mode": "curiosity_short" if background_game_id else "generate_short",
                "knowledge_item_id": ki.id,
                "source_type": "knowledge_item",
                "source_id": ki.id,
                "idea_source": "user_queue",
                "channel_context": channel_context if channel_context else None,
                "provenance": {
                    "idea": ki.title,
                    "source": ki.source_type or "rss",
                    "source_url": ki.source_url,
                    "facts": [],
                    "script": None,
                    "game_name": bg_game.canonical_name if bg_game else None,
                },
                "background_game_id": background_game_id,
                "planning_scope": "user_queued",
            },
        )
        session.add(plan)
        session.flush()

        # NOTE: KI status is NOT changed here. The KI is marked as used only
        # when the Video is persisted (in generation_service.py). If the job
        # fails before that, the KI remains fresh and can be re-queued.
        log.info(
            f"content plan #{plan.id} [user-queued KI #{ki.id}]: "
            f"topic='{topic[:50]}' game_id={game_id} bg={bg_game.canonical_name if bg_game else 'None'}"
        )
        return plan

    def plan_for_general_curiosity(
        self,
        session: Session,
        background_game_id: int,
        fact_id: Optional[int] = None,
        *,
        user_id: Optional[int] = None,
        channel_context: str = "",
        language_context=None,
    ) -> Optional[ContentPlan]:
        """Create a ContentPlan for a general curiosity (not game-specific).

        The fact comes from the general pool (game_id=NULL).
        The background_game_id is the game whose gameplay will run in the background.
        If fact_id is provided, use that specific fact; otherwise auto-pick the best.

        REFACTORY_V2: applies visibility filter (own + shared pool + public).
        """
        bg_game = session.get(Game, background_game_id)
        if bg_game is None:
            raise ValueError(f"background game #{background_game_id} not found")

        from gpcg.domain.visibility import visible_to_user
        fact_vis = visible_to_user(Fact.user_id, Fact.is_public, user_id)

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
                .where(fact_vis)
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
                .where(fact_vis)
                .order_by((Fact.quality_score * Fact.novelty_score).desc(), Fact.used_count.asc())
            ).scalars().all()

        if not facts:
            # V2: No general facts — try KnowledgeItems (content ideas)
            if self.settings.gpcg_content_intelligence_enabled:
                ki_vis = visible_to_user(KnowledgeItem.user_id, KnowledgeItem.is_public, user_id)
                general_kis = list(session.execute(
                    select(KnowledgeItem)
                    .where(KnowledgeItem.status == KnowledgeItemStatus.fresh.value)
                    .where(KnowledgeItem.editorial_score >= self.settings.gpcg_content_min_editorial_score)
                    .where(ki_vis)
                    .order_by(KnowledgeItem.editorial_score.desc())
                    .limit(15)
                ).scalars().all())
                if general_kis:
                    # Use the best KnowledgeItem as the basis for the video
                    ki = general_kis[0]
                    plan = ContentPlan(
                        game_id=None,
                        background_game_id=background_game_id,
                        topic=ki.title[:200],
                        tone="curious",
                        mood="energetic",
                        fact_id=None,
                        user_id=user_id,
                        scope=ContentScope.general.value,
                        target_duration=self.settings.gpcg_default_target_duration,
            target_language=language_context.language if language_context else "pt-BR",
                        metadata={
                            "mode": "curiosity_short",
                            "knowledge_item_id": ki.id,
                            "source_type": "knowledge_item",
                            "source_id": ki.id,
                            "provenance": {
                                "idea": ki.title,
                                "source": ki.source_type or "rss",
                                "facts": [],
                                "script": None,
                            },
                            "background_game_id": background_game_id,
                            "planning_scope": "general_curiosity",
                        },
                    )
                    session.add(plan)
                    session.flush()
                    # V3: Record per-consumer usage (not global status for public KIs)
                    from gpcg.application.knowledge_item_service import record_usage as _record_usage
                    _record_usage(session, ki.id, plan.user_id)
                    log.info(
                        f"content plan #{plan.id} [curiosity] bg='{bg_game.canonical_name}': "
                        f"KI #{ki.id} '{ki.title[:60]}'"
                    )
                    return plan
            log.warning("no scored general facts or knowledge items available")
            return None

        channel_str = ""
        if channel_context:
            channel_str = (
                f"\n\n## Channel context\n{channel_context}\n"
                f"Align the topic and tone with this channel identity.\n"
            )

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
                f"{channel_str}"
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
                f"{channel_str}"
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
            user_id=user_id,
            format=self.settings.gpcg_default_format,
            target_duration=self.settings.gpcg_default_target_duration,
            target_language=language_context.language if language_context else "pt-BR",
            topic=(data.get("topic") or "").strip(),
            hook=(data.get("hook") or "").strip(),
            tone=(data.get("tone") or "curious").strip().lower(),
            energy=max(0.0, min(1.0, float(data.get("energy", 0.7)))),
            music_mood=(data.get("music_mood") or "neutral").strip().lower(),
            visual_strategy=(data.get("visual_strategy") or "gameplay_compilation").strip().lower(),
            metadata_json={
                "reasoning": data.get("reasoning", ""),
                "mode": "curiosity_short",
                # REFACTORY_V2: provenance chain for curiosity shorts
                "provenance": {
                    "source_type": "fact",
                    "source_id": chosen_fact_id,
                    "source_claim": fact.claim if fact else None,
                    "source_document_id": fact.document_id if fact else None,
                    "background_game_id": background_game_id,
                    "planning_scope": "general_curiosity",
                },
            },
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

    # ── V2: KnowledgeItem integration helpers ──────────────────────────────

    def _get_knowledge_items(
        self,
        session: Session,
        game_id: int,
        scope: str,
        *,
        user_id: Optional[int] = None,
    ) -> list[KnowledgeItem]:
        """V2: Get fresh KnowledgeItems for a game, filtered by scope.

        scope="game": items with game_id == this game
        scope="franchise": items from games with the same franchise
        scope="developer": items from games with the same developer

        REFACTORY_V2: applies visibility filter (own + shared pool + public).
        """
        game = session.get(Game, game_id)
        if not game:
            return []

        from gpcg.domain.visibility import visible_to_user
        ki_vis = visible_to_user(KnowledgeItem.user_id, KnowledgeItem.is_public, user_id)

        stmt = (
            select(KnowledgeItem)
            .where(KnowledgeItem.status == KnowledgeItemStatus.fresh.value)
            .where(KnowledgeItem.editorial_score >= self.settings.gpcg_content_min_editorial_score)
            .where(ki_vis)
            .order_by(KnowledgeItem.editorial_score.desc())
            .limit(10)
        )

        if scope == ContentScope.game.value or not game:
            stmt = stmt.where(KnowledgeItem.game_id == game_id)
        elif scope == ContentScope.franchise.value and game.franchise:
            stmt = stmt.where(KnowledgeItem.franchise == game.franchise)
        elif scope == ContentScope.developer.value and game.developer:
            stmt = stmt.where(KnowledgeItem.developer == game.developer)
        else:
            stmt = stmt.where(KnowledgeItem.game_id == game_id)

        return list(session.execute(stmt).scalars().all())

    def _get_general_knowledge_items(
        self,
        session: Session,
        *,
        user_id: Optional[int] = None,
    ) -> list[KnowledgeItem]:
        """V2: Get general (game_id=None) KnowledgeItems as fallback.

        Used when no game-specific KIs are available — allows generating
        a video about a general topic with the job's game as background.
        """
        from gpcg.domain.visibility import visible_to_user
        ki_vis = visible_to_user(KnowledgeItem.user_id, KnowledgeItem.is_public, user_id)

        stmt = (
            select(KnowledgeItem)
            .where(KnowledgeItem.status == KnowledgeItemStatus.fresh.value)
            .where(KnowledgeItem.game_id.is_(None))
            .where(ki_vis)
            .order_by(KnowledgeItem.editorial_score.desc())
            .limit(10)
        )
        return list(session.execute(stmt).scalars().all())

    def _build_unified_candidates(
        self,
        facts: list[Fact],
        knowledge_items: list[KnowledgeItem],
    ) -> list[dict]:
        """V2: Build a unified candidate list from Facts + KnowledgeItems.

        The LLM receives a list of "ideas" without knowing the source type.
        Each idea has: id, source ("fact" or "knowledge_item"), title/claim,
        type, score, and used count.
        """
        candidates = []

        # Add facts (top 10)
        for f in facts[:10]:
            if self.settings.gpcg_curiosity_scoring_enabled:
                candidates.append({
                    "id": f.id,
                    "source": "fact",
                    "category": f.category,
                    "claim": f.claim,
                    "quality": f.quality_score,
                    "novelty": f.novelty_score,
                    "curiosity": f.curiosity_score,
                    "used": f.used_count,
                })
            else:
                candidates.append({
                    "id": f.id,
                    "source": "fact",
                    "category": f.category,
                    "claim": f.claim,
                    "quality": f.quality_score,
                    "novelty": f.novelty_score,
                    "used": f.used_count,
                })

        # V2: Add knowledge items (top 10)
        for ki in knowledge_items[:10]:
            candidates.append({
                "id": ki.id,
                "source": "knowledge_item",
                "item_type": ki.item_type,
                "title": ki.title,
                "content": ki.content[:200],  # truncate for prompt
                "editorial_score": ki.editorial_score,
                "source_type": ki.source_type,
                "published_at": ki.published_at.isoformat() if ki.published_at else None,
            })

        return candidates
