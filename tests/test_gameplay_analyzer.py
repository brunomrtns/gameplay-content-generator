"""Tests for the GameplayAnalyzer — automatic semantic gameplay understanding.

Tests A-F from the spec, using mock VisionAnalyzer/ASRTranscriber/FrameSampler
so they don't require Ollama or real video files (except the fixture-based ones).

Cases:
  A — gameplay with rapid events (walk → dialogue → combat → chase → flee)
      Expected: refinement separates the happenings
  B — gameplay with long stable periods
      Expected: no hundreds of redundant events
  C — gameplay with abrupt change
      Expected: boundary detected, resolution increased at that point
  D — gameplay with lots of dialogue
      Expected: vision + transcript combined
  E — ambiguous gameplay
      Expected: low confidence / UNKNOWN, no invented events
  F — long gameplay
      Expected: processed in batches without holding everything in memory

Also tests:
  - GameplayIndexService persistence and queries
  - AnalysisConfig hashing for versioning
  - EventTimeline serialization
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from gpcg.domain.gameplay_events import (
    AnalysisConfig,
    AudioSegment,
    CoarseSegment,
    EventTimeline,
    GameplayEventRecord,
    RawFrameObservation,
    RefinedEvent,
)
from gpcg.infrastructure.frame_sampler import FrameSampler, SampledFrame


# ── Fakes ────────────────────────────────────────────────────────────────────


class FakeFrameSampler:
    """Fake sampler that doesn't touch FFmpeg. Returns dummy frame paths."""

    def __init__(self) -> None:
        self.coarse_calls: list[float] = []  # segment_sec values
        self.dense_calls: list[tuple[float, float, float]] = []  # (start, end, interval)
        self.audio_extracted = False

    def coarse_sample(self, source, segment_sec=30.0, output_dir=None):
        self.coarse_calls.append(segment_sec)
        # Return dummy frames at segment midpoints
        from gpcg.infrastructure.media import probe
        info = probe(source)
        timestamps = []
        t = segment_sec / 2
        while t < info.duration:
            timestamps.append(t)
            t += segment_sec
        return [SampledFrame(path=Path(f"/tmp/fake_{t}.jpg"), timestamp=t) for t in timestamps]

    def dense_sample(self, source, start, end, interval_sec=3.0, output_dir=None):
        self.dense_calls.append((start, end, interval_sec))
        timestamps = []
        t = start + interval_sec / 2
        while t < end:
            timestamps.append(t)
            t += interval_sec
        return [SampledFrame(path=Path(f"/tmp/fake_{t}.jpg"), timestamp=t) for t in timestamps]

    def extract_audio(self, source, output_path=None):
        self.audio_extracted = True
        if output_path is None:
            output_path = Path("/tmp/fake_audio.wav")
        # Create a dummy file so the ASR fake can "read" it
        Path(output_path).touch()
        return Path(output_path)

    def cleanup_dir(self, dir_path):
        pass  # no-op for fake


class FakeVisionAnalyzer:
    """Fake VLM that returns scripted observations based on timestamps."""

    def __init__(self, frame_observations: Optional[dict] = None, batch_observations: Optional[dict] = None, interesting_scores: Optional[dict] = None):
        # frame_observations: {timestamp: RawFrameObservation}
        self.frame_observations = frame_observations or {}
        # batch_observations: {(start, end): RawFrameObservation}
        self.batch_observations = batch_observations or {}
        # interesting_scores: {event_type: score}
        self.interesting_scores = interesting_scores or {}
        self.single_calls = 0
        self.batch_calls = 0
        self.score_calls = 0

    def analyze_single_frame(self, frame_path):
        self.single_calls += 1
        # Extract timestamp from fake filename /tmp/fake_15.0.jpg
        name = Path(frame_path).stem
        try:
            ts = float(name.replace("fake_", ""))
        except ValueError:
            ts = 0.0
        return self.frame_observations.get(ts, RawFrameObservation(timestamp=ts, event_type="UNKNOWN"))

    def analyze_frame_batch(self, frames, *, start_time=0.0, interval_sec=3.0):
        self.batch_calls += 1
        # Find the matching batch observation by time range
        if frames:
            end_time = start_time + len(frames) * interval_sec
            for (bstart, bend), obs in self.batch_observations.items():
                if abs(bstart - start_time) < 1.0:
                    obs.timestamp = start_time
                    return obs
        return RawFrameObservation(timestamp=start_time, event_type="UNKNOWN")

    def score_interesting(self, description, event_type):
        self.score_calls += 1
        return self.interesting_scores.get(event_type, 0.3)


