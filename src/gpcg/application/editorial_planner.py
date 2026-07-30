"""Editorial planner — produces a VideoCreativePlan before script generation.

This is the editorial brain of the pipeline. It analyzes:
  - The topic/fact being covered
  - The gameplay available (from the semantic index)
  - Whether the video is GAME_RELATED or GENERAL_TOPIC
  - The appropriate tone, humor level, and narrative structure
  - Which LLM model to use (gemma3 for serious, qwen3 for personality)

The planner does NOT write the script. It produces an editorial plan that
the script generator follows. This separation ensures editorial decisions
are explicit and observable, not hidden in prompt instructions.

Key principles:
  - "Ser engraçado" não significa "fazer uma piada a cada 20 segundos"
  - If there's no genuine funny observation, humor.enabled=false
  - Qwen3 with humor.intensity=low ≠ "make jokes" — it means "use creative
    capacity for natural observations, but don't force comedy"
  - The plan has a CENTRAL IDEA (thesis), not just a collection of facts
  - The narrative has an arc: hook → context → development → escalation → payoff → conclusion
"""

from __future__ import annotations

import time
from typing import Optional

from gpcg.config import get_settings
from gpcg.domain.creative_plan import (
    HUMOR_INTENSITY_LOW,
    HUMOR_INTENSITY_MEDIUM_LOW,
    HUMOR_INTENSITY_NONE,
    VIDEO_TYPE_GAME_RELATED,
    VIDEO_TYPE_GENERAL_TOPIC,
    HumorPlan,
    ModelRecommendation,
    NarrativeBeat,
    ScriptReview,
    ToneWeights,
    VideoCreativePlan,
)
from gpcg.domain.models import ContentPlan, Fact, Game, GameplayEvent, GameplaySource
from gpcg.infrastructure.llm import LLMClient, LLMError
from gpcg.logging import get_logger
from sqlalchemy.orm import Session

log = get_logger(__name__)


# ── Prompts ──────────────────────────────────────────────────────────────────

PLANNER_SYSTEM = """You are an EDITORIAL PLANNER for a Brazilian gaming YouTube Shorts channel.
Your job is NOT to write the script. Your job is to decide HOW the video should be made.

You analyze the topic, the available gameplay, and produce a VideoCreativePlan that the scriptwriter will follow.

## Core Principles

1. CENTRAL IDEA: Every video needs a thesis — one core idea that the video explores. Not a list of facts, but a perspective.

2. NARRATIVE ARC: The video must go somewhere. Structure:
   - hook: grabs attention in 3 seconds
   - context: sets up the topic
   - development: explores the idea
   - escalation: raises the stakes or reveals something surprising
   - payoff: delivers on the promise
   - conclusion: lands the idea

3. HUMOR IS TEMPERO, NOT THE MAIN COURSE:
   - "Ser engraçado" ≠ "fazer uma piada a cada 20 segundos"
   - If there's no genuine funny observation, set humor.enabled=false
   - Low humor = occasional natural observations, NOT forced jokes
   - Medium-low = a few well-placed comments, still mostly informative
   - SILENCE IS BETTER THAN A BAD JOKE

4. AVOID AI HUMOR PATTERNS:
   - "Já imaginou se..." / "Imagine um jogo onde..."
   - "Isso é mais X do que Y" / "É como se X encontrasse Y"
   - "O jogo basicamente disse: agora é guerra!"
   - "E aí você percebe que..."
   - "Prepare-se para..." / "Você não vai acreditar..."
   These are NOT funny. They are AI trying to sound funny.

5. GOOD HUMOR COMES FROM:
   - observation: noticing something genuinely curious
   - sarcasm: saying something seriously when context makes it funny
   - wording: a normal sentence made funny by construction
   - understatement: treating something absurd as completely normal
   - dry_commentary: a short observation beats an elaborate punchline
   - contextual: humor that depends on what was just said/shown

6. MODEL SELECTION:
   - gemma3: for serious, informative, documental, neutral tone videos
   - qwen3: when there's space for personality, commentary, sarcasm, observations
   - Qwen3 does NOT mean "make jokes" — it means "more creative capacity for natural language"
   - qwen3 with humor.intensity=low = use creativity for observations, NOT comedy

7. VIDEO TYPES:
   - GAME_RELATED: the video is ABOUT the game. Gameplay matches the topic.
   - GENERAL_TOPIC: the video is about something else. Gameplay is visual background only.

8. SCRIPT SHOULD SOUND SPOKEN, NOT WRITTEN:
   - Natural phrasing, varied rhythm, pauses
   - Short sentences mixed with longer ones
   - No essay structure, no "Neste vídeo iremos explorar..."
   - Like someone telling you about something, not reading an article

## Output

Return ONLY valid JSON (no markdown, no text before or after):
{
  "video_type": "GAME_RELATED|GENERAL_TOPIC",
  "central_idea": "The thesis of this video in 1-2 sentences.",
  "narrative_beats": [
    {"label": "hook", "description": "what the hook does", "content_type": "observation"},
    {"label": "context", "description": "...", "content_type": "fact"},
    {"label": "development", "description": "...", "content_type": "fact"},
    {"label": "escalation", "description": "...", "content_type": "commentary"},
    {"label": "payoff", "description": "...", "content_type": "observation"},
    {"label": "conclusion", "description": "...", "content_type": "conclusion"}
  ],
  "tone": {
    "informative": 0.8,
    "casual": 0.6,
    "sarcastic": 0.2,
    "comedic": 0.1,
    "dramatic": 0.1,
    "nostalgic": 0.0,
    "mysterious": 0.0,
    "energetic": 0.3
  },
  "humor": {
    "enabled": true,
    "intensity": "none|low|medium-low|medium|high",
    "styles": ["observation", "sarcasm", "wording"],
    "frequency": "sparse|occasional|frequent"
  },
  "gameplay_strategy": "related|background_filler|thematic_match",
  "visual_dependency": "high|medium|low",
  "gameplay_query": "semantic query for finding relevant gameplay clips, e.g. 'character being chased' or empty if background_filler",
  "model_recommendation": "gemma3:12b or qwen3:14b",
  "model_reason": "Why this model was chosen for this video."
}"""


