"""Gameplay analyzer — automatic semantic understanding of gameplay recordings.

Pipeline (separate from video generation):
  1. COARSE PASS: sample 1 frame per N-second segment → VLM → identify boundaries
  2. ADAPTIVE REFINEMENT: densify sampling only in high-activity/change zones
  3. AUDIO/ASR: transcribe audio track (if present) → timed segments
  4. MERGE: combine visual events + transcript → GameplayEventRecord[]
  5. INTERESTING SCORE: VLM rates editorial usefulness of each event

The result is an EventTimeline stored in the semantic index (GameplayEvent
table). Video generation queries this index instead of reprocessing the MP4.

Key principles:
  - Adaptive, NOT fixed-interval sampling. Dense zones get more frames.
  - VLM is instructed to NEVER invent events. Ambiguous → POSSIBLE_ + low conf.
  - visual_confidence (what we see) is SEPARATE from interesting_score (usefulness).
  - No manual clip classification. The system discovers events on its own.
  - Clips are NOT extracted physically — only temporal references are stored.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from gpcg.config import get_settings
from gpcg.domain.gameplay_events import (
    AnalysisConfig,
    AudioSegment,
    CoarseSegment,
    EventTimeline,
    GameplayEventRecord,
    RawFrameObservation,
    RefinedEvent,
)
from gpcg.infrastructure.asr_transcriber import ASRTranscriber, get_asr_transcriber
from gpcg.infrastructure.frame_sampler import FrameSampler, SampledFrame
from gpcg.infrastructure.image_enhancer import EnhancementConfig, enhance_crop, save_image
from gpcg.infrastructure.llm import LLMError
from gpcg.infrastructure.media import MediaError, probe
from gpcg.infrastructure.player_detector import PlayerDetector, PlayerDetection, load_frame
from gpcg.infrastructure.vision_analyzer import VisionAnalyzer
from gpcg.logging import get_logger

log = get_logger(__name__)


class GameplayAnalyzer:
    """Orchestrates the multi-pass gameplay analysis pipeline.

    Dependencies are injectable for testing (FakeVisionAnalyzer, FakeASRTranscriber).

    When camera_type is set (e.g., "third_person"), the analyzer uses a CASCADED
    pipeline: YOLO detects the player → crop + upscale → VLM classifies the
    player's action on the crop + VLM describes the environment on the full frame.
    This dramatically improves accuracy for third-person games where the player
    is small in the frame. When camera_type is "unknown", falls back to the
    legacy full-frame VLM analysis.
    """

    def __init__(
        self,
        vision: Optional[VisionAnalyzer] = None,
        asr: Optional[ASRTranscriber] = None,
        sampler: Optional[FrameSampler] = None,
        config: Optional[AnalysisConfig] = None,
        player_detector: Optional[PlayerDetector] = None,
        camera_type: str = "unknown",
    ) -> None:
        s = get_settings()
        self.vision = vision or VisionAnalyzer()
        self.asr = asr or get_asr_transcriber()
        self.sampler = sampler or FrameSampler()
        self.player_detector = player_detector  # lazy-init when needed
        self.camera_type = camera_type
        self.config = config or AnalysisConfig(
            coarse_segment_sec=s.gpcg_gameplay_coarse_segment_sec,
            refine_interval_sec=s.gpcg_gameplay_refine_interval_sec,
            activity_threshold=s.gpcg_gameplay_activity_threshold,
            high_activity_threshold=s.gpcg_gameplay_high_activity_threshold,
            ultra_refine_interval_sec=s.gpcg_gameplay_ultra_refine_interval_sec,
            interesting_threshold=s.gpcg_gameplay_interesting_threshold,
            vlm_batch_size=s.gpcg_gameplay_vlm_batch_size,
            analysis_version=s.gpcg_gameplay_analysis_version,
            vision_model=s.gpcg_gameplay_vision_model,
            asr_model=s.gpcg_gameplay_asr_model,
            asr_device=s.gpcg_gameplay_asr_device,
            asr_compute_type=s.gpcg_gameplay_asr_compute_type,
        )

    @property
    def use_cascade(self) -> bool:
        """True if the cascaded pipeline (YOLO + crop + VLM) should be used."""
        return self.camera_type != "unknown"

    def _get_detector(self) -> PlayerDetector:
        """Lazy-init the player detector (YOLO model load is expensive)."""
        if self.player_detector is None:
            self.player_detector = PlayerDetector()
        return self.player_detector

    def analyze(
        self,
        source_path: str | Path,
        source_id: int = 0,
        *,
        enable_asr: Optional[bool] = None,
        enable_interesting_score: Optional[bool] = None,
        camera_type: Optional[str] = None,
        progress_callback: Optional[callable] = None,
        save_crops_to: Optional[Path] = None,
    ) -> EventTimeline:
        """Run the full analysis pipeline on a gameplay recording.

        Args:
            source_path: path to the gameplay video file
            source_id: DB source ID (for the timeline record)
            enable_asr: override ASR enable (default from config)
            enable_interesting_score: override interesting scoring (default from config)
            camera_type: override the camera type for this analysis run
                (e.g., "third_person", "first_person"). When set, enables the
                cascaded pipeline (YOLO + crop + VLM). When None, uses the
                camera_type set in the constructor.
            progress_callback: optional callable(stage: str, pct: float) for progress
            save_crops_to: optional directory to save player crops (cascaded mode
                only, for debugging). Created if it doesn't exist.

        Returns:
            EventTimeline with all detected events
        """
        source_path = Path(source_path)
        if camera_type is not None:
            self.camera_type = camera_type
        t0 = time.time()
        do_asr = enable_asr if enable_asr is not None else self.config.enable_asr
        do_score = enable_interesting_score if enable_interesting_score is not None else self.config.enable_interesting_score

        log.info(f"analyzing gameplay: {source_path.name} (camera_type={self.camera_type}, cascade={self.use_cascade})")
        info = probe(source_path)
        log.info(f"duration={info.duration:.1f}s, has_audio={info.has_audio}, {info.width}x{info.height}")

        # Bump analysis version when using cascade (so re-analysis is triggered)
        version = self.config.analysis_version
        if self.use_cascade:
            version = f"{self.config.analysis_version}-cascade"

        timeline = EventTimeline(
            source_id=source_id,
            source_path=str(source_path),
            duration=info.duration,
            analysis_version=version,
            vision_model=self.config.vision_model,
            asr_model=self.config.asr_model if do_asr else "",
            config_hash=self.config.to_hash(),
            has_audio=info.has_audio,
        )

        # ── Pass 1: Coarse analysis ──────────────────────────────────────
        if progress_callback:
            progress_callback("coarse", 0.0)
        coarse_segments = self._coarse_pass(source_path, info.duration, progress_callback, save_crops_to)
        log.info(f"coarse pass: {len(coarse_segments)} segments, "
                 f"{sum(1 for s in coarse_segments if s.needs_refinement)} need refinement")

        # ── Pass 2: Adaptive refinement ──────────────────────────────────
        if progress_callback:
            progress_callback("refine", 0.0)
        refined_events = self._adaptive_refine(source_path, coarse_segments, progress_callback, save_crops_to)
        log.info(f"refinement: {len(refined_events)} granular events")

        # ── Pass 3: Audio/ASR ────────────────────────────────────────────
        audio_segments: list[AudioSegment] = []
        if do_asr and info.has_audio and self.asr.is_available():
            if progress_callback:
                progress_callback("asr", 0.0)
            audio_segments = self._asr_pass(source_path, progress_callback)
            timeline.has_transcript = len(audio_segments) > 0
            log.info(f"ASR: {len(audio_segments)} transcript segments")
        else:
            log.info("ASR skipped (no audio, disabled, or unavailable)")

        # ── Pass 4: Merge visual + audio ─────────────────────────────────
        if progress_callback:
            progress_callback("merge", 0.0)
        merged = self._merge_events(refined_events, audio_segments)
        log.info(f"merge: {len(merged)} final events")

        # ── Pass 5: Interesting score ────────────────────────────────────
        if do_score and merged:
            if progress_callback:
                progress_callback("score", 0.0)
            self._score_events(merged, progress_callback)
            log.info(f"scored {len(merged)} events")

        timeline.events = merged
        elapsed = time.time() - t0
        log.info(f"analysis complete: {len(timeline.events)} events in {elapsed:.1f}s")

        if progress_callback:
            progress_callback("done", 1.0)
        return timeline

    def _cascaded_analyze_batch(
        self,
        batch: list[SampledFrame],
        interval_sec: float,
        save_crops_to: Optional[Path] = None,
    ) -> RawFrameObservation:
        """Cascaded analysis of a batch of frames.

        Analyzes each frame individually (YOLO → crop → VLM player + VLM env),
        then merges the per-frame observations into a single observation
        representing the whole batch. This detects motion/progression by
        comparing player states across frames.

        This is more expensive than the legacy batch VLM call (N×VLM instead
        of 1×VLM), but much more accurate for third-person games.
        """
        if not batch:
            return RawFrameObservation(timestamp=0.0)
        if len(batch) == 1:
            return self._cascaded_analyze_frame(
                batch[0].path, batch[0].timestamp, save_crops_to
            )

        # Analyze each frame in the batch
        observations: list[RawFrameObservation] = []
        for f in batch:
            obs = self._cascaded_analyze_frame(f.path, f.timestamp, save_crops_to)
            observations.append(obs)

        # Merge into a single observation
        return self._merge_batch_observations(observations, batch[0].timestamp)

    def _merge_batch_observations(
        self,
        observations: list[RawFrameObservation],
        start_time: float,
    ) -> RawFrameObservation:
        """Merge per-frame observations into a single batch observation.

        Detects progression by comparing actions across frames:
        - If actions change (e.g., standing → running → fighting), describe the arc
        - If actions are similar, report as a stable event
        - Activity level = max across frames
        - Visual confidence = average
        """
        if not observations:
            return RawFrameObservation(timestamp=start_time)
        if len(observations) == 1:
            return observations[0]

        # Collect unique actions in order
        all_actions: list[str] = []
        seen_actions: set[str] = set()
        for obs in observations:
            for a in obs.actions:
                if a not in seen_actions:
                    all_actions.append(a)
                    seen_actions.add(a)

        # Use the most common event type (mode)
        type_counts: dict[str, int] = {}
        for obs in observations:
            t = obs.event_type
            type_counts[t] = type_counts.get(t, 0) + 1
        event_type = max(type_counts, key=type_counts.get) if type_counts else "UNKNOWN"

        # Description: combine first + last if different (shows progression)
        first_desc = observations[0].description
        last_desc = observations[-1].description
        if first_desc and last_desc and first_desc != last_desc:
            description = f"{first_desc} Then {last_desc.lower()}"
        else:
            description = first_desc or last_desc or "Activity across frames"

        # Location: most common
        locations = [obs.location for obs in observations if obs.location]
        location = max(set(locations), key=locations.count) if locations else ""

        # Tags: union
        all_tags: set[str] = set()
        for obs in observations:
            all_tags.update(obs.tags)

        # Characters: union
        all_chars: set[str] = set()
        for obs in observations:
            all_chars.update(obs.characters)

        # Activity: max
        max_activity = max(obs.activity_level for obs in observations)

        # Confidence: average
        confidences = [obs.visual_confidence for obs in observations if obs.visual_confidence > 0]
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

        obs = RawFrameObservation(
            timestamp=start_time,
            event_type=event_type,
            description=description,
            characters=list(all_chars),
            location=location,
            actions=all_actions,
            activity_level=max_activity,
            visual_confidence=avg_conf,
            tags=list(all_tags),
        )
        obs.event_type = obs.normalize_type()
        return obs

    # ── Pass 1: Coarse ──────────────────────────────────────────────────

    def _cascaded_analyze_frame(
        self,
        frame_path: Path,
        timestamp: float,
        save_crops_to: Optional[Path] = None,
    ) -> RawFrameObservation:
        """Cascaded analysis: YOLO → crop → VLM player action + VLM environment → merge.

        This is the enhanced analysis path used when camera_type is set (not "unknown").
        It produces a much more accurate observation than the legacy full-frame VLM
        analysis, especially for third-person games where the player is small.

        Args:
            frame_path: path to the full frame image
            timestamp: frame timestamp (for the observation)
            save_crops_to: optional directory to save crops (for debugging)

        Returns:
            RawFrameObservation with merged player action + environment data
        """
        import tempfile

        try:
            frame = load_frame(frame_path)
        except Exception as e:
            log.warning(f"could not load frame {frame_path}: {e}")
            return RawFrameObservation(timestamp=timestamp)

        detector = self._get_detector()
        detection = detector.detect(frame, camera_type=self.camera_type)

        # If no player detected, fall back to full-frame analysis
        if not detection.has_player:
            log.debug(f"no player detected at {timestamp:.1f}s, falling back to full-frame")
            try:
                return self.vision.analyze_single_frame(frame_path)
            except LLMError:
                return RawFrameObservation(timestamp=timestamp)

        # Stage 2a: Crop + enhance the player region
        crop, adj_bbox = enhance_crop(frame, detection.player_bbox, EnhancementConfig())

        # Save crop to temp file for VLM
        crop_path = Path(tempfile.mktemp(suffix=".jpg", prefix="gpcg_crop_"))
        try:
            save_image(crop, str(crop_path))

            # Optionally save crop for debugging
            if save_crops_to is not None:
                save_crops_to.mkdir(parents=True, exist_ok=True)
                debug_name = f"crop_{timestamp:07.1f}.jpg"
                save_image(crop, str(save_crops_to / debug_name))

            # Format YOLO context (neutral — no "weapons" label)
            yolo_parts = []
            for v in detection.nearby_vehicles:
                yolo_parts.append(f"{v.cls} (conf={v.confidence:.2f})")
            for w in detection.weapons:
                yolo_parts.append(f"{w.cls} (conf={w.confidence:.2f})")
            yolo_context = (
                f"objects detected near player: {', '.join(yolo_parts)}"
                if yolo_parts
                else "no notable objects detected near player"
            )

            # Stage 2b: VLM analyzes player action (on crop)
            try:
                player_data = self.vision.analyze_player_action(crop_path, yolo_context)
            except LLMError as e:
                log.warning(f"VLM player action failed at {timestamp:.1f}s: {e}")
                player_data = {}

            # Stage 2c: VLM analyzes environment (on full frame)
            try:
                env_data = self.vision.analyze_environment(frame_path)
            except LLMError as e:
                log.warning(f"VLM environment failed at {timestamp:.1f}s: {e}")
                env_data = {}

        finally:
            crop_path.unlink(missing_ok=True)

        # Merge into RawFrameObservation
        return self._merge_cascade_to_observation(
            player_data, env_data, detection, timestamp
        )

    def _merge_cascade_to_observation(
        self,
        player: dict,
        env: dict,
        detection: PlayerDetection,
        timestamp: float,
    ) -> RawFrameObservation:
        """Merge cascaded analysis results into a RawFrameObservation.

        Combines:
        - Player action (from crop VLM): movement, combat, held item
        - Environment (from full-frame VLM): location, other characters, UI
        - YOLO detection data: is_riding, is_armed (as corroboration)
        """
        movement = str(player.get("movement", "unknown"))
        movement_detail = str(player.get("movement_detail", ""))
        combat_state = str(player.get("combat_state", "none"))
        held_item = str(player.get("held_item", "none"))
        held_detail = str(player.get("held_item_detail", ""))
        action_desc = str(player.get("action_description", ""))
        player_conf = float(player.get("confidence", 0.0))

        location = str(env.get("location", ""))
        location_detail = str(env.get("location_detail", ""))
        env_desc = str(env.get("environment_description", ""))
        other_chars = str(env.get("other_characters", "none"))
        other_chars_detail = str(env.get("other_characters_detail", ""))
        ui_elements = str(env.get("ui_elements", "none"))

        # Determine event_type from player state
        event_type = self._infer_event_type(
            movement, combat_state, detection, other_chars, ui_elements,
            movement_detail=movement_detail,
        )

        # Build description: combine player action + environment
        desc_parts = []
        if action_desc:
            desc_parts.append(action_desc)
        if env_desc and env_desc != action_desc:
            desc_parts.append(env_desc)
        description = " ".join(desc_parts) if desc_parts else "Unknown activity"

        # Build actions list from player state
        actions = []
        if movement_detail and movement_detail != "unknown":
            actions.append(movement_detail)
        if combat_state == "fighting":
            actions.append("fighting")
        if combat_state == "armed" or held_item == "weapon":
            actions.append(f"armed with {held_detail}" if held_detail else "armed")
        if detection.is_riding:
            actions.append("riding")

        # Build tags
        tags = [movement]
        if combat_state != "none" and combat_state != "unknown":
            tags.append(combat_state)
        if detection.is_riding:
            tags.append("riding")
        if detection.is_armed:
            tags.append("armed")
        if location:
            tags.append(location)
        if ui_elements and ui_elements != "none":
            tags.append(f"ui:{ui_elements}")

        # Activity level: combat > riding > moving > standing
        if combat_state == "fighting":
            activity = 0.9
        elif combat_state in ("armed", "aiming", "shooting"):
            activity = 0.6
        elif detection.is_riding or movement in ("on_bike", "on_skate", "on_vehicle"):
            activity = 0.4
        elif movement_detail in ("running", "sprinting"):
            activity = 0.5
        elif movement_detail in ("walking",):
            activity = 0.3
        else:
            activity = 0.1

        # Visual confidence: average of player + env confidence
        env_conf = float(env.get("confidence", 0.0))
        visual_conf = (player_conf + env_conf) / 2 if env_conf > 0 else player_conf

        # Characters
        characters = []
        if other_chars != "none" and other_chars_detail:
            characters.append(other_chars_detail)

        obs = RawFrameObservation(
            timestamp=timestamp,
            event_type=event_type,
            description=description,
            characters=characters,
            location=location_detail or location,
            actions=actions,
            activity_level=activity,
            visual_confidence=visual_conf,
            tags=tags,
        )
        obs.event_type = obs.normalize_type()
        return obs

    def _infer_event_type(
        self,
        movement: str,
        combat_state: str,
        detection: PlayerDetection,
        other_chars: str,
        ui_elements: str,
        movement_detail: str = "",
    ) -> str:
        """Infer the event type from cascaded analysis signals."""
        # UI elements take priority (menu, dialogue, loading)
        if ui_elements in ("menu", "loading"):
            return ui_elements.upper()
        if ui_elements == "dialogue":
            return "DIALOGUE"

        # Combat
        if combat_state == "fighting":
            return "COMBAT"
        if combat_state in ("armed", "aiming", "shooting"):
            return "COMBAT"

        # Vehicle
        if movement in ("on_bike", "on_skate", "on_vehicle"):
            return "VEHICLE"
        if detection.is_riding:
            return "VEHICLE"

        # Movement-based
        if movement_detail in ("running", "sprinting"):
            return "CHASE"
        if movement_detail in ("walking",):
            return "TRAVEL"

        # Default
        return "EXPLORATION"

    def _coarse_pass(
        self,
        source: Path,
        duration: float,
        progress_callback: Optional[callable] = None,
        save_crops_to: Optional[Path] = None,
    ) -> list[CoarseSegment]:
        """First pass: one frame per segment, identify boundaries and activity."""
        frame_dir = self.sampler.sampler_tmp if hasattr(self.sampler, 'sampler_tmp') else None
        import tempfile
        frame_dir = Path(tempfile.mkdtemp(prefix="gpcg_coarse_"))

        try:
            frames = self.sampler.coarse_sample(
                source,
                segment_sec=self.config.coarse_segment_sec,
                output_dir=frame_dir,
            )
            segments: list[CoarseSegment] = []
            prev_obs: Optional[RawFrameObservation] = None

            for i, frame in enumerate(frames):
                if progress_callback:
                    pct = (i + 1) / max(1, len(frames)) * 0.3  # coarse = 30% of progress
                    progress_callback("coarse", pct)

                try:
                    if self.use_cascade:
                        obs = self._cascaded_analyze_frame(frame.path, frame.timestamp, save_crops_to)
                    else:
                        obs = self.vision.analyze_single_frame(frame.path)
                except LLMError as e:
                    log.warning(f"VLM failed on frame {i} ({frame.timestamp:.1f}s): {e}")
                    obs = RawFrameObservation(timestamp=frame.timestamp)

                seg_start = frame.timestamp - self.config.coarse_segment_sec / 2
                seg_end = frame.timestamp + self.config.coarse_segment_sec / 2
                seg = CoarseSegment(
                    start=max(0, seg_start),
                    end=min(duration, seg_end),
                    observation=obs,
                )

                # Detect boundary: significant change from previous segment
                if prev_obs is not None:
                    seg.is_boundary = self._is_boundary(prev_obs, obs)
                    seg.needs_refinement = (
                        seg.is_boundary
                        or obs.activity_level >= self.config.activity_threshold
                    )

                segments.append(seg)
                prev_obs = obs

            # Also mark the last segment if it had high activity
            if segments and not segments[-1].needs_refinement:
                if segments[-1].observation.activity_level >= self.config.activity_threshold:
                    segments[-1].needs_refinement = True

            return segments
        finally:
            self.sampler.cleanup_dir(frame_dir)

    def _is_boundary(self, prev: RawFrameObservation, curr: RawFrameObservation) -> bool:
        """Detect if there's a significant change between two observations."""
        # Type change
        if prev.event_type != curr.event_type:
            # Don't flag UNKNOWN→X or X→UNKNOWN as boundaries (uncertainty)
            if prev.event_type != "UNKNOWN" and curr.event_type != "UNKNOWN":
                return True
        # Large activity level change
        activity_delta = abs(prev.activity_level - curr.activity_level)
        if activity_delta >= 0.3:
            return True
        # Location change
        if prev.location and curr.location and prev.location.lower() != curr.location.lower():
            return True
        return False

    # ── Pass 2: Adaptive refinement ─────────────────────────────────────

    def _adaptive_refine(
        self,
        source: Path,
        coarse_segments: list[CoarseSegment],
        progress_callback: Optional[callable] = None,
        save_crops_to: Optional[Path] = None,
    ) -> list[RefinedEvent]:
        """Refine only the segments that need it (boundaries + high activity)."""
        if not coarse_segments:
            return []

        import tempfile
        events: list[RefinedEvent] = []
        total_to_refine = sum(1 for s in coarse_segments if s.needs_refinement)
        refined_count = 0

        for seg in coarse_segments:
            if progress_callback and total_to_refine > 0:
                pct = 0.3 + (refined_count / total_to_refine) * 0.3  # refine = 30%
                progress_callback("refine", pct)

            if not seg.needs_refinement:
                # Low-activity segment: emit as a single event from coarse observation
                obs = seg.observation
                events.append(RefinedEvent(
                    start=seg.start,
                    end=seg.end,
                    event_type=obs.normalize_type(),
                    description=obs.description or "Low activity segment",
                    characters=obs.characters,
                    location=obs.location,
                    actions=obs.actions,
                    visual_confidence=obs.visual_confidence,
                    activity_level=obs.activity_level,
                    tags=obs.tags,
                ))
                continue

            # Determine refinement interval based on activity level
            if seg.observation.activity_level >= self.config.high_activity_threshold:
                interval = self.config.ultra_refine_interval_sec
            else:
                interval = self.config.refine_interval_sec

            # Dense sample within this segment
            frame_dir = Path(tempfile.mkdtemp(prefix="gpcg_refine_"))
            try:
                frames = self.sampler.dense_sample(
                    source,
                    start=seg.start,
                    end=seg.end,
                    interval_sec=interval,
                    output_dir=frame_dir,
                )

                if not frames:
                    # Fallback to coarse observation
                    obs = seg.observation
                    events.append(RefinedEvent(
                        start=seg.start,
                        end=seg.end,
                        event_type=obs.normalize_type(),
                        description=obs.description,
                        characters=obs.characters,
                        location=obs.location,
                        actions=obs.actions,
                        visual_confidence=obs.visual_confidence,
                        activity_level=obs.activity_level,
                        tags=obs.tags,
                    ))
                    continue

                # Process frames in batches via VLM
                batch_size = self.config.vlm_batch_size
                for batch_start in range(0, len(frames), batch_size):
                    batch = frames[batch_start:batch_start + batch_size]
                    batch_frames = [f.path for f in batch]
                    batch_start_time = batch[0].timestamp
                    batch_end_time = batch[-1].timestamp + interval

                    try:
                        if self.use_cascade:
                            # Cascaded mode: analyze each frame individually
                            # (YOLO + crop + VLM per frame), then merge.
                            # This is more expensive but much more accurate.
                            obs = self._cascaded_analyze_batch(
                                batch, interval, save_crops_to=save_crops_to
                            )
                        else:
                            obs = self.vision.analyze_frame_batch(
                                batch_frames,
                                start_time=batch_start_time,
                                interval_sec=interval,
                            )
                    except LLMError as e:
                        log.warning(f"VLM batch failed at {batch_start_time:.1f}s: {e}")
                        obs = RawFrameObservation(timestamp=batch_start_time)

                    events.append(RefinedEvent(
                        start=batch_start_time,
                        end=min(seg.end, batch_end_time),
                        event_type=obs.normalize_type(),
                        description=obs.description,
                        characters=obs.characters,
                        location=obs.location,
                        actions=obs.actions,
                        visual_confidence=obs.visual_confidence,
                        activity_level=obs.activity_level,
                        tags=obs.tags,
                    ))
            finally:
                self.sampler.cleanup_dir(frame_dir)

            refined_count += 1

        # Merge adjacent events of the same type (reduce fragmentation)
        events = self._merge_adjacent_same_type(events)
        return events

    def _merge_adjacent_same_type(self, events: list[RefinedEvent]) -> list[RefinedEvent]:
        """Merge consecutive events with the same type and similar descriptions."""
        if len(events) <= 1:
            return events

        merged: list[RefinedEvent] = [events[0]]
        for ev in events[1:]:
            prev = merged[-1]
            if (
                ev.event_type == prev.event_type
                and ev.start - prev.end < 2.0  # within 2s gap
                and ev.visual_confidence == prev.visual_confidence
            ):
                # Extend the previous event
                prev.end = ev.end
                # Append description if different
                if ev.description and ev.description != prev.description:
                    prev.description = f"{prev.description} {ev.description}"
                # Merge actions/characters/tags (dedup)
                prev.actions = list(set(prev.actions + ev.actions))
                prev.characters = list(set(prev.characters + ev.characters))
                prev.tags = list(set(prev.tags + ev.tags))
                # Take the higher activity level
                prev.activity_level = max(prev.activity_level, ev.activity_level)
            else:
                merged.append(ev)

        return merged

    # ── Pass 3: ASR ─────────────────────────────────────────────────────

    def _asr_pass(
        self,
        source: Path,
        progress_callback: Optional[callable] = None,
    ) -> list[AudioSegment]:
        """Extract audio and transcribe via faster-whisper."""
        import tempfile
        audio_path = Path(tempfile.mktemp(suffix=".wav", prefix="gpcg_asr_"))
        try:
            self.sampler.extract_audio(source, audio_path)
            if progress_callback:
                progress_callback("asr", 0.5)
            segments = self.asr.transcribe_with_fallback(audio_path)
            if progress_callback:
                progress_callback("asr", 1.0)
            return segments
        except MediaError as e:
            log.warning(f"audio extraction failed: {e}")
            return []
        finally:
            if audio_path.exists():
                audio_path.unlink(missing_ok=True)

    # ── Pass 4: Merge visual + audio ─────────────────────────────────────

    def _merge_events(
        self,
        visual: list[RefinedEvent],
        audio: list[AudioSegment],
    ) -> list[GameplayEventRecord]:
        """Merge visual events with overlapping audio transcripts."""
        records: list[GameplayEventRecord] = []

        for ev in visual:
            # Find overlapping audio segments
            overlapping_text: list[str] = []
            for seg in audio:
                # Audio segment overlaps with visual event if:
                if seg.start < ev.end and seg.end > ev.start:
                    overlapping_text.append(seg.text)

            transcript = " ".join(overlapping_text).strip()

            records.append(GameplayEventRecord(
                start_time=ev.start,
                end_time=ev.end,
                event_type=ev.event_type,
                description=ev.description,
                characters=ev.characters,
                location=ev.location,
                actions=ev.actions,
                tags=ev.tags,
                transcript=transcript,
                visual_confidence=ev.visual_confidence,
                interesting_score=0.0,  # filled in pass 5
                metadata={
                    "activity_level": ev.activity_level,
                    "has_transcript": bool(transcript),
                },
            ))

        return records

    # ── Pass 5: Interesting score ───────────────────────────────────────

    def _score_events(
        self,
        events: list[GameplayEventRecord],
        progress_callback: Optional[callable] = None,
    ) -> None:
        """Rate each event's editorial interestingness (in-place)."""
        for i, ev in enumerate(events):
            if progress_callback:
                pct = 0.6 + (i + 1) / max(1, len(events)) * 0.4  # score = 40%
                progress_callback("score", pct)

            # Skip scoring for obviously boring events (saves VLM calls)
            if ev.event_type in ("LOADING", "MENU", "IDLE") and ev.visual_confidence > 0.7:
                ev.interesting_score = 0.1
                continue
            if ev.event_type == "UNKNOWN":
                ev.interesting_score = 0.0
                continue

            try:
                score = self.vision.score_interesting(ev.description, ev.event_type)
                ev.interesting_score = score
            except Exception as e:
                log.warning(f"interesting score failed for event {i}: {e}")
                ev.interesting_score = 0.0