class FakeASRTranscriber:
    """Fake ASR that returns scripted segments."""

    def __init__(self, segments: Optional[list[AudioSegment]] = None, available: bool = True):
        self.segments = segments or []
        self._available = available
        self.transcribe_calls = 0

    def is_available(self):
        return self._available

    def transcribe(self, audio_path, language=""):
        self.transcribe_calls += 1
        return list(self.segments)

    def transcribe_with_fallback(self, audio_path, language=""):
        try:
            return self.transcribe(audio_path, language=language)
        except Exception:
            return []


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_observation(ts, etype, desc="", activity=0.3, confidence=0.8, location="", actions=None):
    return RawFrameObservation(
        timestamp=ts,
        event_type=etype,
        description=desc,
        activity_level=activity,
        visual_confidence=confidence,
        location=location,
        actions=actions or [],
    )


def _build_analyzer(vision, asr=None, sampler=None, config=None):
    from gpcg.application.gameplay_analyzer import GameplayAnalyzer
    # Default config uses small segments to work with the 3s test fixture
    if config is None:
        config = AnalysisConfig(
            coarse_segment_sec=1.0,
            refine_interval_sec=0.3,
            ultra_refine_interval_sec=0.15,
            activity_threshold=0.5,
            high_activity_threshold=0.75,
        )
    return GameplayAnalyzer(
        vision=vision,
        asr=asr or FakeASRTranscriber(available=False),
        sampler=sampler or FakeFrameSampler(),
        config=config,
    )


# ── Tests ────────────────────────────────────────────────────────────────────


class TestAnalysisConfig:
    """Config hashing and versioning."""

    def test_config_hash_is_stable(self):
        c1 = AnalysisConfig()
        c2 = AnalysisConfig()
        assert c1.to_hash() == c2.to_hash()

    def test_config_hash_changes_with_params(self):
        c1 = AnalysisConfig(coarse_segment_sec=30.0)
        c2 = AnalysisConfig(coarse_segment_sec=60.0)
        assert c1.to_hash() != c2.to_hash()

    def test_config_hash_ignores_unrelated_fields(self):
        c1 = AnalysisConfig(asr_device="cuda")
        c2 = AnalysisConfig(asr_device="cpu")
        # asr_device is NOT in the hash (it's runtime, not analysis logic)
        assert c1.to_hash() == c2.to_hash()


class TestEventTimeline:
    """Timeline data structure tests."""

    def test_empty_timeline(self):
        tl = EventTimeline(source_id=1, source_path="/test.mp4", duration=120.0)
        assert tl.event_count == 0
        assert tl.confident_events == []
        assert tl.interesting_events == []

    def test_confident_events_filter(self):
        tl = EventTimeline(source_id=1, source_path="/test.mp4", duration=120.0)
        tl.events = [
            GameplayEventRecord(0, 10, "COMBAT", "fight", visual_confidence=0.9),
            GameplayEventRecord(10, 20, "WALK", "walk", visual_confidence=0.4),
        ]
        assert len(tl.confident_events) == 1
        assert tl.confident_events[0].event_type == "COMBAT"

    def test_interesting_events_filter(self):
        tl = EventTimeline(source_id=1, source_path="/test.mp4", duration=120.0)
        tl.events = [
            GameplayEventRecord(0, 10, "COMBAT", "fight", interesting_score=0.8),
            GameplayEventRecord(10, 20, "WALK", "walk", interesting_score=0.1),
        ]
        assert len(tl.interesting_events) == 1

    def test_by_type_matches_possible_prefix(self):
        tl = EventTimeline(source_id=1, source_path="/test.mp4", duration=120.0)
        tl.events = [
            GameplayEventRecord(0, 10, "COMBAT", "fight"),
            GameplayEventRecord(10, 20, "POSSIBLE_COMBAT", "maybe fight"),
        ]
        assert len(tl.by_type("COMBAT")) == 2

    def test_in_range_overlapping(self):
        tl = EventTimeline(source_id=1, source_path="/test.mp4", duration=120.0)
        tl.events = [
            GameplayEventRecord(0, 10, "A", "a"),
            GameplayEventRecord(10, 20, "B", "b"),
            GameplayEventRecord(20, 30, "C", "c"),
        ]
        # Events overlapping [5, 15]
        result = tl.in_range(5, 15)
        assert len(result) == 2  # A (0-10) and B (10-20)

    def test_to_json_roundtrip(self):
        tl = EventTimeline(source_id=1, source_path="/test.mp4", duration=60.0)
        tl.events = [GameplayEventRecord(0, 10, "COMBAT", "fight", visual_confidence=0.9)]
        j = tl.to_json()
        d = json.loads(j)
        assert d["event_count"] == 1
        assert d["events"][0]["event_type"] == "COMBAT"


