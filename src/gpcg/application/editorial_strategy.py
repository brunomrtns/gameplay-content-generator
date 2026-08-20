"""Editorial Strategy Service — decides what video to produce next.

The GPCG is an autonomous content producer: the user provides gameplays,
knowledge, and channel identity, and the system decides on its own:
- which game to feature
- what topic to cover
- what format to use (game-specific short vs general curiosity)
- which gameplay to use as background

This service acts as the "editorial brain" of the channel. Before creating
a new job, it analyzes:
1. Which games have ready gameplay sources (imported + mapped)
2. Which games have knowledge (facts, document chunks)
3. What topics have already been covered (ContentPlan history)
4. Which facts have been used recently (Fact.used_count)
5. Channel profile (tone, niche, audience)

It then uses an LLM to make an editorial decision: pick a game + fact +
format that maximizes variety and avoids repetition.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select, func, desc
from sqlalchemy.orm import Session

from gpcg.core.models import (
    ContentPlan,
    Fact,
    Job,
    JobStatus,
    KnowledgeChunk,
    KnowledgeItem,
    KnowledgeItemStatus,
    Video,
)
from gpcg.domains.games.models import (
    Game,
    GameplayAsset,
    GameplaySource,
    IngestionStatus,
)
from gpcg.logging import get_logger

log = get_logger(__name__)


@dataclass
class EditorialDecision:
    """The editorial decision: what video to produce next."""

    job_type: str  # "generate_short" | "curiosity_short"
    game_id: Optional[int] = None  # for generate_short: the game to feature
    background_game_id: Optional[int] = None  # for curiosity_short: background game
    fact_id: Optional[int] = None  # which fact to use (if any)
    topic_hint: str = ""  # editorial direction for the content planner
    reason: str = ""  # why this decision was made (for logging)
    success: bool = True
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "job_type": self.job_type,
            "game_id": self.game_id,
            "background_game_id": self.background_game_id,
            "fact_id": self.fact_id,
            "topic_hint": self.topic_hint,
            "reason": self.reason,
            "success": self.success,
            "error": self.error,
        }


@dataclass
class GameInventory:
    """Summary of what's available for a game."""

    game_id: int
    game_name: str
    gameplay_sources_ready: int = 0
    gameplay_sources_total: int = 0
    gameplay_clips_available: int = 0
    total_gameplay_duration: float = 0.0
    facts_available: int = 0
    facts_unused: int = 0
    knowledge_chunks: int = 0
    knowledge_items: int = 0
    videos_produced: int = 0
    recent_topics: list[str] = field(default_factory=list)

    @property
    def has_gameplay(self) -> bool:
        # A game only has usable gameplay if it has clips/assets defined.
        # A GameplaySource with status=ready but 0 clips is NOT usable —
        # the render pipeline will fail with "no gameplay assets available".
        return self.gameplay_clips_available > 0

    @property
    def has_knowledge(self) -> bool:
        return self.facts_available > 0 or self.knowledge_chunks > 0 or self.knowledge_items > 0

    @property
    def is_producible(self) -> bool:
        """A game is producible if it has gameplay AND knowledge."""
        return self.has_gameplay and self.has_knowledge

    def to_prompt_dict(self) -> dict:
        return {
            "game": self.game_name,
            "gameplay_clips": self.gameplay_clips_available,
            "total_duration_s": int(self.total_gameplay_duration),
            "facts": self.facts_available,
            "unused_facts": self.facts_unused,
            "knowledge_chunks": self.knowledge_chunks,
            "knowledge_items": self.knowledge_items,
            "videos_already_made": self.videos_produced,
            "recent_topics": self.recent_topics[-5:],
        }


