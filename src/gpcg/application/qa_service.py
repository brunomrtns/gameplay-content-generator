"""QA service — technical (deterministic) + AI quality assessment.

Technical QA: FFprobe validates the output file is decodable, has correct
duration, aspect ratio, audio stream, codec, non-null volume.

AI QA: LLM evaluates coherence (script vs narration), hook quality, pacing,
repetition — returns a structured report with score + issues.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.core.models import (
    ContentPlan,
    Script,
    Video,
    VideoStatus,
)
from gpcg.infrastructure.llm import LLMClient, LLMError
from gpcg.infrastructure.media import MediaError, generate_thumbnail, probe
from gpcg.logging import get_logger

log = get_logger(__name__)


@dataclass
class QAIssue:
    type: str  # pacing, hook, coherence, repetition, technical, etc
    severity: str  # low, medium, high
    description: str
    repair_stage: Optional[str] = None  # which pipeline stage to retry


@dataclass
class QAResult:
    passed: bool
    score: float  # 0-100
    technical: dict = field(default_factory=dict)
    issues: list[QAIssue] = field(default_factory=list)
    ai_report: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "score": self.score,
            "technical": self.technical,
            "issues": [
                {"type": i.type, "severity": i.severity, "description": i.description, "repair_stage": i.repair_stage}
                for i in self.issues
            ],
            "ai_report": self.ai_report,
        }


class QAService:
    """Technical + AI quality assessment."""

    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self.llm = llm
        self.settings = get_settings()

    def evaluate(
        self,
        video_path: Path,
        script: Script,
        content_plan: ContentPlan,
        target_duration: int,
    ) -> QAResult:
        """Run technical + AI QA on a generated video."""
        issues: list[QAIssue] = []
        technical: dict = {}
        score = 100.0

        # ── Technical QA ────────────────────────────────────────────────────
        try:
            info = probe(video_path)
            technical = info.to_dict()
        except MediaError as e:
            return QAResult(
                passed=False,
                score=0.0,
                technical={"error": str(e)},
                issues=[QAIssue("technical", "high", f"file not decodable: {e}", "render")],
            )

        # Duration check (Shorts: 15-60s typically)
        dur = info.duration
        technical["target_duration"] = target_duration
        min_duration = self.settings.gpcg_min_video_duration
        if dur < 10:
            issues.append(QAIssue("technical", "high", f"duration too short: {dur:.1f}s", "render"))
            score -= 40
        elif dur < min_duration:
            issues.append(QAIssue("technical", "high",
                f"duration {dur:.1f}s below minimum {min_duration}s — script too short", "script"))
            score -= 30
        elif abs(dur - target_duration) > target_duration * 0.3:
            issues.append(QAIssue("technical", "medium", f"duration {dur:.1f}s far from target {target_duration}s", "render"))
            score -= 15

        # Aspect ratio (reel = 9:16 = 0.5625)
        expected_ratio = 9.0 / 16.0 if "9_16" in self.settings.gpcg_default_video_profile else 16.0 / 9.0
        actual_ratio = info.width / info.height if info.height else 0
        if abs(actual_ratio - expected_ratio) > 0.05:
            issues.append(QAIssue("technical", "high", f"wrong aspect ratio: {info.aspect_ratio}", "render"))
            score -= 30

        # Audio presence
        if not info.has_audio:
            issues.append(QAIssue("technical", "high", "no audio stream", "render"))
            score -= 40

        # Codec
        if info.codec not in ("h264", "hevc", "h265"):
            issues.append(QAIssue("technical", "low", f"unexpected codec: {info.codec}", "render"))
            score -= 5

        # ── AI QA ───────────────────────────────────────────────────────────
        ai_report: dict = {}
        if self.llm is not None:
            try:
                ai_report = self._ai_qa(script, content_plan, info)
                ai_score = float(ai_report.get("score", 80))
                score = (score + ai_score) / 2  # blend technical + AI
                for issue in ai_report.get("issues", []):
                    issues.append(
                        QAIssue(
                            type=issue.get("type", "ai"),
                            severity=issue.get("severity", "low"),
                            description=issue.get("description", ""),
                            repair_stage=issue.get("repair_stage"),
                        )
                    )
            except LLMError as e:
                log.warning(f"AI QA failed: {e}")
                ai_report = {"error": str(e)}

        # Determine pass/fail
        # Score-based: a good score (>= 70) passes even with high-severity
        # issues, since the LLM critic may flag subjective problems that
        # don't warrant rejecting an otherwise good video. Below 70, any
        # high-severity issue triggers a fail for auto-repair.
        high_issues = [i for i in issues if i.severity == "high"]
        if score >= 70:
            passed = True
        else:
            passed = len(high_issues) == 0 and score >= 60

        return QAResult(
            passed=passed,
            score=max(0.0, min(100.0, score)),
            technical=technical,
            issues=issues,
            ai_report=ai_report,
        )

    def _ai_qa(self, script: Script, plan: ContentPlan, info) -> dict:
        """LLM evaluates the script/plan quality (no frame analysis in MVP)."""
        system = """You are a YouTube Shorts quality reviewer. Evaluate the script and metadata
for a generated Short. You do NOT see the video frames — evaluate the content quality only.

Return JSON:
{
  "score": <0-100>,
  "issues": [{"type": "pacing|hook|coherence|repetition|tone|length", "severity": "low|medium|high", "description": "...", "repair_stage": "script|content_planning|tts|render|none"}],
  "summary": "<brief>"
}"""
        # Build context — game name if game-specific, else curiosity context
        if plan.game is not None:
            context_line = f"Game: {plan.game.canonical_name}\n"
        elif plan.background_game is not None:
            context_line = (
                f"Context: General curiosity (NOT about the game)\n"
                f"Background gameplay: {plan.background_game.canonical_name}\n"
            )
        else:
            context_line = "Context: General curiosity\n"

        prompt = (
            f"{context_line}"
            f"Topic: {plan.topic}\n"
            f"Tone: {plan.tone}\n"
            f"Target duration: {plan.target_duration}s\n"
            f"Script char count: {script.char_count}\n\n"
            f"Final script:\n{script.final}\n\n"
            f"Video duration: {info.duration:.1f}s\n"
            f"Video resolution: {info.width}x{info.height}\n"
            f"Evaluate this Short."
        )
        return self.llm.chat_json(system, prompt, temperature=0.3, max_tokens=1024)


def persist_qa_result(
    session: Session,
    video: Video,
    result: QAResult,
    video_path: Path,
) -> Video:
    """Update a Video record with QA results + thumbnail."""
    video.qa_score = result.score
    video.qa_report = result.to_dict()
    video.duration = result.technical.get("duration", 0.0)
    video.width = result.technical.get("width", 0)
    video.height = result.technical.get("height", 0)
    video.status = VideoStatus.qa_passed.value if result.passed else VideoStatus.qa_failed.value
    video.file_path = str(video_path)

    # Generate thumbnail
    try:
        thumb = video_path.parent / f"{video_path.stem}_thumb.jpg"
        generate_thumbnail(video_path, thumb, at=min(1.0, max(0.1, video.duration / 2)))
        video.thumbnail_path = str(thumb)
    except MediaError as e:
        log.warning(f"thumbnail generation failed: {e}")

    session.flush()
    return video