class TestRawFrameObservation:
    """Observation parsing and type normalization."""

    def test_normalize_known_type(self):
        obs = RawFrameObservation(timestamp=0, event_type="COMBAT")
        assert obs.normalize_type() == "COMBAT"

    def test_normalize_variation(self):
        obs = RawFrameObservation(timestamp=0, event_type="fighting")
        assert obs.normalize_type() == "COMBAT"

    def test_normalize_unknown(self):
        obs = RawFrameObservation(timestamp=0, event_type="xyz")
        assert obs.normalize_type() == "UNKNOWN"

    def test_normalize_possible_prefix(self):
        obs = RawFrameObservation(timestamp=0, event_type="POSSIBLE_COMBAT")
        assert obs.normalize_type() == "POSSIBLE_COMBAT"


# ── Case A: rapid events ─────────────────────────────────────────────────────


class TestCaseARapidEvents:
    """Test A — gameplay with rapid events (walk → dialogue → combat → chase → flee).

    Expected: refinement separates these happenings into distinct events.
    """

    def test_rapid_events_are_separated(self, sample_video):
        # Coarse observations: 3 segments of 1s each in a 3s video
        # Segments at t=0.5, 1.5, 2.5
        frame_obs = {
            0.5: _make_observation(0.5, "TRAVEL", "walking through school", activity=0.2, confidence=0.85),
            1.5: _make_observation(1.5, "COMBAT", "fighting students", activity=0.9, confidence=0.9),
            2.5: _make_observation(2.5, "CHASE", "running from teacher", activity=0.85, confidence=0.88),
        }
        # Batch observations for refined segments
        batch_obs = {
            (1.5, 2.5): _make_observation(1.5, "COMBAT", "fight with students", activity=0.9),
            (2.5, 3.5): _make_observation(2.5, "CHASE", "chased by teacher", activity=0.85),
        }
        vision = FakeVisionAnalyzer(
            frame_observations=frame_obs,
            batch_observations=batch_obs,
            interesting_scores={"COMBAT": 0.9, "CHASE": 0.85, "DIALOGUE": 0.5, "TRAVEL": 0.2},
        )
        analyzer = _build_analyzer(vision)

        timeline = analyzer.analyze(sample_video, source_id=1, enable_asr=False)

        # Should have multiple distinct events
        assert timeline.event_count >= 2
        types = [e.event_type for e in timeline.events]
        # Combat and chase should be present
        assert "COMBAT" in types or any("COMBAT" in t for t in types)
        assert "CHASE" in types or any("CHASE" in t for t in types)

    def test_high_activity_triggers_refinement(self, sample_video):
        frame_obs = {
            0.5: _make_observation(0.5, "TRAVEL", "walking", activity=0.2),
            1.5: _make_observation(1.5, "COMBAT", "fighting", activity=0.95),
        }
        vision = FakeVisionAnalyzer(frame_observations=frame_obs)
        sampler = FakeFrameSampler()
        analyzer = _build_analyzer(vision, sampler=sampler)
        analyzer.analyze(sample_video, source_id=1, enable_asr=False, enable_interesting_score=False)

        # The high-activity segment should trigger dense sampling
        assert len(sampler.dense_calls) > 0


# ── Case B: long stable periods ──────────────────────────────────────────────


class TestCaseBStablePeriods:
    """Test B — gameplay with long stable periods.

    Expected: no hundreds of redundant events.
    """

    def test_stable_gameplay_few_events(self, sample_video):
        # All segments show low-activity exploration
        frame_obs = {}
        for t in [0.5, 1.5, 2.5]:
            frame_obs[t] = _make_observation(t, "EXPLORATION", "walking around", activity=0.15, confidence=0.8)
        vision = FakeVisionAnalyzer(
            frame_observations=frame_obs,
            interesting_scores={"EXPLORATION": 0.2},
        )
        analyzer = _build_analyzer(vision)
        timeline = analyzer.analyze(sample_video, source_id=1, enable_asr=False)

        # Low-activity segments should NOT trigger refinement
        # Each coarse segment becomes one event (no fragmentation)
        assert timeline.event_count <= 4  # at most one per coarse segment


