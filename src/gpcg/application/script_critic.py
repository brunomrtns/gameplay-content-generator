"""Script critic — editorial review of generated scripts.

Evaluates scripts across 5 dimensions:
  1. Structure: beginning, development, conclusion, central idea
  2. Naturalness: sounds like speech, no AI-isms, no over-explanation
  3. Humor: jokes work, not forced, remove bad humor (don't replace)
  4. Coherence: consistent tone, doesn't abandon central idea
  5. Gameplay (when applicable): narration matches visuals

Produces a ScriptReview with verdict PASS or REVISE.
If REVISE, provides specific feedback for regeneration.

CRITICAL RULE: When detecting bad humor, the instruction is "REMOVE this passage",
NOT "replace with another joke". Silence > bad joke.

V2: Section-based review. When GPCG_SCRIPT_CRITIC_SECTION_BASED=true, the
critic reviews each SECTION of the script (hook, development, payoff)
separately, producing per-section scores and issues. This gives more
granular feedback — instead of "naturalness=60", you get "hook naturalness=80,
development naturalness=40, payoff naturalness=70". See
docs/EDITORIAL_REFACTOR_PLAN_V2.md §4.5, Fase 5.
"""

from __future__ import annotations

import time
from typing import Optional

from gpcg.config import get_settings
from gpcg.domain.creative_plan import (
    CRITIC_DIMENSIONS,
    CRITIC_VERDICT_PASS,
    CRITIC_VERDICT_REVISE,
    CriticIssue,
    ScriptReview,
    VideoCreativePlan,
)
from gpcg.domains.games.prompts import CRITIC_SYSTEM, SECTION_CRITIC_SYSTEM
from gpcg.infrastructure.llm import LLMClient, LLMError
from gpcg.logging import get_logger

log = get_logger(__name__)



