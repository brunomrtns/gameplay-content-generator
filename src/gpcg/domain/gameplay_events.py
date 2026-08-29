"""Domain dataclasses for gameplay event analysis.

These are pure data structures used by the GameplayAnalyzer pipeline:
  - RawFrameObservation: VLM output for a single frame/batch
  - CoarseSegment: result of the first (low-res) pass
  - RefinedEvent: result of adaptive refinement
  - AudioSegment: ASR transcript segment with timestamps
  - GameplayEventRecord: final merged event (maps to GameplayEvent ORM)
  - EventTimeline: ordered collection of events for a source
  - AnalysisConfig: parameters controlling the analysis

The ORM model GameplayEvent (in models.py) is the persisted form.
These dataclasses are the in-flight representation during analysis.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional


# ── Event type taxonomy ──────────────────────────────────────────────────────

# Canonical event types (uppercase). POSSIBLE_ prefix indicates ambiguity.
EVENT_TYPES = {
    "COMBAT", "CHASE", "DIALOGUE", "CUTSCENE", "EXPLORATION",
    "VEHICLE", "MENU", "LOADING", "PUZZLE", "STEALTH",
    "INTERACTION", "MINIGAME", "TRAVEL", "IDLE", "UNKNOWN",
}

# All valid types include the POSSIBLE_ variants
ALL_VALID_TYPES = EVENT_TYPES | {f"POSSIBLE_{t}" for t in EVENT_TYPES}


@dataclass
class RawFrameObservation:
    """VLM output for a single frame or small frame batch.

    The VLM is instructed to be honest about confidence — if it cannot
    tell what's happening, it returns event_type=UNKNOWN with low confidence.
    """
    timestamp: float
    event_type: str = "UNKNOWN"
    description: str = ""
    characters: list[str] = field(default_factory=list)
    location: str = ""
    actions: list[str] = field(default_factory=list)
    activity_level: float = 0.0  # 0-1, how much is happening
    visual_confidence: float = 0.0  # 0-1, how sure the VLM is
    tags: list[str] = field(default_factory=list)

    def normalize_type(self) -> str:
        """Ensure event_type is uppercase and valid. Unknown → UNKNOWN."""
        t = self.event_type.upper().strip()
        if t in ALL_VALID_TYPES:
            return t
        # Try to map common variations
        mapping = {
            "FIGHT": "COMBAT", "FIGHTING": "COMBAT", "BATTLE": "COMBAT",
            "RUNNING": "CHASE", "PURSUIT": "CHASE",
            "TALKING": "DIALOGUE", "SPEECH": "DIALOGUE",
            "WALKING": "TRAVEL", "DRIVING": "VEHICLE", "RIDING": "VEHICLE",
            "PAUSE": "MENU", "UI": "MENU", "HUD": "MENU",
        }
        return mapping.get(t, "UNKNOWN")


@dataclass
class CoarseSegment:
    """Result of the coarse (low-res) first pass over a segment."""
    start: float
    end: float
    observation: RawFrameObservation
    is_boundary: bool = False  # True if significant change from previous
    needs_refinement: bool = False  # True if activity/change warrants refine


@dataclass
class RefinedEvent:
    """A granular event produced by adaptive refinement."""
    start: float
    end: float
    event_type: str
    description: str
    characters: list[str] = field(default_factory=list)
    location: str = ""
    actions: list[str] = field(default_factory=list)
    visual_confidence: float = 0.0
    activity_level: float = 0.0
    tags: list[str] = field(default_factory=list)


@dataclass
class AudioSegment:
    """ASR transcript segment with timestamps."""
    start: float
    end: float
    text: str
    language: str = ""
    confidence: float = 0.0
    speaker: str = ""


@dataclass
class GameplayEventRecord:
    """Final merged event — the in-flight representation that maps to
    GameplayEvent ORM. Combines visual (RefinedEvent) + audio (AudioSegment).
    """
    start_time: float
    end_time: float
    event_type: str
    description: str
    characters: list[str] = field(default_factory=list)
    location: str = ""
    actions: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    transcript: str = ""
    visual_confidence: float = 0.0
    interesting_score: float = 0.0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "start_time": self.start_time,
            "end_time": self.end_time,
            "event_type": self.event_type,
            "description": self.description,
            "characters": self.characters,
            "location": self.location,
            "actions": self.actions,
            "tags": self.tags,
            "transcript": self.transcript,
            "visual_confidence": self.visual_confidence,
            "interesting_score": self.interesting_score,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> GameplayEventRecord:
        return cls(
            start_time=d.get("start_time", 0.0),
            end_time=d.get("end_time", 0.0),
            event_type=d.get("event_type", "UNKNOWN"),
            description=d.get("description", ""),
            characters=d.get("characters", []),
            location=d.get("location", ""),
            actions=d.get("actions", []),
            tags=d.get("tags", []),
            transcript=d.get("transcript", ""),
            visual_confidence=d.get("visual_confidence", 0.0),
            interesting_score=d.get("interesting_score", 0.0),
            metadata=d.get("metadata", {}),
        )


@dataclass
class EventTimeline:
    """Ordered collection of events for a single gameplay source."""
    source_id: int
    source_path: str
    duration: float
    events: list[GameplayEventRecord] = field(default_factory=list)
    analysis_version: str = "v1"
    vision_model: str = ""
    asr_model: str = ""
    config_hash: str = ""
    has_audio: bool = False
    has_transcript: bool = False

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def confident_events(self) -> list[GameplayEventRecord]:
        """Events with visual_confidence >= 0.7."""
        return [e for e in self.events if e.visual_confidence >= 0.7]

    @property
    def interesting_events(self) -> list[GameplayEventRecord]:
        """Events with interesting_score >= 0.4 (configurable threshold)."""
        return [e for e in self.events if e.interesting_score >= 0.4]

    def by_type(self, event_type: str) -> list[GameplayEventRecord]:
        """Filter events by type (case-insensitive, matches POSSIBLE_ prefix too)."""
        et = event_type.upper()
        return [e for e in self.events if e.event_type == et or e.event_type == f"POSSIBLE_{et}"]

    def in_range(self, start: float, end: float) -> list[GameplayEventRecord]:
        """Events overlapping [start, end] time range."""
        return [e for e in self.events if e.start_time < end and e.end_time > start]

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "source_path": self.source_path,
            "duration": self.duration,
            "analysis_version": self.analysis_version,
            "vision_model": self.vision_model,
            "asr_model": self.asr_model,
            "config_hash": self.config_hash,
            "has_audio": self.has_audio,
            "has_transcript": self.has_transcript,
            "event_count": self.event_count,
            "events": [e.to_dict() for e in self.events],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> EventTimeline:
        return cls(
            source_id=d.get("source_id", 0),
            source_path=d.get("source_path", ""),
            duration=d.get("duration", 0.0),
            events=[GameplayEventRecord.from_dict(e) for e in d.get("events", [])],
            analysis_version=d.get("analysis_version", "v1"),
            vision_model=d.get("vision_model", ""),
            asr_model=d.get("asr_model", ""),
            config_hash=d.get("config_hash", ""),
            has_audio=d.get("has_audio", False),
            has_transcript=d.get("has_transcript", False),
        )

    @classmethod
    def from_json(cls, json_str: str) -> EventTimeline:
        return cls.from_dict(json.loads(json_str))


@dataclass
class AnalysisConfig:
    """Parameters controlling gameplay analysis. Built from Settings."""
    coarse_segment_sec: float = 30.0
    refine_interval_sec: float = 3.0
    activity_threshold: float = 0.5
    high_activity_threshold: float = 0.75
    ultra_refine_interval_sec: float = 1.5
    interesting_threshold: float = 0.4
    vlm_batch_size: int = 4
    analysis_version: str = "v1"
    vision_model: str = "gemma3:12b"
    asr_model: str = "large-v3"
    asr_device: str = "cuda"
    asr_compute_type: str = "float16"
    enable_asr: bool = True
    enable_interesting_score: bool = True

    def to_hash(self) -> str:
        """Stable hash of config for versioning/reprocessing decisions."""
        payload = json.dumps({
            "coarse_segment_sec": self.coarse_segment_sec,
            "refine_interval_sec": self.refine_interval_sec,
            "activity_threshold": self.activity_threshold,
            "high_activity_threshold": self.high_activity_threshold,
            "ultra_refine_interval_sec": self.ultra_refine_interval_sec,
            "vlm_batch_size": self.vlm_batch_size,
            "analysis_version": self.analysis_version,
            "vision_model": self.vision_model,
            "asr_model": self.asr_model,
            "enable_asr": self.enable_asr,
            "enable_interesting_score": self.enable_interesting_score,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