# ── Case C: abrupt change ────────────────────────────────────────────────────


class TestCaseCAbruptChange:
    """Test C — gameplay with abrupt change.

    Expected: boundary detected, resolution increased at that point.
    """

    def test_boundary_detected_on_type_change(self, sample_video):
        frame_obs = {
            0.5: _make_observation(0.5, "EXPLORATION", "calm exploration", activity=0.2),
            1.5: _make_observation(1.5, "COMBAT", "sudden fight", activity=0.9),
            2.5: _make_observation(2.5, "COMBAT", "still fighting", activity=0.85),
        }
        vision = FakeVisionAnalyzer(frame_observations=frame_obs)
        analyzer = _build_analyzer(vision)
        analyzer.analyze(sample_video, source_id=1, enable_asr=False, enable_interesting_score=False)

        # The boundary between segment 1 and 2 should trigger refinement
        # (type change EXPLORATION → COMBAT)
        assert vision.batch_calls > 0  # refinement happened


# ── Case D: dialogue with transcript ─────────────────────────────────────────


class TestCaseDDialogueTranscript:
    """Test D — gameplay with lots of dialogue.

    Expected: vision + transcript combined.
    """

    def test_transcript_merged_into_events(self, sample_video):
        frame_obs = {
            0.5: _make_observation(0.5, "DIALOGUE", "talking to character", activity=0.3),
        }
        audio_segs = [
            AudioSegment(start=0, end=1, text="What are you doing here?", confidence=0.9),
            AudioSegment(start=1, end=2, text="I was just leaving.", confidence=0.85),
        ]
        vision = FakeVisionAnalyzer(frame_observations=frame_obs)
        asr = FakeASRTranscriber(segments=audio_segs, available=True)
        analyzer = _build_analyzer(vision, asr=asr)
        timeline = analyzer.analyze(sample_video, source_id=1, enable_asr=True, enable_interesting_score=False)

        # At least one event should have transcript text
        has_transcript = any(e.transcript for e in timeline.events)
        assert has_transcript, "Expected at least one event with transcript text"

    def test_asr_unavailable_falls_back_gracefully(self, sample_video):
        frame_obs = {0.5: _make_observation(0.5, "DIALOGUE", "talking", activity=0.3)}
        vision = FakeVisionAnalyzer(frame_observations=frame_obs)
        asr = FakeASRTranscriber(available=False)
        analyzer = _build_analyzer(vision, asr=asr)
        timeline = analyzer.analyze(sample_video, source_id=1, enable_asr=True, enable_interesting_score=False)

        # Should still produce events, just without transcripts
        assert timeline.event_count > 0
        assert not timeline.has_transcript


# ── Case E: ambiguous gameplay ───────────────────────────────────────────────


class TestCaseEAmbiguousGameplay:
    """Test E — ambiguous gameplay.

    Expected: low confidence / UNKNOWN, no invented events.
    """

    def test_ambiguous_returns_unknown_with_low_confidence(self, sample_video):
        frame_obs = {
            0.5: _make_observation(0.5, "UNKNOWN", "unclear what's happening", activity=0.3, confidence=0.2),
            1.5: _make_observation(1.5, "UNKNOWN", "still unclear", activity=0.3, confidence=0.2),
            2.5: _make_observation(2.5, "UNKNOWN", "unclear", activity=0.3, confidence=0.2),
        }
        vision = FakeVisionAnalyzer(frame_observations=frame_obs)
        analyzer = _build_analyzer(vision)
        timeline = analyzer.analyze(sample_video, source_id=1, enable_asr=False, enable_interesting_score=False)

        # Should have events with UNKNOWN type and low confidence
        unknown_events = [e for e in timeline.events if e.event_type == "UNKNOWN"]
        assert len(unknown_events) > 0
        assert all(e.visual_confidence < 0.5 for e in unknown_events)

    def test_possible_prefix_preserved(self, sample_video):
        frame_obs = {
            0.5: _make_observation(0.5, "POSSIBLE_COMBAT", "might be a fight", activity=0.5, confidence=0.4),
            1.5: _make_observation(1.5, "POSSIBLE_COMBAT", "still unclear", activity=0.5, confidence=0.4),
            2.5: _make_observation(2.5, "POSSIBLE_COMBAT", "unclear", activity=0.5, confidence=0.4),
        }
        vision = FakeVisionAnalyzer(frame_observations=frame_obs)
        analyzer = _build_analyzer(vision)
        timeline = analyzer.analyze(sample_video, source_id=1, enable_asr=False, enable_interesting_score=False)

        possible_events = [e for e in timeline.events if e.event_type.startswith("POSSIBLE_")]
        assert len(possible_events) > 0