class ScriptCritic:
    """Evaluates scripts and decides PASS or REVISE.

    If REVISE, the script is regenerated with the critic's feedback.
    Up to max_revisions attempts (configurable, default 3).
    """

    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self.llm = llm or LLMClient()
        self.settings = get_settings()

    def review(
        self,
        script_text: str,
        plan: VideoCreativePlan,
        *,
        revision_count: int = 0,
        source_fact: str = "",
    ) -> ScriptReview:
        """Evaluate a script and return a ScriptReview.

        Args:
            script_text: the final narration script text
            plan: the VideoCreativePlan the script was based on
            revision_count: how many revisions have been done (0 = first review)
            source_fact: the original fact the script should be based on
                (used for factual_accuracy checking — detects hallucinations)

        Returns:
            ScriptReview with verdict, scores, issues, and feedback
        """
        t0 = time.time()
        s = self.settings

        if not s.gpcg_script_critic_enabled:
            return ScriptReview(
                verdict=CRITIC_VERDICT_PASS,
                overall_score=100.0,
                dimension_scores={d: 100.0 for d in CRITIC_DIMENSIONS},
                revision_count=revision_count,
            )

        # Build the review prompt
        user_prompt = self._build_prompt(script_text, plan, revision_count, source_fact)

        # Call the LLM
        model = s.gpcg_script_critic_model or None  # empty = default text model
        try:
            data = self.llm.chat_json(
                system=CRITIC_SYSTEM,
                prompt=user_prompt,
                model=model,
                temperature=s.gpcg_script_critic_temperature,
                max_tokens=1024,
            )
        except LLMError as e:
            log.error(f"script critic LLM failed: {e}")
            # On failure, pass the script (don't block the pipeline)
            return ScriptReview(
                verdict=CRITIC_VERDICT_PASS,
                overall_score=70.0,
                dimension_scores={d: 70.0 for d in CRITIC_DIMENSIONS},
                revision_count=revision_count,
                feedback=f"critic failed: {e}",
            )

        review = self._parse_review(data, revision_count)
        review.latency_ms = int((time.time() - t0) * 1000)

        log.info(
            f"script critic: verdict={review.verdict} score={review.overall_score:.1f} "
            f"issues={len(review.issues)} revision={revision_count} "
            f"latency={review.latency_ms}ms"
        )

        return review

    def review_sections(
        self,
        script_text: str,
        plan: VideoCreativePlan,
        *,
        revision_count: int = 0,
        source_fact: str = "",
    ) -> ScriptReview:
        """Evaluate a script SECTION BY SECTION (V2).

        Splits the script into sections (aligned with narrative beats when
        available, else by sentence groups), reviews each section separately,
        and aggregates into a ScriptReview with per-section breakdown stored
        in the review's metadata.

        Gated by GPCG_SCRIPT_CRITIC_SECTION_BASED. When off, falls back to
        the holistic `review` method.
        """
        t0 = time.time()
        s = self.settings

        if not s.gpcg_script_critic_enabled:
            return ScriptReview(
                verdict=CRITIC_VERDICT_PASS,
                overall_score=100.0,
                dimension_scores={d: 100.0 for d in CRITIC_DIMENSIONS},
                revision_count=revision_count,
            )

        # Fall back to holistic review when section-based is disabled
        if not getattr(s, "gpcg_script_critic_section_based", False):
            return self.review(
                script_text, plan, revision_count=revision_count, source_fact=source_fact,
            )

        # Split the script into sections
        sections = self._split_into_sections(script_text, plan)

        # Build the section-based prompt
        user_prompt = self._build_section_prompt(
            script_text, plan, sections, revision_count, source_fact
        )

        # Call the LLM
        model = s.gpcg_script_critic_model or None
        try:
            data = self.llm.chat_json(
                system=SECTION_CRITIC_SYSTEM,
                prompt=user_prompt,
                model=model,
                temperature=s.gpcg_script_critic_temperature,
                max_tokens=2048,
            )
        except LLMError as e:
            log.error(f"section critic LLM failed: {e}")
            return ScriptReview(
                verdict=CRITIC_VERDICT_PASS,
                overall_score=70.0,
                dimension_scores={d: 70.0 for d in CRITIC_DIMENSIONS},
                revision_count=revision_count,
                feedback=f"section critic failed: {e}",
            )

        review = self._parse_section_review(data, revision_count)
        review.latency_ms = int((time.time() - t0) * 1000)

        log.info(
            f"script critic (section-based): verdict={review.verdict} "
            f"score={review.overall_score:.1f} sections={len(sections)} "
            f"issues={len(review.issues)} revision={revision_count} "
            f"latency={review.latency_ms}ms"
        )

        return review

    def _split_into_sections(
        self, script: str, plan: VideoCreativePlan
    ) -> list[dict]:
        """Split a script into sections aligned with narrative beats.

        When the plan has narrative beats, we try to align sections with
        beats. When no beats, we split by sentence groups (first=hook,
        middle=development, last=payoff).

        Returns a list of {"label", "text"} dicts.
        """
        import re as _re

        sentences = _re.split(r'(?<=[.!?])\s+', script.strip())
        sentences = [s for s in sentences if s.strip()]
        if not sentences:
            return [{"label": "full", "text": script}]

        beats = plan.narrative_beats if plan and plan.success else []
        if beats and len(sentences) >= len(beats):
            # Distribute sentences across beats proportionally
            sections = []
            per_beat = max(1, len(sentences) // len(beats))
            idx = 0
            for beat in beats:
                end = idx + per_beat if beat != beats[-1] else len(sentences)
                text = " ".join(sentences[idx:end])
                sections.append({"label": beat.label, "text": text})
                idx = end
            return sections
        elif len(sentences) >= 3:
            # Simple 3-section split: hook / development / payoff
            third = len(sentences) // 3
            return [
                {"label": "hook", "text": " ".join(sentences[:third + 1])},
                {"label": "development", "text": " ".join(sentences[third + 1:2 * third + 1])},
                {"label": "payoff", "text": " ".join(sentences[2 * third + 1:])},
            ]
        else:
            # Too short to split — single section
            return [{"label": "full", "text": script}]

    def _build_section_prompt(
        self,
        script: str,
        plan: VideoCreativePlan,
        sections: list[dict],
        revision_count: int,
        source_fact: str = "",
    ) -> str:
        """Build the user prompt for the section-based critic LLM call."""
        parts = [
            f"CENTRAL IDEA: {plan.central_idea}",
            f"VIDEO TYPE: {plan.video_type}",
            f"HUMOR PLAN: enabled={plan.humor.enabled} intensity={plan.humor.intensity}",
            f"REVISION: this is review #{revision_count + 1}",
            f"",
        ]

        if source_fact:
            parts.append("SOURCE FACT (check each section against this):")
            parts.append("---")
            parts.append(source_fact)
            parts.append("---")
            parts.append("")

        parts.append("SCRIPT SECTIONS (evaluate each separately):")
        parts.append("---")
        for sec in sections:
            parts.append(f"[{sec['label'].upper()}]")
            parts.append(sec["text"])
            parts.append("")
        parts.append("---")
        parts.append("")

        if plan.narrative_beats:
            parts.append("INTENDED NARRATIVE BEATS:")
            for beat in plan.narrative_beats:
                parts.append(f"  {beat.label}: {beat.description}")
            parts.append("")

        parts.append("Evaluate each section separately, then aggregate. Return the JSON.")
        return "\n".join(parts)

    def _parse_section_review(self, data: dict, revision_count: int) -> ScriptReview:
        """Parse the section-based LLM response into a ScriptReview.

        The section-level scores are aggregated into the dimension_scores
        (weighted by section length). Per-section issues are merged into the
        top-level issues list.
        """
        if not isinstance(data, dict):
            return ScriptReview(
                verdict=CRITIC_VERDICT_PASS,
                revision_count=revision_count,
                feedback="invalid section critic response",
            )

        verdict = str(data.get("verdict", CRITIC_VERDICT_PASS)).upper()
        if verdict not in (CRITIC_VERDICT_PASS, CRITIC_VERDICT_REVISE):
            verdict = CRITIC_VERDICT_PASS

        overall = float(data.get("overall_score", 70.0))

        # Parse dimension scores (same as holistic)
        dim_scores = data.get("dimension_scores", {})
        if not isinstance(dim_scores, dict):
            dim_scores = {}
        for d in CRITIC_DIMENSIONS:
            raw = dim_scores.get(d)
            try:
                dim_scores[d] = float(raw) if raw is not None else 70.0
            except (TypeError, ValueError):
                dim_scores[d] = 70.0
        clean_scores = {}
        for k, v in dim_scores.items():
            try:
                clean_scores[k] = float(v) if v is not None else 70.0
            except (TypeError, ValueError):
                pass
        dim_scores = clean_scores

        # Parse issues
        issues_data = data.get("issues", [])
        issues = []
        if isinstance(issues_data, list):
            for i in issues_data:
                if isinstance(i, dict):
                    issues.append(CriticIssue(
                        dimension=str(i.get("dimension", "")),
                        severity=str(i.get("severity", "low")),
                        description=str(i.get("description", "")),
                        location=str(i.get("location", "")),
                        suggestion=str(i.get("suggestion", "")),
                    ))

        # Also parse per-section issues and merge them in
        sections_data = data.get("sections", [])
        if isinstance(sections_data, list):
            for sec in sections_data:
                if not isinstance(sec, dict):
                    continue
                sec_label = str(sec.get("label", "unknown"))
                sec_issues = sec.get("issues", [])
                if isinstance(sec_issues, list):
                    for i in sec_issues:
                        if isinstance(i, dict):
                            issues.append(CriticIssue(
                                dimension=str(i.get("dimension", "")),
                                severity=str(i.get("severity", "low")),
                                description=str(i.get("description", "")),
                                location=f"[{sec_label}] {i.get('location', '')}",
                                suggestion=str(i.get("suggestion", "")),
                            ))

        feedback = str(data.get("feedback", ""))

        return ScriptReview(
            verdict=verdict,
            overall_score=overall,
            dimension_scores=dim_scores,
            issues=issues,
            feedback=feedback,
            revision_count=revision_count,
        )

    def should_revise(self, review: ScriptReview, current_revisions: int) -> bool:
        """Check if the script should be revised based on the review.

        Args:
            review: the ScriptReview
            current_revisions: how many revisions have been done so far

        Returns:
            True if the script should be revised (REVISE + under max)
        """
        if review.verdict != CRITIC_VERDICT_REVISE:
            return False
        if current_revisions >= self.settings.gpcg_script_critic_max_revisions:
            return False
        return True

    def _build_prompt(
        self,
        script: str,
        plan: VideoCreativePlan,
        revision_count: int,
        source_fact: str = "",
    ) -> str:
        """Build the user prompt for the critic LLM call."""
        parts = [
            f"CENTRAL IDEA: {plan.central_idea}",
            f"VIDEO TYPE: {plan.video_type}",
            f"HUMOR PLAN: enabled={plan.humor.enabled} intensity={plan.humor.intensity}",
            f"REVISION: this is review #{revision_count + 1} (0 = first draft)",
            f"",
        ]

        # Include the source fact for factual_accuracy checking
        if source_fact:
            parts.append("SOURCE FACT (the ONLY verified information — check the script against this):")
            parts.append(f"---")
            parts.append(f"{source_fact}")
            parts.append(f"---")
            parts.append(f"")
            parts.append(f"CRITICAL: The script must NOT invent gameplay mechanics, features, or details")
            parts.append(f"that are NOT in the SOURCE FACT above. If the script describes mechanics not")
            parts.append(f"mentioned in the source fact, flag them as factual_accuracy issues with HIGH")
            parts.append(f"severity. Commentary and opinions about the fact are OK — invented mechanics are NOT.")
            parts.append(f"")

        parts.append("SCRIPT TO EVALUATE:")
        parts.append("---")
        parts.append(script)
        parts.append("---")
        parts.append("")

        if plan.narrative_beats:
            parts.append("INTENDED NARRATIVE BEATS:")
            for beat in plan.narrative_beats:
                parts.append(f"  {beat.label}: {beat.description}")
            parts.append("")

        parts.append("Evaluate this script and return the review JSON.")

        return "\n".join(parts)

    def _parse_review(self, data: dict, revision_count: int) -> ScriptReview:
        """Parse the LLM JSON response into a ScriptReview."""
        if not isinstance(data, dict):
            return ScriptReview(
                verdict=CRITIC_VERDICT_PASS,
                revision_count=revision_count,
                feedback="invalid critic response",
            )

        verdict = str(data.get("verdict", CRITIC_VERDICT_PASS)).upper()
        if verdict not in (CRITIC_VERDICT_PASS, CRITIC_VERDICT_REVISE):
            verdict = CRITIC_VERDICT_PASS

        overall = float(data.get("overall_score", 70.0))
        dim_scores = data.get("dimension_scores", {})
        if not isinstance(dim_scores, dict):
            dim_scores = {}
        # Ensure all dimensions present with valid float scores.
        # The LLM sometimes returns malformed values (e.g. dimension names
        # as values instead of scores, or nested dicts). Be defensive.
        for d in CRITIC_DIMENSIONS:
            raw = dim_scores.get(d)
            try:
                dim_scores[d] = float(raw) if raw is not None else 70.0
            except (TypeError, ValueError):
                dim_scores[d] = 70.0
        # Also clean any extra keys that aren't valid floats
        clean_scores = {}
        for k, v in dim_scores.items():
            try:
                clean_scores[k] = float(v) if v is not None else 70.0
            except (TypeError, ValueError):
                pass  # skip malformed entries
        dim_scores = clean_scores

        issues_data = data.get("issues", [])
        issues = []
        if isinstance(issues_data, list):
            for i in issues_data:
                if isinstance(i, dict):
                    issues.append(CriticIssue(
                        dimension=str(i.get("dimension", "")),
                        severity=str(i.get("severity", "low")),
                        description=str(i.get("description", "")),
                        location=str(i.get("location", "")),
                        suggestion=str(i.get("suggestion", "")),
                    ))

        feedback = str(data.get("feedback", ""))

        return ScriptReview(
            verdict=verdict,
            overall_score=overall,
            dimension_scores=dim_scores,
            issues=issues,
            feedback=feedback,
            revision_count=revision_count,
        )