class EditorialPlanner:
    """Produces a VideoCreativePlan from a ContentPlan + gameplay context.

    The planner makes editorial decisions BEFORE the script is written:
    video type, central idea, narrative arc, tone, humor level, model selection.
    """

    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self.llm = llm or LLMClient()
        self.settings = get_settings()

    def plan(
        self,
        session: Session,
        content_plan: ContentPlan,
        *,
        job_type: str = "generate_short",
        background_game_id: Optional[int] = None,
    ) -> VideoCreativePlan:
        """Analyze the content plan and produce a VideoCreativePlan.

        Args:
            session: DB session for querying gameplay index
            content_plan: the content plan (topic, hook, tone, fact)
            job_type: "generate_short" (GAME_RELATED) or "curiosity_short" (GENERAL_TOPIC)
            background_game_id: for curiosity shorts, the game providing background gameplay

        Returns:
            VideoCreativePlan with all editorial decisions
        """
        t0 = time.time()
        s = self.settings

        if not s.gpcg_editorial_planning_enabled:
            return VideoCreativePlan.empty("editorial planning disabled")

        # Determine video type from job type
        video_type = (
            VIDEO_TYPE_GENERAL_TOPIC if job_type == "curiosity_short"
            else VIDEO_TYPE_GAME_RELATED
        )

        # Gather context for the planner
        context = self._gather_context(
            session, content_plan, video_type, background_game_id
        )

        # Build the user prompt
        user_prompt = self._build_user_prompt(content_plan, video_type, context)

        # Call the LLM
        try:
            data = self.llm.chat_json(
                system=PLANNER_SYSTEM,
                prompt=user_prompt,
                model=s.gpcg_llm_model,  # use default text model for planning
                temperature=s.gpcg_editorial_temperature,
                max_tokens=s.gpcg_editorial_max_tokens,
            )
        except LLMError as e:
            log.error(f"editorial planner LLM failed: {e}")
            return VideoCreativePlan.empty(f"LLM error: {e}")

        # Parse the response into a VideoCreativePlan
        plan = self._parse_plan(data, video_type, content_plan)
        plan.latency_ms = int((time.time() - t0) * 1000)
        plan.gameplay_compatibility = context.get("compatibility", {})

        log.info(
            f"editorial plan: type={plan.video_type} model={plan.model.model} "
            f"humor={plan.humor.enabled}/{plan.humor.intensity} "
            f"beats={len(plan.narrative_beats)} latency={plan.latency_ms}ms"
        )

        return plan

    def _gather_context(
        self,
        session: Session,
        plan: ContentPlan,
        video_type: str,
        background_game_id: Optional[int],
    ) -> dict:
        """Gather gameplay context for the planner prompt."""
        context: dict = {
            "gameplay_events": [],
            "compatibility": {},
            "game_name": "",
        }

        # Get the game name
        game_id = plan.game_id or background_game_id
        if game_id:
            game = session.get(Game, game_id)
            if game:
                context["game_name"] = game.canonical_name

        # Query gameplay events from the semantic index
        select_game_id = background_game_id if video_type == VIDEO_TYPE_GENERAL_TOPIC else plan.game_id
        if select_game_id:
            from sqlalchemy import select
            sources = session.execute(
                select(GameplaySource).where(
                    GameplaySource.game_id == select_game_id,
                    GameplaySource.ingestion_status == "ready",
                )
            ).scalars().all()

            for src in sources:
                compat = src.compatibility
                context["compatibility"][src.id] = compat

                # If analysis is ready, get interesting events
                if src.is_analysis_ready:
                    events = session.execute(
                        select(GameplayEvent)
                        .where(GameplayEvent.source_id == src.id)
                        .where(GameplayEvent.interesting_score >= 0.4)
                        .order_by(GameplayEvent.interesting_score.desc())
                        .limit(10)
                    ).scalars().all()

                    for ev in events:
                        context["gameplay_events"].append({
                            "source_id": src.id,
                            "start": ev.start_time,
                            "end": ev.end_time,
                            "type": ev.event_type,
                            "description": ev.description[:100],
                            "interesting": ev.interesting_score,
                        })

        return context

    def _build_user_prompt(self, plan: ContentPlan, video_type: str, context: dict) -> str:
        """Build the user prompt for the planner LLM call."""
        parts = [
            f"VIDEO TYPE: {video_type}",
            f"",
            f"TOPIC: {plan.topic}",
            f"HOOK (suggested by content planner): {plan.hook}",
            f"TONE (suggested): {plan.tone}",
            f"TARGET DURATION: {plan.target_duration}s",
        ]

        if context.get("game_name"):
            parts.append(f"GAME: {context['game_name']}")

        # Add fact claim if available (CRITICAL for gameplay_query generation)
        if plan.fact_id:
            from sqlalchemy import select
            from gpcg.domain.models import Fact
            # The fact claim is the single source of truth for what the video
            # is about. Without it, the planner can't generate a meaningful
            # gameplay_query for semantic clip retrieval.
            from gpcg.infrastructure.database import session_scope
            with session_scope() as sess:
                fact = sess.get(Fact, plan.fact_id)
                if fact:
                    parts.append(f"\nFACT TO TELL (source of truth): {fact.claim}")
                    parts.append(f"  (This is the ONLY fact the video should cover. Do NOT invent additional mechanics.)")

        # Add gameplay context
        events = context.get("gameplay_events", [])
        if events:
            parts.append(f"\nAVAILABLE GAMEPLAY EVENTS (top {len(events)} by interesting score):")
            for ev in events[:8]:
                parts.append(
                    f"  [{ev['start']:.0f}-{ev['end']:.0f}s] {ev['type']}: {ev['description']} "
                    f"(interesting={ev['interesting']:.2f})"
                )
            parts.append(
                f"\nCRITICAL: When gameplay_strategy is 'related' or 'thematic_match', "
                f"you MUST provide a gameplay_query (short keyword(s) from the events above "
                f"that match the FACT). This query is used for semantic search over event "
                f"tags, descriptions, and actions. Use simple keywords like 'skate', "
                f"'bicycle', 'combat', 'driving' — not full sentences. Leave empty ONLY "
                f"when strategy is 'background_filler'."
            )
        else:
            parts.append("\nNo semantic gameplay index available (will use random selection).")

        parts.append(f"\nProduce the VideoCreativePlan JSON now.")

        return "\n".join(parts)

    def _extract_gameplay_query_from_plan(
        self, content_plan: Optional[ContentPlan]
    ) -> str:
        """Extract a gameplay search keyword from the content plan's fact claim.

        Fallback when the LLM doesn't generate a gameplay_query. Looks for
        gameplay-relevant keywords (skate, bike, car, combat, fight, weapon,
        etc.) in the fact claim and topic. Returns the first match or empty.
        """
        if content_plan is None or not content_plan.fact_id:
            return ""
        from gpcg.infrastructure.database import session_scope
        from gpcg.domain.models import Fact
        text = ""
        with session_scope() as sess:
            fact = sess.get(Fact, content_plan.fact_id)
            if fact:
                text = f"{fact.claim} {content_plan.topic}".lower()
        if not text:
            return ""
        # Gameplay action keywords (pt-BR + en) that map to cascaded pipeline tags
        keywords = [
            "skate", "bicycle", "bicicleta", "bike", "carro", "car",
            "combat", "luta", "fight", "weapon", "arma",
            "neve", "snow", "food", "comida",
            "corrimao", "railing", "slide", "deslizar",
            "ninja", "furtivo", "stealth",
            "frisbee", "bola", "ball",
            "privada", "toilet", "swirly",
            "sprinkler", "aspersor",
            "tatuagem", "tattoo",
            "rato", "rat",
            "banho", "water",
        ]
        for kw in keywords:
            if kw in text:
                return kw
        return ""

    def _parse_plan(
        self, data: dict, fallback_video_type: str, content_plan: Optional[ContentPlan] = None
    ) -> VideoCreativePlan:
        """Parse the LLM JSON response into a VideoCreativePlan."""
        if not isinstance(data, dict):
            return VideoCreativePlan.empty("invalid planner response")

        # Parse video type
        video_type = data.get("video_type", fallback_video_type)
        if video_type not in (VIDEO_TYPE_GAME_RELATED, VIDEO_TYPE_GENERAL_TOPIC):
            video_type = fallback_video_type

        # Parse central idea
        central_idea = str(data.get("central_idea", "")).strip()

        # Parse narrative beats
        beats_data = data.get("narrative_beats", [])
        beats = []
        if isinstance(beats_data, list):
            for b in beats_data:
                if isinstance(b, dict):
                    beats.append(NarrativeBeat(
                        label=str(b.get("label", "")),
                        description=str(b.get("description", "")),
                        content_type=str(b.get("content_type", "fact")),
                    ))

        # Parse tone
        tone = ToneWeights.from_dict(data.get("tone", {}))

        # Parse humor
        humor = HumorPlan.from_dict(data.get("humor", {}))

        # Parse gameplay strategy
        gameplay_strategy = str(data.get("gameplay_strategy", "background_filler"))
        visual_dependency = str(data.get("visual_dependency", "low"))
        gameplay_query = str(data.get("gameplay_query", "")).strip()

        # Fallback: if the LLM didn't generate a gameplay_query but the strategy
        # is "related" or "thematic_match", extract keywords from the fact claim.
        # This ensures the GameplayRetriever can do semantic search even when
        # the LLM ignores the instruction.
        if not gameplay_query and gameplay_strategy in ("related", "thematic_match"):
            gameplay_query = self._extract_gameplay_query_from_plan(content_plan)

        # Parse model recommendation
        model_name = str(data.get("model_recommendation", ""))
        model_reason = str(data.get("model_reason", ""))

        # Resolve model to actual Ollama tag
        s = self.settings
        if "qwen" in model_name.lower():
            model = s.gpcg_editorial_qwen_model
        elif "gemma" in model_name.lower():
            model = s.gpcg_editorial_gemma_model
        else:
            # Fallback: if humor enabled or casual tone high, use qwen
            if humor.enabled or tone.casual >= 0.6:
                model = s.gpcg_editorial_qwen_model
            else:
                model = s.gpcg_editorial_gemma_model

        return VideoCreativePlan(
            video_type=video_type,
            central_idea=central_idea,
            narrative_beats=beats,
            tone=tone,
            humor=humor,
            gameplay_strategy=gameplay_strategy,
            visual_dependency=visual_dependency,
            gameplay_query=gameplay_query,
            model=ModelRecommendation(model=model, reason=model_reason),
            success=True,
        )
