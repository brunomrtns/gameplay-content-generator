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
from gpcg.infrastructure.llm import LLMClient, LLMError
from gpcg.logging import get_logger

log = get_logger(__name__)


# ── Prompts ──────────────────────────────────────────────────────────────────

CRITIC_SYSTEM = """You are a SCRIPT CRITIC for a Brazilian gaming YouTube Shorts channel.
You evaluate narration scripts and decide if they PASS or need REVISE.

## Evaluation Dimensions

1. STRUCTURE (0-100):
   - Is there a clear beginning?
   - Is there development?
   - Is there a conclusion?
   - Is there a CENTRAL IDEA (thesis)?
   - Does the script actually GO somewhere?
   - Or is it just a collection of facts thrown together?

2. NATURALNESS (0-100):
   - Does it sound like someone SPEAKING, not writing?
   - Are there AI-isms? (detect the PATTERN, not just specific words):
     * Generic introductions ("Neste vídeo iremos explorar...")
     * Artificial exaggeration
     * Consecutive rhetorical questions
     * Over-explanation
     * Predictable transitions
     * Generic conclusions
     * "Você não vai acreditar..."
     * "O que torna isso ainda mais interessante"
     * "E é aí que as coisas ficam interessantes"
     * "Prepare-se..."
     * "Já imaginou..."
     * Repetitive syntactic structures
     * Unnecessary metaphors
     * Excess adjectives
     * Generic YouTube presenter tone
   - Is the narrator explaining too much?
   - Does it feel like AI-generated text?

3. HUMOR (0-100):
   - Do the jokes actually WORK?
   - Is the humor FORCED?
   - Are there TOO MANY jokes?
   - Could a joke be REMOVED without losing anything?
   - Did the humor arise naturally from context?
   - Is any phrase DESPERATELY trying to be funny?
   - CRITICAL: Bad humor should be REMOVED, not replaced with another joke.

4. COHERENCE (0-100):
   - Does the text maintain the same tone?
   - Is there a passage that seems to belong to another video?
   - Does the script abandon the central idea?
   - Is there information that doesn't contribute to the narrative?

5. GAMEPLAY (0-100, when applicable):
   - Does the narration match what's on screen?
   - Does the selected clip reinforce the narrative?
   - Is the image just filler?
   - Is there a better synchronization opportunity?

6. FACTUAL_ACCURACY (0-100 — CRITICAL):
   - Does the script INVENT gameplay mechanics, features, or details that are
     NOT in the source fact?
   - Compare each claim in the script against the SOURCE FACT provided.
   - If the script says "you can use X to do Y" and the source fact doesn't
     mention Y, that's a HALLUCINATION. Score LOW (0-30).
   - If the script adds commentary or opinions about the fact, that's OK
     (commentary is not a factual claim).
   - If the script describes mechanics, abilities, or features not in the
     source fact, that's a CRITICAL issue with HIGH severity.
   - Adding plausible-sounding but invented gameplay details is the WORST
     error a script can make — it misleads viewers.
   - Score 100 only if every factual claim in the script is supported by
     the source fact.
   - Score 0 if the script invents multiple gameplay mechanics not in the fact.

## Verdict Rules

PASS when:
- Overall score >= 70
- No high-severity issues
- Structure has a clear arc
- Naturalness is high (sounds like speech)
- Factual accuracy is high (no invented mechanics)

REVISE when:
- Overall score < 70, OR
- Any high-severity issue, OR
- Structure lacks central idea or arc, OR
- Naturalness has clear AI-isms, OR
- Factual accuracy < 70 (invented content detected)

## Feedback for Revision

If REVISE, provide SPECIFIC feedback:
- What exactly is wrong (quote the problematic phrase)
- What to do instead (but for humor: "REMOVE this", not "replace with a joke")
- For factual accuracy: "REMOVE this invented mechanic — it's not in the source fact"
- Be concrete, not generic

## Output

Return ONLY valid JSON:
{
  "verdict": "PASS|REVISE",
  "overall_score": 75,
  "dimension_scores": {
    "structure": 80,
    "naturalness": 75,
    "humor": 70,
    "coherence": 85,
    "gameplay": 60,
    "factual_accuracy": 90
  },
  "issues": [
    {
      "dimension": "naturalness",
      "severity": "medium",
      "description": "The phrase 'prepare-se para uma jornada' is a generic AI introduction",
      "location": "first sentence",
      "suggestion": "Start with a direct observation instead of a generic hook"
    }
  ],
  "feedback": "Specific revision instructions for the scriptwriter..."
}"""


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