class EditorialStrategyService:
    """Decides what video to produce next, autonomously.

    Called by the automation loop before creating a new job. Analyzes the
    user's inventory (gameplays, knowledge, history) and uses an LLM to
    make an editorial decision.
    """

    def __init__(self, llm=None) -> None:
        self.llm = llm
        # How many recent topics to consider for diversity
        self.history_window = 10
        # How many days to look back for "recent" videos
        self.recent_days = 30

    def decide_next_video(self, session: Session, user_id: int) -> EditorialDecision:
        """Analyze the user's inventory and decide what to produce next.

        Returns an EditorialDecision with the chosen game, format, and fact.
        """
        # 1. Build inventory of all games with gameplay + knowledge
        inventory = self._build_inventory(session, user_id)
        if not inventory:
            return EditorialDecision(
                job_type="generate_short",
                success=False,
                error="No games with both gameplay and knowledge available",
            )

        producible = [g for g in inventory if g.is_producible]
        if not producible:
            # Check if any game has gameplay but no knowledge (can still do
            # a gameplay-focused video without RAG)
            gameplay_only = [g for g in inventory if g.has_gameplay]
            if gameplay_only:
                producible = gameplay_only
                log.info(
                    f"editorial: {len(gameplay_only)} games have gameplay but no "
                    f"knowledge — will produce without RAG"
                )
            else:
                return EditorialDecision(
                    job_type="generate_short",
                    success=False,
                    error="No games with ready gameplay sources",
                )

        # 2. Get recent editorial history (topics, games, facts used)
        recent_history = self._get_recent_history(session, user_id)

        # 3. Use LLM to make editorial decision
        if self.llm is None:
            # Fallback: simple heuristic (least recently produced game)
            decision = self._heuristic_decision(producible, recent_history)
        else:
            try:
                decision = self._llm_decision(producible, recent_history, session, user_id)
            except Exception as e:
                log.warning(f"editorial LLM decision failed: {e}, falling back to heuristic")
                decision = self._heuristic_decision(producible, recent_history)

        log.info(
            f"editorial decision: type={decision.job_type} "
            f"game_id={decision.game_id or decision.background_game_id} "
            f"fact_id={decision.fact_id} reason={decision.reason[:80]}"
        )
        return decision

    def _build_inventory(self, session: Session, user_id: int) -> list[GameInventory]:
        """Build inventory of all games for the user."""
        # Find all games that have gameplay sources for this user
        games_with_gameplay = session.execute(
            select(Game, func.count(GameplaySource.id), func.sum(GameplaySource.duration))
            .join(GameplaySource, GameplaySource.game_id == Game.id)
            .where(GameplaySource.user_id == user_id)
            .where(GameplaySource.ingestion_status == IngestionStatus.ready.value)
            .group_by(Game.id)
        ).all()

        # Also find games that have knowledge (facts or chunks)
        games_with_facts = set(
            session.execute(
                select(Fact.game_id)
                .where(Fact.user_id == user_id)
                .where(Fact.game_id.isnot(None))
                .distinct()
            ).scalars().all()
        )
        games_with_chunks = set(
            session.execute(
                select(KnowledgeChunk.game_id)
                .where(KnowledgeChunk.user_id == user_id)
                .where(KnowledgeChunk.game_id.isnot(None))
                .distinct()
            ).scalars().all()
        )

        # V2: Also include games with KnowledgeItems (content ideas)
        games_with_kis = set(
            session.execute(
                select(KnowledgeItem.game_id)
                .where(KnowledgeItem.status == KnowledgeItemStatus.fresh.value)
                .where(KnowledgeItem.game_id.isnot(None))
                .where((KnowledgeItem.user_id == user_id) | (KnowledgeItem.user_id.is_(None)))
                .distinct()
            ).scalars().all()
        )

        all_game_ids = {g.id for g, _, _ in games_with_gameplay} | games_with_facts | games_with_chunks | games_with_kis
        if not all_game_ids:
            return []

        inventory: list[GameInventory] = []
        for game_id in all_game_ids:
            game = session.get(Game, game_id)
            if not game:
                continue

            inv = GameInventory(
                game_id=game.id,
                game_name=game.canonical_name,
            )

            # Gameplay stats
            for g, count, total_dur in games_with_gameplay:
                if g.id == game.id:
                    inv.gameplay_sources_ready = count
                    inv.total_gameplay_duration = total_dur or 0.0
                    break

            # Count usable clips (GameplayAsset) for this game — only sources
            # that are ready AND owned by user OR public.
            # A source with status=ready but 0 clips is NOT usable.
            inv.gameplay_clips_available = session.execute(
                select(func.count(GameplayAsset.id))
                .join(GameplaySource, GameplayAsset.source_id == GameplaySource.id)
                .where(GameplaySource.game_id == game.id)
                .where(GameplaySource.ingestion_status == IngestionStatus.ready.value)
                .where(
                    (GameplaySource.user_id == user_id) |
                    (GameplaySource.is_public == True)
                )
            ).scalar() or 0

            # Total gameplay sources (including non-ready)
            inv.gameplay_sources_total = session.execute(
                select(func.count(GameplaySource.id))
                .where(GameplaySource.user_id == user_id)
                .where(GameplaySource.game_id == game.id)
            ).scalar() or 0

            # Knowledge stats
            inv.facts_available = session.execute(
                select(func.count(Fact.id))
                .where(Fact.user_id == user_id)
                .where(Fact.game_id == game.id)
            ).scalar() or 0

            inv.facts_unused = session.execute(
                select(func.count(Fact.id))
                .where(Fact.user_id == user_id)
                .where(Fact.game_id == game.id)
                .where(Fact.used_count == 0)
            ).scalar() or 0

            inv.knowledge_chunks = session.execute(
                select(func.count(KnowledgeChunk.id))
                .where(KnowledgeChunk.user_id == user_id)
                .where(KnowledgeChunk.game_id == game.id)
            ).scalar() or 0

            # V2: KnowledgeItems (content ideas from RSS/Wikipedia)
            inv.knowledge_items = session.execute(
                select(func.count(KnowledgeItem.id))
                .where(
                    KnowledgeItem.status == KnowledgeItemStatus.fresh.value,
                    KnowledgeItem.game_id == game.id,
                    ((KnowledgeItem.user_id == user_id) | (KnowledgeItem.user_id.is_(None))),
                )
            ).scalar() or 0

            # History: videos produced for this game
            inv.videos_produced = session.execute(
                select(func.count(ContentPlan.id))
                .where(ContentPlan.user_id == user_id)
                .where(ContentPlan.game_id == game.id)
            ).scalar() or 0

            # Recent topics for this game
            recent_plans = session.execute(
                select(ContentPlan.topic)
                .where(ContentPlan.user_id == user_id)
                .where(ContentPlan.game_id == game.id)
                .order_by(desc(ContentPlan.created_at))
                .limit(5)
            ).scalars().all()
            inv.recent_topics = [t for t in recent_plans if t]

            inventory.append(inv)

        return inventory

    def _get_recent_history(self, session: Session, user_id: int) -> dict:
        """Get recent editorial history for diversity."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.recent_days)

        recent_plans = session.execute(
            select(ContentPlan)
            .where(ContentPlan.user_id == user_id)
            .order_by(desc(ContentPlan.created_at))
            .limit(self.history_window)
        ).scalars().all()

        return {
            "recent_topics": [p.topic for p in recent_plans if p.topic],
            "recent_game_ids": [p.game_id for p in recent_plans if p.game_id],
            "recent_fact_ids": [p.fact_id for p in recent_plans if p.fact_id],
            "total_videos": len(recent_plans),
        }

    def _llm_decision(
        self,
        inventory: list[GameInventory],
        history: dict,
        session: Session,
        user_id: int,
    ) -> EditorialDecision:
        """Use LLM to make an editorial decision."""
        # Only show games that have gameplay available — the LLM cannot
        # produce a video for a game without gameplay assets.
        playable = [g for g in inventory if g.has_gameplay]
        if not playable:
            return self._heuristic_decision(inventory, history)

        games_info = [g.to_prompt_dict() for g in playable]
        recent_topics = history["recent_topics"][:10]

        # Load channel profile for context
        channel_context = ""
        try:
            from gpcg.core.models import ChannelProfile

            profile = session.query(ChannelProfile).filter(
                ChannelProfile.user_id == user_id
            ).first()
            if profile:
                channel_context = profile.to_prompt_context()
        except Exception:
            pass

        prompt = (
            f"Você é o editor-chefe de um canal no YouTube. Seu trabalho é decidir "
            f"qual vídeo produzir a seguir.\n\n"
            f"## Perfil do canal\n{channel_context}\n\n"
            f"## Jogos disponíveis (com gameplays e/ou conhecimento)\n"
            f"{json.dumps(games_info, indent=2, ensure_ascii=False)}\n\n"
            f"## Vídeos já produzidos recentemente (evite repetir)\n"
            f"{json.dumps(recent_topics, ensure_ascii=False) if recent_topics else 'Nenhum vídeo ainda'}\n\n"
            f"## Sua decisão\n"
            f"Escolha UM jogo para o próximo vídeo. Priorize:\n"
            f"1. Jogos que têm gameplays E conhecimento (facts/chunks)\n"
            f"2. Jogos com menos vídeos produzidos (para variar)\n"
            f"3. Facts não utilizados (unused_facts > 0)\n"
            f"4. Evite repetir temas dos vídeos recentes\n\n"
            f"Responda em JSON:\n"
            f'{{"game_name": "nome do jogo", "reason": "por que escolheu este jogo"}}\n'
        )

        system = (
            "Você é um editor de canal do YouTube especializado em games. "
            "Você decide o conteúdo de forma autônoma, maximizando variedade "
            "e qualidade. Responda sempre em JSON válido."
        )

        try:
            data = self.llm.chat_json(system, prompt, temperature=0.7, max_tokens=500)
        except Exception as e:
            log.warning(f"editorial LLM call failed: {e}")
            raise

        chosen_name = (data.get("game_name") or "").strip()
        reason = (data.get("reason") or "").strip()

        # Find the game by name (fuzzy match)
        chosen_game = None
        for inv in inventory:
            if inv.game_name.lower() == chosen_name.lower():
                chosen_game = inv
                break
        if not chosen_game:
            # Fuzzy match: check if chosen_name is contained in game name
            for inv in inventory:
                if chosen_name.lower() in inv.game_name.lower() or inv.game_name.lower() in chosen_name.lower():
                    chosen_game = inv
                    break
        if not chosen_game:
            log.warning(f"editorial: LLM chose '{chosen_name}' but no matching game found")
            return self._heuristic_decision(inventory, history)

        # Pick a fact for this game (prefer unused)
        fact_id = self._pick_fact(session, user_id, chosen_game.game_id, history)

        return EditorialDecision(
            job_type="generate_short",
            game_id=chosen_game.game_id,
            fact_id=fact_id,
            topic_hint=reason,
            reason=f"LLM escolheu {chosen_game.game_name}: {reason}",
        )

    def _llm_decision_from_data(
        self,
        inventory: list[GameInventory],
        history: dict,
        channel_context: str = "",
        general_ideas: list[dict] | None = None,
    ) -> EditorialDecision:
        """Use LLM to make an editorial decision from API-provided data.

        This is the headless version of _llm_decision — it doesn't need
        a DB session. The channel_context and history are provided by
        the caller (fetched from the VPS API).

        V2: Now also considers general KnowledgeItems (content ideas without
        a specific game) for curiosity_short videos. The LLM can choose
        between a game-specific video (generate_short) or a general curiosity
        video (curiosity_short) with any of the user's gameplays as background.
        """
        # V3: Removed gameplay-as-prerequisite. Games with knowledge (not just
        # gameplay) are candidates. Gameplay is chosen AFTER the subject.
        games_with_knowledge = [g for g in inventory if g.has_knowledge]
        gameplay_games = [g for g in inventory if g.has_gameplay]
        gameplay_names = [g.game_name for g in gameplay_games]

        if not games_with_knowledge and not general_ideas:
            return self._heuristic_decision(inventory, history)

        # ── Deterministic saturation signals ───────────────────────────
        recent_topics = history.get("recent_topics", [])[:10]
        recent_game_ids = history.get("recent_game_ids", [])[:10]
        recent_game_counts: dict[int, int] = {}
        for gid in recent_game_ids:
            recent_game_counts[gid] = recent_game_counts.get(gid, 0) + 1
        # Saturated: appeared in 2+ of recent videos
        saturated_game_ids = {gid for gid, cnt in recent_game_counts.items() if cnt >= 2}

        # Pre-LLM override: if 3+ recent videos same game + general ideas exist
        if saturated_game_ids and general_ideas:
            max_sat = max(recent_game_counts.values())
            if max_sat >= 3:
                best_idea = max(general_ideas, key=lambda i: i.get("editorial_score", 0))
                bg_game = gameplay_games[0] if gameplay_games else None
                log.info(f"editorial: saturation override ({max_sat}x same game) → curiosity_short #{best_idea['id']}")
                return EditorialDecision(
                    job_type="curiosity_short",
                    background_game_id=bg_game.game_id if bg_game else None,
                    fact_id=None,
                    topic_hint=best_idea.get("title", ""),
                    reason=f"saturação: {max_sat} vídeos do mesmo jogo → ideia #{best_idea['id']}",
                )

        games_info = [g.to_prompt_dict() for g in games_with_knowledge]
        for g_info, g_inv in zip(games_info, games_with_knowledge):
            if g_inv.game_id in saturated_game_ids:
                g_info["_saturated"] = True

        recent_game_names = []
        for inv in inventory:
            if inv.game_id in set(recent_game_ids[:5]):
                recent_game_names.append(inv.game_name)
        recent_games_str = (
            ", ".join(recent_game_names) if recent_game_names else "Nenhum"
        )

        # V2: Build general ideas section
        ideas_section = ""
        if general_ideas:
            ideas_json = [
                {"id": idea["id"], "title": idea["title"], "type": idea.get("item_type", ""),
                 "score": idea.get("editorial_score", 0)}
                for idea in general_ideas[:15]
            ]
            ideas_section = (
                f"## Ideias de conteúdo gerais (notícias/curiosidades da indústria)\n"
                f"Estas ideias podem virar vídeos de curiosidade geral (curiosity_short) "
                f"com gameplay de qualquer jogo como fundo.\n"
                f"{json.dumps(ideas_json, indent=2, ensure_ascii=False)}\n\n"
            )

        prompt = (
            f"Você é o editor-chefe de um canal no YouTube. Seu trabalho é decidir "
            f"qual vídeo produzir a seguir.\n\n"
            f"## Perfil do canal\n{channel_context}\n\n"
            f"## Jogos com conhecimento disponível (facts/knowledge_items)\n"
            f"Marcados como _saturated=true se já apareceram muito recentemente.\n"
            f"{json.dumps(games_info, indent=2, ensure_ascii=False)}\n\n"
            f"{ideas_section}"
            f"## Vídeos já produzidos recentemente (evite repetir)\n"
            f"{json.dumps(recent_topics, ensure_ascii=False) if recent_topics else 'Nenhum vídeo ainda'}\n\n"
            f"## Jogos já usados recentemente (evite repetir)\n"
            f"{recent_games_str}\n\n"
            f"## Jogos com gameplay disponível para fundo visual\n"
            f"{', '.join(gameplay_names) if gameplay_names else 'Nenhum'}\n\n"
            f"## Sua decisão\n"
            f"Escolha o próximo vídeo considerando:\n"
            f"1. VARIEDADE: se os últimos vídeos foram do mesmo jogo, escolha outro assunto\n"
            f"2. QUALIDADE: ideias com maior editorial_score são preferíveis\n"
            f"3. NOVIDADE: assuntos ainda não cobertos têm prioridade\n"
            f"4. SATURAÇÃO: jogos marcados como _saturated devem ser evitados\n\n"
            f"O vídeo pode ser:\n"
            f"- generate_short: curiosidade SOBRE um jogo específico que tem conhecimento\n"
            f"- curiosity_short: ideia geral (notícia/curiosidade da indústria) com gameplay como fundo\n"
            f"  (a gameplay é escolhida depois como suporte visual — não precisa ser do mesmo jogo)\n\n"
            f"Responda em JSON:\n"
            f'{{"format": "generate_short" ou "curiosity_short", '
            f'"game_name": "nome do jogo (para generate_short)", '
            f'"idea_id": <id da ideia (para curiosity_short)>, '
            f'"reason": "por que escolheu"}}\n'
        )

        system = (
            "Você é um editor de canal do YouTube especializado em games. "
            "Você decide o conteúdo de forma autônoma, maximizando variedade "
            "e qualidade. O assunto do vídeo é decidido pelo seu valor editorial, "
            "não pela gameplay disponível. Responda sempre em JSON válido."
        )

        data = self.llm.chat_json(system, prompt, temperature=0.7, max_tokens=500)

        chosen_format = (data.get("format") or data.get("job_type") or "generate_short").strip()
        reason = (data.get("reason") or "").strip()

        # Handle curiosity_short (general idea with background gameplay)
        if chosen_format == "curiosity_short" and data.get("idea_id"):
            idea_id = int(data["idea_id"])
            # Pick the game with most gameplay for background
            if gameplay_games:
                # Sort by: least videos produced, then most gameplay duration
                gameplay_games.sort(key=lambda g: (g.videos_produced, -g.total_gameplay_duration))
                bg_game = gameplay_games[0]
                return EditorialDecision(
                    job_type="curiosity_short",
                    background_game_id=bg_game.game_id,
                    fact_id=None,
                    topic_hint=reason,
                    reason=f"LLM escolheu ideia #{idea_id}: {reason}",
                )
            else:
                log.warning("curiosity_short chosen but no gameplay games available")
                return self._heuristic_decision(inventory, history, general_ideas)

        # Handle generate_short (game-specific)
        chosen_name = (data.get("game_name") or "").strip()

        # Find the game by name (fuzzy match)
        chosen_game = None
        for inv in inventory:
            if inv.game_name.lower() == chosen_name.lower():
                chosen_game = inv
                break
        if not chosen_game:
            for inv in inventory:
                if chosen_name.lower() in inv.game_name.lower() or inv.game_name.lower() in chosen_name.lower():
                    chosen_game = inv
                    break
        if not chosen_game:
            log.warning(f"editorial: LLM chose '{chosen_name}' but no matching game found")
            return self._heuristic_decision(inventory, history, general_ideas)

        # Post-LLM saturation check: if LLM chose a saturated game, override
        if chosen_game.game_id in saturated_game_ids and general_ideas:
            best_idea = max(general_ideas, key=lambda i: i.get("editorial_score", 0))
            bg_game = gameplay_games[0] if gameplay_games else None
            log.info(f"editorial: LLM chose saturated game {chosen_game.game_name}, overriding → idea #{best_idea['id']}")
            return EditorialDecision(
                job_type="curiosity_short",
                background_game_id=bg_game.game_id if bg_game else None,
                fact_id=None,
                topic_hint=best_idea.get("title", ""),
                reason=f"override: jogo saturado → ideia #{best_idea['id']}",
            )

        return EditorialDecision(
            job_type="generate_short",
            game_id=chosen_game.game_id,
            fact_id=None,  # Fact picking is done by the caller (from API data)
            topic_hint=reason,
            reason=f"LLM escolheu {chosen_game.game_name}: {reason}",
        )

    def _pick_fact(
        self,
        session: Session,
        user_id: int,
        game_id: int,
        history: dict,
    ) -> Optional[int]:
        """Pick the best unused fact for a game."""
        recent_fact_ids = set(history.get("recent_fact_ids", []))

        # Prefer unused facts, then least-used
        facts = session.execute(
            select(Fact)
            .where(Fact.user_id == user_id)
            .where(Fact.game_id == game_id)
            .order_by(Fact.used_count.asc(), Fact.quality_score.desc())
            .limit(10)
        ).scalars().all()

        if not facts:
            return None

        # Filter out recently used facts
        fresh_facts = [f for f in facts if f.id not in recent_fact_ids]
        if fresh_facts:
            return fresh_facts[0].id
        return facts[0].id

    def _heuristic_decision(
        self,
        inventory: list[GameInventory],
        history: dict,
        general_ideas: list[dict] | None = None,
    ) -> EditorialDecision:
        """Fallback: pick the best candidate without LLM.

        V3: Now considers general ideas for curiosity_short. If there are
        high-quality general ideas and the recent history is saturated with
        game-specific content, prefers curiosity_short.
        """
        # Check if recent history is saturated with same games
        recent_game_ids = history.get("recent_game_ids", [])[:5]
        recent_game_counts: dict[int, int] = {}
        for gid in recent_game_ids:
            recent_game_counts[gid] = recent_game_counts.get(gid, 0) + 1
        saturated_game_ids = {gid for gid, cnt in recent_game_counts.items() if cnt >= 2}

        # If we have general ideas and recent history is saturated, prefer curiosity_short
        if general_ideas and saturated_game_ids:
            best_idea = max(general_ideas, key=lambda i: i.get("editorial_score", 0))
            if best_idea.get("editorial_score", 0) >= 30:
                gameplay_games = [g for g in inventory if g.has_gameplay]
                bg_game = gameplay_games[0] if gameplay_games else None
                return EditorialDecision(
                    job_type="curiosity_short",
                    background_game_id=bg_game.game_id if bg_game else None,
                    fact_id=None,
                    topic_hint=best_idea.get("title", ""),
                    reason=f"heurística: saturação detectada → ideia geral #{best_idea['id']}",
                )

        # Also: if no producible games but general ideas exist, use curiosity_short
        producible = [g for g in inventory if g.is_producible]
        if not producible and general_ideas:
            best_idea = max(general_ideas, key=lambda i: i.get("editorial_score", 0))
            gameplay_games = [g for g in inventory if g.has_gameplay]
            bg_game = gameplay_games[0] if gameplay_games else None
            return EditorialDecision(
                job_type="curiosity_short",
                background_game_id=bg_game.game_id if bg_game else None,
                fact_id=None,
                topic_hint=best_idea.get("title", ""),
                reason=f"heurística: sem jogos_producíveis → ideia geral #{best_idea['id']}",
            )

        if not producible:
            producible = [g for g in inventory if g.has_gameplay]
        if not producible:
            return EditorialDecision(
                job_type="generate_short",
                success=False,
                error="No producible games found",
            )

        # Sort by: fewest videos, then most unused facts, then most gameplay
        producible.sort(
            key=lambda g: (
                g.videos_produced,
                -g.facts_unused,
                -g.total_gameplay_duration,
            )
        )

        chosen = producible[0]
        return EditorialDecision(
            job_type="generate_short",
            game_id=chosen.game_id,
            fact_id=None,  # Let content planner pick
            topic_hint="",
            reason=f"heurística: {chosen.game_name} tem {chosen.videos_produced} vídeos, "
            f"{chosen.facts_unused} facts não usados",
        )
