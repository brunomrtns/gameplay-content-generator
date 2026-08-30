"""Script service — draft → optimize → originality check → final narration.

The script must be:
- In pt-BR
- ~800-1000 chars (for ~60s TTS)
- Suitable for TTS (clean punctuation, no weird symbols)
- Retention-optimized (strong hook, good pacing, no redundancy)
- Factually grounded in the source fact
- ORIGINAL: anti-plagiarism check via n-gram overlap against source documents;
  automatic rewrite if too similar (up to gpcg_max_originality_rewrites)

When a CreativeMaterial is provided (from the CreativeEngine stage), the
draft prompt is enriched with hooks/angles/punchlines/observations to steer
the script toward a more creative, natural, and engaging tone.

When a VideoCreativePlan is provided (from the EditorialPlanner stage), the
draft uses the plan's central idea, narrative beats, tone weights, and humor
plan to orient the script. The model is also selected from the plan.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.domain.creative_plan import StoryConcept, VideoCreativePlan
from gpcg.core.models import (
    ContentPlan,
    Fact,
    Script,
    ScriptStatus,
)
from gpcg.domain.originality import check_originality
from gpcg.domains.games.prompts import (
    DRAFT_SYSTEM,
    OPTIMIZE_SYSTEM,
    PLAN_DRAFT_SYSTEM,
    REVISION_SYSTEM,
    REWRITE_SYSTEM,
)
from gpcg.infrastructure.llm import LLMClient, LLMError
from gpcg.logging import get_logger

if TYPE_CHECKING:
    from gpcg.application.creative_engine import CreativeMaterial

log = get_logger(__name__)




class ScriptService:
    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self.llm = llm
        self.settings = get_settings()

    def generate_script(
        self,
        session: Session,
        content_plan_id: int,
        *,
        creative_material: Optional["CreativeMaterial"] = None,
        creative_plan: Optional[VideoCreativePlan] = None,
        story_concept: Optional[StoryConcept] = None,
        channel_context: str = "",
        knowledge_context: str = "",
        critic_feedback: Optional[str] = None,
        previous_script: Optional[str] = None,
        user_id: Optional[int] = None,
        language_context=None,
    ) -> Optional[Script]:
        """Generate draft → optimize → final script for a content plan.

        When `creative_material` is provided (non-None and successful), the
        draft prompt is enriched with hooks/angles/punchlines/observations
        produced by the CreativeEngine. This steers the script toward a more
        creative and natural tone without changing the underlying LLM.

        When `creative_plan` is provided (non-None and successful), the draft
        uses the plan's central idea, narrative beats, tone, and humor strategy.
        The LLM model is also selected from the plan (gemma3 vs qwen3).

        When `story_concept` is provided (V2, non-None and successful), the
        draft prompt incorporates the angle, curiosity_gap, narrative_hook,
        and frame from the Story Finder. The narrative_hook becomes the
        suggested opening line; the frame informs how the fact is presented.

        When `critic_feedback` + `previous_script` are provided, this is a
        REVISION pass — the script is regenerated using the critic's feedback
        instead of drafting from scratch.
        """
        plan = session.get(ContentPlan, content_plan_id)
        if plan is None:
            raise ValueError(f"content plan #{content_plan_id} not found")

        fact_text = ""
        if plan.fact_id:
            fact = session.get(Fact, plan.fact_id)
            if fact:
                fact_text = fact.claim

        # V2: If plan was based on a KnowledgeItem, use its content as source
        if not fact_text and plan.metadata_json:
            ki_id = plan.metadata_json.get("knowledge_item_id")
            if ki_id:
                from gpcg.core.models import KnowledgeItem
                ki = session.get(KnowledgeItem, ki_id)
                if ki:
                    # Use summary if available, else title, else content
                    fact_text = ki.content[:500] if ki.content else (ki.title or "")

        llm = self.llm or LLMClient()
        s = self.settings

        # ── Language-aware character targets ──────────────────────────────
        # Mandarin Chinese has ~3.5 chars/sec vs ~13-15 for Latin scripts.
        # Use language-aware targets when a language_context is provided;
        # otherwise fall back to config defaults (backward compat).
        if language_context is not None:
            from gpcg.i18n.language_context import get_target_char_range
            lang_min, lang_max = get_target_char_range(
                plan.target_duration, language_context.language
            )
        else:
            lang_min = s.gpcg_narration_min_chars
            lang_max = lang_max

        # Determine the model to use
        # Priority: creative_plan.model > creative_material model > default
        model_override = None
        if creative_plan is not None and creative_plan.success and creative_plan.model.model:
            model_override = creative_plan.model.model
            log.info(f"using plan-recommended model: {model_override}")

        # Build context — game name if game-specific, else "general curiosity"
        if plan.game is not None:
            context_line = f"Game: {plan.game.canonical_name}\n"
        elif plan.background_game is not None:
            context_line = (
                f"Context: General curiosity (NOT about the game)\n"
                f"Background gameplay: {plan.background_game.canonical_name} (just visual filler)\n"
            )
        else:
            context_line = "Context: General curiosity\n"

        # ── Channel context + knowledge (per-channel personalization) ──────
        # The channel profile tells the AI what kind of channel this is
        # (niche, audience, tone, narrative style). The knowledge context
        # provides RAG-retrieved chunks from the user's uploaded documents.
        # Both are injected into the prompt so the script is personalized
        # to the channel rather than generic.
        channel_block = ""
        if channel_context:
            channel_block = f"\nIDENTIDADE DO CANAL:\n{channel_context}\n"
        knowledge_block = ""
        if knowledge_context:
            knowledge_block = f"\n{knowledge_context}\n"

        # ── Revision pass (critic feedback) ────────────────────────────────
        if critic_feedback and previous_script:
            revision_prompt = self._build_revision_prompt(
                plan, fact_text, previous_script, critic_feedback, creative_plan, s
            )
            # Inject channel context + knowledge into revision
            if channel_block or knowledge_block:
                revision_prompt = f"{channel_block}{knowledge_block}\n{revision_prompt}"
            try:
                rev_data = llm.chat_json(
                    REVISION_SYSTEM, revision_prompt,
                    model=model_override,
                    temperature=0.6, max_tokens=2500,
                )
                revised = (rev_data.get("script") or "").strip()
                if not revised:
                    log.error("empty revision script")
                    return None
                # Skip optimize for revisions — the critic already reviewed
                final = revised
                char_count = len(final)

                # Expand if still too short (same logic as initial generation)
                max_expand = 3
                expand_attempt = 0
                while len(final) < lang_min and expand_attempt < max_expand:
                    expand_attempt += 1
                    shortfall = lang_min - len(final)
                    expand_prompt = (
                        f"Current script ({len(final)} chars):\n\n{final}\n\n"
                        f"CRITICAL: This script is {shortfall} characters TOO SHORT. "
                        f"It MUST be at least {lang_min} characters for "
                        f"~{plan.target_duration}s of narration. "
                        f"EXPAND the script by adding more detail, examples, and depth. "
                        f"DO NOT repeat content — add NEW information, insights, and transitions. "
                        f"Keep the same tone ({plan.tone}) and style. "
                        f"Return the full expanded script (not just the additions)."
                    )
                    try:
                        expand_data = llm.chat_json(OPTIMIZE_SYSTEM, expand_prompt, temperature=0.5, max_tokens=3000)
                        expanded = (expand_data.get("script") or "").strip()
                        if expanded and len(expanded) > len(final):
                            final = expanded
                            char_count = len(final)
                            log.info(f"revision expanded (attempt {expand_attempt}): {char_count} chars")
                        else:
                            break
                    except LLMError:
                        break
                # Still run originality check
                source_texts, fact_claims = self._collect_sources(session, plan, user_id=user_id)
                all_sources = list(source_texts)
                if fact_claims:
                    all_sources.append(("extracted_facts", " ".join(fact_claims)))
                report = check_originality(
                    final, all_sources,
                    n=s.gpcg_originality_ngram_size,
                    threshold=s.gpcg_originality_threshold,
                    language=language_context.language if language_context else "pt-BR",
                )
                script = Script(
                    content_plan_id=content_plan_id,
                    draft=previous_script,
                    optimized=revised,
                    final=final,
                    status=ScriptStatus.final.value,
                    char_count=char_count,
                    originality_score=report.score,
                    originality_report=report.to_dict(),
                    rewrite_count=0,
                    language=language_context.language if language_context else "pt-BR",
                )
                session.add(script)
                session.flush()
                log.info(f"revised script #{script.id}: {char_count} chars, originality={report.score:.1f}")
                return script
            except LLMError as e:
                log.error(f"revision failed: {e}")
                return None

        # ── Draft ──────────────────────────────────────────────────────────
        # Choose system prompt: plan-oriented if plan available, else legacy
        if creative_plan is not None and creative_plan.success:
            draft_system = PLAN_DRAFT_SYSTEM
            draft_prompt = self._build_plan_draft_prompt(
                plan, fact_text, creative_plan, s, story_concept=story_concept,
                channel_block=channel_block, knowledge_block=knowledge_block,
            )
        else:
            draft_system = DRAFT_SYSTEM
            draft_prompt = (
                f"{context_line}"
                f"{channel_block}{knowledge_block}"
                f"Topic: {plan.topic}\n"
                f"Tone: {plan.tone}\n"
                f"Energy: {plan.energy}\n"
                f"Hook idea: {plan.hook}\n"
                f"Fact to tell: {fact_text}\n"
                f"Target: {lang_min}-{lang_max} characters, "
                f"~{plan.target_duration} seconds of TTS narration.\n"
                f"CRITICAL: The script MUST be at least {lang_min} characters long. "
                f"Do NOT write a short script. Expand with context, examples, and commentary "
                f"about the fact to fill the time."
            )
            # V2: incorporate StoryConcept even without a creative plan
            if story_concept is not None and story_concept.success:
                draft_prompt += self._format_story_concept(story_concept)

        # Enrich with creative material when available
        if creative_material is not None and creative_material.success:
            draft_prompt += self._format_creative_material(creative_material)
        try:
            draft_data = llm.chat_json(
                draft_system, draft_prompt,
                model=model_override,
                temperature=0.7, max_tokens=2500,
            )
        except LLMError as e:
            log.error(f"draft generation failed: {e}")
            return None
        draft = (draft_data.get("script") or "").strip()
        if not draft:
            log.error("empty draft script")
            return None

        # ── Optimize ────────────────────────────────────────────────────────
        opt_prompt = (
            f"Draft script:\n\n{draft}\n\n"
            f"Optimize for ~{plan.target_duration}s Short, tone={plan.tone}, "
            f"target {lang_min}-{lang_max} characters. "
            f"Current length: {len(draft)} chars. "
            f"{'TOO SHORT — must expand to at least ' + str(lang_min) + ' chars.' if len(draft) < lang_min else ''}"
        )
        try:
            opt_data = llm.chat_json(OPTIMIZE_SYSTEM, opt_prompt, temperature=0.4, max_tokens=2500)
            optimized = (opt_data.get("script") or "").strip()
            if not optimized:
                optimized = draft
        except LLMError as e:
            log.warning(f"optimization failed, using draft: {e}")
            optimized = draft

        # Choose final (prefer optimized if within char bounds, else draft)
        final = optimized
        char_count = len(final)
        if char_count < lang_min or char_count > lang_max:
            # Try draft if it's closer
            if lang_min <= len(draft) <= lang_max:
                final = draft
                char_count = len(final)

        # ── Expand if still too short ───────────────────────────────────────
        # The LLM often produces scripts well below the target. Force a
        # dedicated expansion pass to reach at least gpcg_narration_min_chars.
        max_expand_attempts = 3
        expand_attempt = 0
        while len(final) < lang_min and expand_attempt < max_expand_attempts:
            expand_attempt += 1
            shortfall = lang_min - len(final)
            expand_prompt = (
                f"Current script ({len(final)} chars):\n\n{final}\n\n"
                f"CRITICAL: This script is {shortfall} characters TOO SHORT. "
                f"It MUST be at least {lang_min} characters for "
                f"~{plan.target_duration}s of narration. "
                f"EXPAND the script by adding more detail, examples, and depth. "
                f"DO NOT repeat content — add NEW information, insights, and transitions. "
                f"Keep the same tone ({plan.tone}) and style. "
                f"Return the full expanded script (not just the additions)."
            )
            try:
                expand_data = llm.chat_json(OPTIMIZE_SYSTEM, expand_prompt, temperature=0.5, max_tokens=3000)
                expanded = (expand_data.get("script") or "").strip()
                if expanded and len(expanded) > len(final):
                    final = expanded
                    char_count = len(final)
                    log.info(f"script expanded (attempt {expand_attempt}): {char_count} chars")
                else:
                    log.warning(f"script expansion attempt {expand_attempt} did not produce longer text")
                    break
            except LLMError as e:
                log.warning(f"script expansion failed (attempt {expand_attempt}): {e}")
                break

        # ── Anti-plagiarism: originality check + automatic rewrite ─────────
        # Compare the final script against source documents + fact claims.
        # If too similar, rewrite via LLM and re-check (up to max_rewrites).
        source_texts, fact_claims = self._collect_sources(session, plan, user_id=user_id)
        ngram_n = s.gpcg_originality_ngram_size
        threshold = s.gpcg_originality_threshold
        rewrite_count = 0
        max_rewrites = s.gpcg_max_originality_rewrites

        # Include fact claims as a source to check against
        all_sources = list(source_texts)
        if fact_claims:
            all_sources.append(("extracted_facts", " ".join(fact_claims)))
        report = check_originality(final, all_sources, n=ngram_n, threshold=threshold, language=language_context.language if language_context else "pt-BR")

        log.info(
            f"originality check: score={report.score:.1f} overlap={report.max_overlap:.4f} "
            f"source={report.matched_source} matches={len(report.longest_matches)}"
        )

        while not report.is_original and rewrite_count < max_rewrites:
            rewrite_count += 1
            log.warning(
                f"script not original (score={report.score:.1f}), "
                f"rewriting attempt {rewrite_count}/{max_rewrites}"
            )
            # Show the LLM what matched so it knows what to avoid
            matches_str = "\n".join(f"- \"{m}\"" for m in report.longest_matches[:5])
            rewrite_prompt = (
                f"Current script:\n\n{final}\n\n"
                f"Source text it's too similar to (from '{report.matched_source}'):\n"
                f"{(dict(source_texts).get(report.matched_source, '')[:1500])}\n\n"
                f"Matching phrases to avoid:\n{matches_str}\n\n"
                f"Rewrite COMPLETELY. Same fact, totally different words. "
                f"Target {lang_min}-{lang_max} chars."
            )
            try:
                rw_data = llm.chat_json(REWRITE_SYSTEM, rewrite_prompt, temperature=0.8, max_tokens=1500)
                rewritten = (rw_data.get("script") or "").strip()
                if rewritten and len(rewritten) >= 100:
                    final = rewritten
                    char_count = len(final)
                    # Re-check
                    report = check_originality(final, all_sources, n=ngram_n, threshold=threshold, language=language_context.language if language_context else "pt-BR")
                    log.info(
                        f"rewrite {rewrite_count}: score={report.score:.1f} "
                        f"overlap={report.max_overlap:.4f}"
                    )
                else:
                    log.warning("rewrite produced empty/short result, keeping previous")
                    break
            except LLMError as e:
                log.error(f"rewrite failed: {e}")
                break

        # Final verdict
        if not report.is_original:
            log.warning(
                f"script still not fully original after {rewrite_count} rewrite(s) "
                f"(score={report.score:.1f}). Proceeding but flagging for review."
            )

        script = Script(
            content_plan_id=content_plan_id,
            draft=draft,
            optimized=optimized,
            final=final,
            status=ScriptStatus.final.value,
            char_count=char_count,
            originality_score=report.score,
            originality_report=report.to_dict(),
            rewrite_count=rewrite_count,
            language=language_context.language if language_context else "pt-BR",
        )
        session.add(script)
        session.flush()
        log.info(
            f"script #{script.id} for plan #{content_plan_id}: {char_count} chars, "
            f"originality={report.score:.1f} (rewrites={rewrite_count})"
        )
        return script

    def _format_creative_material(self, material: "CreativeMaterial") -> str:
        """Format CreativeMaterial as an extra prompt section for the draft LLM.

        The material is offered as INSPIRATION, not as text to copy. The
        anti-plagiarism check still runs after, so originality is enforced.
        """
        def _bullet_list(items: list[str], limit: int = 5) -> str:
            if not items:
                return "  (nenhum)\n"
            return "".join(f"  - {it}\n" for it in items[:limit])

        return (
            f"\nMATERIAL CRIATIVO (use como inspiração de tom/estilo, NÃO copie verbatim):\n"
            f"Hooks sugeridos:\n{_bullet_list(material.hooks)}"
            f"Ângulos criativos:\n{_bullet_list(material.angles)}"
            f"Punchlines:\n{_bullet_list(material.punchlines)}"
            f"Observações:\n{_bullet_list(material.observations)}"
            f"Escolha o melhor hook, desenvolva o melhor ângulo, e termine "
            f"com a punchline mais marcante. Mantenha o tom natural e espontâneo.\n"
        )

    def _build_plan_draft_prompt(
        self,
        plan: ContentPlan,
        fact_text: str,
        creative_plan: VideoCreativePlan,
        s,
        *,
        story_concept: Optional[StoryConcept] = None,
        channel_block: str = "",
        knowledge_block: str = "",
    ) -> str:
        """Build the draft prompt oriented by the VideoCreativePlan.

        When a StoryConcept is available (V2), the angle, curiosity_gap,
        narrative_hook, and frame are added so the scriptwriter opens with
        the narrative_hook and frames the fact as the story finder decided.

        When channel_block/knowledge_block are provided, the channel's
        identity and RAG-retrieved knowledge are injected so the script is
        personalized to the channel rather than generic.
        """
        # Context line
        if plan.game is not None:
            context_line = f"Game: {plan.game.canonical_name}\n"
        elif plan.background_game is not None:
            context_line = (
                f"Context: General curiosity (NOT about the game)\n"
                f"Background gameplay: {plan.background_game.canonical_name} (visual filler only)\n"
            )
        else:
            context_line = "Context: General curiosity\n"

        parts = [
            context_line,
        ]
        # Inject channel identity + knowledge before the video type
        if channel_block:
            parts.append(channel_block.strip())
        if knowledge_block:
            parts.append(knowledge_block.strip())
        parts.extend([
            f"VIDEO TYPE: {creative_plan.video_type}",
            f"",
            f"CENTRAL IDEA: {creative_plan.central_idea}",
            f"",
            f"FACT TO TELL: {fact_text}",
            f"",
            f"TARGET: {lang_min}-{lang_max} characters, "
            f"~{plan.target_duration} seconds of TTS narration.",
            f"CRITICAL: The script MUST be at least {lang_min} characters long.",
            f"",
        ])

        # V2: Story Concept — the editorial angle and frame
        if story_concept is not None and story_concept.success:
            parts.append("STORY CONCEPT (the editorial angle — use this to orient the script):")
            parts.append(f"  ANGLE: {story_concept.angle}")
            parts.append(f"  CURIOSITY_GAP: {story_concept.curiosity_gap}")
            parts.append(f"  NARRATIVE_HOOK (suggested opening line): {story_concept.narrative_hook}")
            parts.append(f"  FRAME: {story_concept.frame}")
            if story_concept.is_insight:
                parts.append(f"  This is an INSIGHT — it illuminates the whole. Build toward the 'aha' moment.")
            else:
                parts.append(f"  This is TRIVIA — an isolated detail. Don't force a deeper meaning.")
            parts.append(f"  Open with the narrative_hook (or a variation). Use the frame to present the fact.")
            parts.append("")

        # Narrative beats
        if creative_plan.narrative_beats:
            parts.append("NARRATIVE BEATS (follow this structure, but don't label it):")
            for beat in creative_plan.narrative_beats:
                parts.append(f"  {beat.label}: {beat.description}")
            parts.append("")

        # Tone
        tone = creative_plan.tone
        parts.append(f"TONE WEIGHTS: informative={tone.informative} casual={tone.casual} "
                     f"sarcastic={tone.sarcastic} comedic={tone.comedic} "
                     f"dramatic={tone.dramatic} energetic={tone.energetic}")
        parts.append("")

        # Humor plan
        humor = creative_plan.humor
        parts.append(f"HUMOR PLAN:")
        parts.append(f"  enabled: {humor.enabled}")
        if humor.enabled:
            parts.append(f"  intensity: {humor.intensity}")
            parts.append(f"  styles: {', '.join(humor.styles) if humor.styles else 'any'}")
            parts.append(f"  frequency: {humor.frequency}")
            parts.append(f"  REMEMBER: low intensity = natural observations, NOT jokes.")
            parts.append(f"  REMEMBER: SILENCE > BAD JOKE. If no natural observation, just say it normally.")
        else:
            parts.append(f"  NO HUMOR. Zero jokes. Informative and natural only.")
        parts.append("")

        # Original hook idea from content plan (as inspiration)
        if plan.hook:
            parts.append(f"HOOK INSPIRATION (from content planner): {plan.hook}")
            parts.append(f"Use this as inspiration, but write your own hook that fits the central idea.")
            parts.append("")

        parts.append("Write the narration script now. Follow the editorial plan. Return JSON.")

        return "\n".join(parts)

    def _format_story_concept(self, concept: StoryConcept) -> str:
        """Format a StoryConcept as an extra prompt section for the draft LLM.

        Used when a creative_plan is NOT available (legacy path) but a
        story_concept is. The angle and frame steer the script even without
        a full editorial plan.
        """
        return (
            f"\nSTORY CONCEPT (use this to orient the script):\n"
            f"  ANGLE: {concept.angle}\n"
            f"  CURIOSITY_GAP: {concept.curiosity_gap}\n"
            f"  NARRATIVE_HOOK (suggested opening line): {concept.narrative_hook}\n"
            f"  FRAME: {concept.frame}\n"
            f"Open with the narrative_hook (or a variation). Use the frame to present the fact.\n"
        )

    def _build_revision_prompt(
        self,
        plan: ContentPlan,
        fact_text: str,
        previous_script: str,
        critic_feedback: str,
        creative_plan: Optional[VideoCreativePlan],
        s,
    ) -> str:
        """Build the revision prompt using the critic's feedback."""
        parts = [
            f"PREVIOUS SCRIPT (to be revised):",
            f"---",
            f"{previous_script}",
            f"---",
            f"",
            f"CRITIC FEEDBACK:",
            f"{critic_feedback}",
            f"",
            f"FACT TO TELL (the ONLY verified information): {fact_text}",
            f"",
            f"CRITICAL — ANTI-HALLUCINATION:",
            f"The FACT TO TELL above is the ONLY verified information about this topic.",
            f"If the critic flagged invented gameplay mechanics, features, or details,",
            f"REMOVE them entirely. Do NOT replace them with other invented details.",
            f"Commentary and opinions about the fact are OK — invented mechanics are NOT.",
            f"",
            f"TARGET: {lang_min}-{lang_max} characters.",
            f"",
        ]

        if creative_plan is not None and creative_plan.success:
            parts.append(f"CENTRAL IDEA: {creative_plan.central_idea}")
            parts.append(f"HUMOR: enabled={creative_plan.humor.enabled} intensity={creative_plan.humor.intensity}")
            parts.append("")

        parts.append("Produce the revised script. Address the critic's issues. Return JSON.")

        return "\n".join(parts)

    def _collect_sources(
        self, session: Session, plan: ContentPlan, *, user_id: Optional[int] = None
    ) -> tuple[list[tuple[str, str]], list[str]]:
        """Collect source texts (documents) and fact claims for originality checking.

        For game-specific plans: loads docs/facts for that game.
        For general curiosity plans (game_id=None): loads general docs/facts (game_id IS NULL).

        REFACTORY_V2: applies visibility filter (own + shared pool + public).
        """
        from gpcg.core.models import Document, Fact
        from gpcg.domain.visibility import visible_to_user
        from sqlalchemy import select

        doc_vis = visible_to_user(Document.user_id, Document.is_public, user_id)
        fact_vis = visible_to_user(Fact.user_id, Fact.is_public, user_id)

        # Load documents — game-specific or general (game_id IS NULL)
        if plan.game_id is not None:
            docs = session.execute(
                select(Document).where(Document.game_id == plan.game_id, doc_vis)
            ).scalars().all()
            facts = session.execute(
                select(Fact).where(Fact.game_id == plan.game_id, fact_vis)
            ).scalars().all()
        else:
            # General curiosity — load general docs/facts (game_id IS NULL)
            docs = session.execute(
                select(Document).where(Document.game_id.is_(None), doc_vis)
            ).scalars().all()
            facts = session.execute(
                select(Fact).where(Fact.game_id.is_(None), fact_vis)
            ).scalars().all()

        source_texts: list[tuple[str, str]] = []
        for doc in docs:
            try:
                from gpcg.infrastructure.document_parser import parse_document, DocumentParseError
                text = parse_document(doc.file_path, doc.file_type)
                source_texts.append((doc.filename, text))
            except DocumentParseError:
                continue
            except Exception as e:
                log.debug(f"could not load document {doc.filename}: {e}")
                continue

        fact_claims = [f.claim for f in facts if f.claim]

        return source_texts, fact_claims