# ── Case F: long gameplay ────────────────────────────────────────────────────


class TestCaseFLongGameplay:
    """Test F — long gameplay.

    Expected: processed in batches without holding everything in memory.
    """

    def test_long_gameplay_processes_all_segments(self, sample_video):
        # Even a short fixture video tests the batching logic
        # The key assertion is that the analyzer completes without error
        # and processes all coarse segments
        frame_obs = {}
        # The fixture is 3s, so with segment_sec=30 we get 1 segment at t=15
        # But t=15 > 3s duration, so we get 0 segments. Use smaller segment.
        config = AnalysisConfig(coarse_segment_sec=1.0, refine_interval_sec=0.3)
        frame_obs = {
            0.5: _make_observation(0.5, "EXPLORATION", "start", activity=0.2),
            1.5: _make_observation(1.5, "COMBAT", "fight", activity=0.8),
            2.5: _make_observation(2.5, "CHASE", "chase", activity=0.7),
        }
        vision = FakeVisionAnalyzer(
            frame_observations=frame_obs,
            interesting_scores={"COMBAT": 0.9, "CHASE": 0.8, "EXPLORATION": 0.2},
        )
        analyzer = _build_analyzer(vision, config=config)
        timeline = analyzer.analyze(sample_video, source_id=1, enable_asr=False)

        # Should complete and produce events
        assert timeline.event_count > 0
        assert timeline.duration > 0


# ── GameplayIndexService tests ───────────────────────────────────────────────


