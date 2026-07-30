"""Domain dataclasses for the editorial creative plan.

The VideoCreativePlan is the output of the EditorialPlanner stage.
It encodes editorial decisions about:
  - Video type (GAME_RELATED vs GENERAL_TOPIC)
  - Central idea / thesis
  - Narrative arc (beats)
  - Tone weights (not a persona, but editorial parameters)
  - Humor plan (enabled, intensity, styles, frequency)
  - Gameplay strategy (how to use the gameplay index)
  - Model recommendation (gemma3 vs qwen3) with reasoning

The ScriptCritic produces a ScriptReview with verdict (PASS/REVISE),
dimensional scores, and specific issues.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ── Video types ──────────────────────────────────────────────────────────────

VIDEO_TYPE_GAME_RELATED = "GAME_RELATED"
VIDEO_TYPE_GENERAL_TOPIC = "GENERAL_TOPIC"


# ── Humor ────────────────────────────────────────────────────────────────────

HUMOR_INTENSITY_NONE = "none"
HUMOR_INTENSITY_LOW = "low"
HUMOR_INTENSITY_MEDIUM_LOW = "medium-low"
HUMOR_INTENSITY_MEDIUM = "medium"
HUMOR_INTENSITY_HIGH = "high"

HUMOR_STYLES = {
    "observation", "sarcasm", "wording", "ironic_comparison",
    "subversion", "understatement", "dry_commentary", "contextual",
}

HUMOR_FREQUENCY_SPARSE = "sparse"
HUMOR_FREQUENCY_OCCASIONAL = "occasional"
HUMOR_FREQUENCY_FREQUENT = "frequent"


@dataclass
class HumorPlan:
    """Editorial humor strategy. The core principle:

    If there is no genuinely funny observation or a naturally more amusing
    way to say something, just say it normally. Silence > bad joke.

    Qwen3 with humor.intensity=low means: use the model's creative capacity
    for natural observations, but do NOT try to turn the video into comedy.
    """
    enabled: bool = False
    intensity: str = HUMOR_INTENSITY_NONE  # none, low, medium-low, medium, high
    styles: list[str] = field(default_factory=list)  # subset of HUMOR_STYLES
    frequency: str = HUMOR_FREQUENCY_SPARSE  # sparse, occasional, frequent

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "intensity": self.intensity,
            "styles": self.styles,
            "frequency": self.frequency,
        }

    @classmethod
    def from_dict(cls, d: dict) -> HumorPlan:
        return cls(
            enabled=d.get("enabled", False),
            intensity=d.get("intensity", HUMOR_INTENSITY_NONE),
            styles=d.get("styles", []),
            frequency=d.get("frequency", HUMOR_FREQUENCY_SPARSE),
        )

    @classmethod
    def none(cls) -> HumorPlan:
        """No humor at all — for serious/documental content."""
        return cls(enabled=False, intensity=HUMOR_INTENSITY_NONE, styles=[], frequency=HUMOR_FREQUENCY_SPARSE)

    @classmethod
    def low(cls, styles: Optional[list[str]] = None) -> HumorPlan:
        """Low humor — occasional natural observations, no forced jokes."""
        return cls(
            enabled=True,
            intensity=HUMOR_INTENSITY_LOW,
            styles=styles or ["observation", "sarcasm", "wording"],
            frequency=HUMOR_FREQUENCY_SPARSE,
        )


# ── Tone ─────────────────────────────────────────────────────────────────────

@dataclass
class ToneWeights:
    """Editorial personality parameters (NOT a persona caricature).

    Floats 0-1 representing the weight of each tonal dimension.
    These are subtle — the narrator can have personality without making jokes.
    """
    informative: float = 0.8
    casual: float = 0.5
    sarcastic: float = 0.1
    comedic: float = 0.05
    dramatic: float = 0.1
    nostalgic: float = 0.0
    mysterious: float = 0.0
    energetic: float = 0.3

    def to_dict(self) -> dict:
        return {
            "informative": self.informative,
            "casual": self.casual,
            "sarcastic": self.sarcastic,
            "comedic": self.comedic,
            "dramatic": self.dramatic,
            "nostalgic": self.nostalgic,
            "mysterious": self.mysterious,
            "energetic": self.energetic,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ToneWeights:
        return cls(
            informative=d.get("informative", 0.8),
            casual=d.get("casual", 0.5),
            sarcastic=d.get("sarcastic", 0.1),
            comedic=d.get("comedic", 0.05),
            dramatic=d.get("dramatic", 0.1),
            nostalgic=d.get("nostalgic", 0.0),
            mysterious=d.get("mysterious", 0.0),
            energetic=d.get("energetic", 0.3),
        )


# ── Narrative ────────────────────────────────────────────────────────────────

@dataclass
class NarrativeBeat:
    """A single beat in the narrative arc."""
    label: str  # hook, context, development, escalation, payoff, conclusion
    description: str  # what happens in this beat
    content_type: str = "fact"  # fact, observation, commentary, transition, humor, conclusion

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "description": self.description,
            "content_type": self.content_type,
        }


# ── Model recommendation ────────────────────────────────────────────────────

@dataclass
class ModelRecommendation:
    """Which LLM to use for script generation, and why."""
    model: str = "gemma3:12b"  # gemma3:12b or qwen3:14b
    reason: str = ""

    def to_dict(self) -> dict:
        return {"model": self.model, "reason": self.reason}


# ── Video Creative Plan ──────────────────────────────────────────────────────

@dataclass
class VideoCreativePlan:
    """The full editorial plan produced by the EditorialPlanner.

    This is the central artifact that drives script generation, creative
    engine, model selection, and gameplay retrieval.
    """
    video_type: str = VIDEO_TYPE_GAME_RELATED  # GAME_RELATED or GENERAL_TOPIC
    central_idea: str = ""  # the thesis / core idea of the video
    narrative_beats: list[NarrativeBeat] = field(default_factory=list)
    tone: ToneWeights = field(default_factory=ToneWeights)
    humor: HumorPlan = field(default_factory=HumorPlan)
    gameplay_strategy: str = "background_filler"  # related, background_filler, thematic_match
    visual_dependency: str = "low"  # high, medium, low
    gameplay_query: str = ""  # semantic query for gameplay retrieval
    model: ModelRecommendation = field(default_factory=ModelRecommendation)
    gameplay_compatibility: dict = field(default_factory=dict)  # what was checked
    latency_ms: int = 0
    success: bool = True
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "video_type": self.video_type,
            "central_idea": self.central_idea,
            "narrative_beats": [b.to_dict() for b in self.narrative_beats],
            "tone": self.tone.to_dict(),
            "humor": self.humor.to_dict(),
            "gameplay_strategy": self.gameplay_strategy,
            "visual_dependency": self.visual_dependency,
            "gameplay_query": self.gameplay_query,
            "model": self.model.to_dict(),
            "gameplay_compatibility": self.gameplay_compatibility,
            "latency_ms": self.latency_ms,
            "success": self.success,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> VideoCreativePlan:
        return cls(
            video_type=d.get("video_type", VIDEO_TYPE_GAME_RELATED),
            central_idea=d.get("central_idea", ""),
            narrative_beats=[NarrativeBeat(**b) for b in d.get("narrative_beats", [])],
            tone=ToneWeights.from_dict(d.get("tone", {})),
            humor=HumorPlan.from_dict(d.get("humor", {})),
            gameplay_strategy=d.get("gameplay_strategy", "background_filler"),
            visual_dependency=d.get("visual_dependency", "low"),
            gameplay_query=d.get("gameplay_query", ""),
            model=ModelRecommendation(**d.get("model", {})),
            gameplay_compatibility=d.get("gameplay_compatibility", {}),
            latency_ms=d.get("latency_ms", 0),
            success=d.get("success", True),
            error=d.get("error", ""),
        )

    @classmethod
    def empty(cls, error: str = "") -> VideoCreativePlan:
        return cls(success=False, error=error)


# ── Script Critic Review ─────────────────────────────────────────────────────

CRITIC_VERDICT_PASS = "PASS"
CRITIC_VERDICT_REVISE = "REVISE"

CRITIC_DIMENSIONS = ["structure", "naturalness", "humor", "coherence", "gameplay", "factual_accuracy"]


@dataclass
class CriticIssue:
    """A specific issue found by the script critic."""
    dimension: str  # structure, naturalness, humor, coherence, gameplay
    severity: str  # low, medium, high
    description: str
    location: str = ""  # approximate location in the script (optional)
    suggestion: str = ""  # how to fix it

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension,
            "severity": self.severity,
            "description": self.description,
            "location": self.location,
            "suggestion": self.suggestion,
        }


@dataclass
class ScriptReview:
    """The result of the ScriptCritic evaluation."""
    verdict: str = CRITIC_VERDICT_PASS  # PASS or REVISE
    overall_score: float = 0.0  # 0-100
    dimension_scores: dict[str, float] = field(default_factory=dict)  # {structure: 85, naturalness: 90, ...}
    issues: list[CriticIssue] = field(default_factory=list)
    feedback: str = ""  # feedback for revision (if REVISE)
    revision_count: int = 0
    latency_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "overall_score": self.overall_score,
            "dimension_scores": self.dimension_scores,
            "issues": [i.to_dict() for i in self.issues],
            "feedback": self.feedback,
            "revision_count": self.revision_count,
            "latency_ms": self.latency_ms,
        }

    @property
    def passed(self) -> bool:
        return self.verdict == CRITIC_VERDICT_PASS

    @property
    def has_high_issues(self) -> bool:
        return any(i.severity == "high" for i in self.issues)

    @classmethod
    def from_dict(cls, d: dict) -> ScriptReview:
        return cls(
            verdict=d.get("verdict", CRITIC_VERDICT_PASS),
            overall_score=d.get("overall_score", 0.0),
            dimension_scores=d.get("dimension_scores", {}),
            issues=[CriticIssue(**i) for i in d.get("issues", [])],
            feedback=d.get("feedback", ""),
            revision_count=d.get("revision_count", 0),
            latency_ms=d.get("latency_ms", 0),
        )