class TestGameplayIndexService:
    """Persistence and query tests for the semantic index."""

    @pytest.fixture
    def fresh_db(self, tmp_path, monkeypatch):
        """Create a fresh SQLite DB for each test."""
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("GPCG_DB_PATH", str(db_path))
        # Clear cached settings and engine so they pick up the new DB path
        from gpcg.config import get_settings
        get_settings.cache_clear()
        import gpcg.infrastructure.database as db_module
        db_module._engine = None
        db_module._SessionLocal = None
        from gpcg.infrastructure.database import init_db
        init_db()
        yield db_path
        # Cleanup after test
        db_module._engine = None
        db_module._SessionLocal = None
        get_settings.cache_clear()

    @pytest.fixture
    def source_id(self, fresh_db):
        """Create a test GameplaySource."""
        from gpcg.domains.games.models import GameplaySource, Game
        from gpcg.infrastructure.database import session_scope
        with session_scope() as session:
            game = Game(canonical_name="TestGame")
            session.add(game)
            session.flush()
            src = GameplaySource(
                game_id=game.id,
                file_path="/tmp/test.mp4",
                filename="test.mp4",
                file_hash="abc123",
                duration=120.0,
                width=1920,
                height=1080,
                has_audio=True,
                ingestion_status="ready",
            )
            session.add(src)
            session.flush()
            return src.id

    def test_set_and_get_analysis_status(self, fresh_db, source_id):
        from gpcg.application.gameplay_index_service import GameplayIndexService
        from gpcg.domains.games.models import AnalysisStatus
        from gpcg.infrastructure.database import session_scope

        svc = GameplayIndexService()
        with session_scope() as session:
            svc.set_analysis_status(session, source_id, AnalysisStatus.analyzing.value)
            assert svc.get_analysis_status(session, source_id) == "analyzing"

            svc.set_analysis_status(
                session, source_id, AnalysisStatus.ready.value,
                version="v1", vision_model="gemma3:12b",
                config_hash="abc123", event_count=42,
            )
            assert svc.is_ready(session, source_id)

    def test_set_and_get_compatibility(self, fresh_db, source_id):
        from gpcg.application.gameplay_index_service import GameplayIndexService
        from gpcg.infrastructure.database import session_scope

        svc = GameplayIndexService()
        with session_scope() as session:
            svc.set_compatibility(session, source_id, game_related=True, general_topic=False)
            compat = svc.get_compatibility(session, source_id)
            assert compat["game_related"] is True
            assert compat["general_topic"] is False

    def test_persist_timeline(self, fresh_db, source_id):
        from gpcg.application.gameplay_index_service import GameplayIndexService
        from gpcg.infrastructure.database import session_scope

        timeline = EventTimeline(
            source_id=source_id,
            source_path="/tmp/test.mp4",
            duration=120.0,
            analysis_version="v1",
            vision_model="gemma3:12b",
            config_hash="abc123",
            has_audio=True,
        )
        timeline.events = [
            GameplayEventRecord(0, 10, "COMBAT", "fight", visual_confidence=0.9, interesting_score=0.85),
            GameplayEventRecord(10, 20, "CHASE", "chase", visual_confidence=0.8, interesting_score=0.7),
            GameplayEventRecord(20, 30, "EXPLORATION", "walk", visual_confidence=0.7, interesting_score=0.2),
        ]

        svc = GameplayIndexService()
        with session_scope() as session:
            count = svc.persist_timeline(session, timeline, source_id=source_id)
            assert count == 3
            assert svc.is_ready(session, source_id)

            # Query events
            events = svc.get_events(session, source_id)
            assert len(events) == 3

            # Query interesting events
            interesting = svc.get_interesting_events(session, source_id, min_interesting=0.5)
            assert len(interesting) == 2  # COMBAT and CHASE

            # Query by type
            combat = svc.get_events(session, source_id, event_type="COMBAT")
            assert len(combat) == 1
            assert combat[0].event_type == "COMBAT"

    def test_reprocessing_replaces_events(self, fresh_db, source_id):
        from gpcg.application.gameplay_index_service import GameplayIndexService
        from gpcg.infrastructure.database import session_scope

        svc = GameplayIndexService()

        # First analysis
        tl1 = EventTimeline(source_id=source_id, source_path="/tmp/test.mp4", duration=120.0, analysis_version="v1", config_hash="hash1")
        tl1.events = [GameplayEventRecord(0, 10, "COMBAT", "fight")]
        with session_scope() as session:
            svc.persist_timeline(session, tl1, source_id=source_id)

        # Reprocess with new events
        tl2 = EventTimeline(source_id=source_id, source_path="/tmp/test.mp4", duration=120.0, analysis_version="v2", config_hash="hash2")
        tl2.events = [
            GameplayEventRecord(0, 5, "COMBAT", "fight start"),
            GameplayEventRecord(5, 10, "CHASE", "chase begins"),
            GameplayEventRecord(10, 20, "DIALOGUE", "conversation"),
        ]
        with session_scope() as session:
            count = svc.persist_timeline(session, tl2, source_id=source_id)
            assert count == 3

            # Old events should be gone
            events = svc.get_events(session, source_id)
            assert len(events) == 3  # not 4 (old 1 + new 3)

    def test_needs_reprocessing_on_config_change(self, fresh_db, source_id):
        from gpcg.application.gameplay_index_service import GameplayIndexService
        from gpcg.infrastructure.database import session_scope

        svc = GameplayIndexService()
        with session_scope() as session:
            # No analysis yet → needs processing
            assert svc.needs_reprocessing(session, source_id, "hash1")

            # Set as ready with hash1
            svc.set_analysis_status(session, source_id, "ready", config_hash="hash1", event_count=1)

            # Same hash → no reprocessing needed
            assert not svc.needs_reprocessing(session, source_id, "hash1")

            # Different hash → needs reprocessing
            assert svc.needs_reprocessing(session, source_id, "hash2")

    def test_get_compatible_sources(self, fresh_db, source_id):
        from gpcg.application.gameplay_index_service import GameplayIndexService
        from gpcg.domains.games.models import GameplaySource
        from gpcg.infrastructure.database import session_scope

        svc = GameplayIndexService()
        with session_scope() as session:
            # Set source as general_topic=False (only for game-related videos)
            svc.set_compatibility(session, source_id, game_related=True, general_topic=False)

            # Query for GAME_RELATED → should find it
            game_related = svc.get_compatible_sources(session, game_id=1, video_type="GAME_RELATED")
            assert any(s.id == source_id for s in game_related)

            # Query for GENERAL_TOPIC → should NOT find it
            general = svc.get_compatible_sources(session, video_type="GENERAL_TOPIC")
            assert not any(s.id == source_id for s in general)

    def test_save_analysis_json(self, fresh_db, tmp_path):
        from gpcg.application.gameplay_index_service import GameplayIndexService

        timeline = EventTimeline(
            source_id=42,
            source_path="/tmp/test.mp4",
            duration=120.0,
            analysis_version="v1",
        )
        timeline.events = [GameplayEventRecord(0, 10, "COMBAT", "fight")]

        svc = GameplayIndexService()
        path = svc.save_analysis_json(timeline, path=tmp_path / "analysis.json")
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["source_id"] == 42
        assert data["event_count"] == 1
