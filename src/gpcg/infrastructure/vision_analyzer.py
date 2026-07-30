"""Vision analyzer — VLM abstraction for gameplay frame analysis.

Abstracts the vision model behind an interface so it can be swapped
(e.g. gemma3:12b → qwen3-vl → minicpm-v) without changing the analyzer.

Currently uses LLMClient.vision_json() which talks to Ollama's /api/chat
with base64-encoded images. The default VLM is gemma3:12b (already installed
and used for game resolution L3).

Key principle: the VLM is instructed to NEVER invent events it cannot see.
Ambiguous observations get POSSIBLE_ prefix and low confidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from gpcg.config import get_settings
from gpcg.domain.gameplay_events import RawFrameObservation
from gpcg.infrastructure.llm import LLMClient, LLMError
from gpcg.logging import get_logger

log = get_logger(__name__)


# ── Prompts ──────────────────────────────────────────────────────────────────

COARSE_PROMPT = """You are analyzing a single frame from a gameplay video recording.
Look at the image and describe what is happening RIGHT NOW.

CRITICAL RULES:
- Only describe what you can ACTUALLY SEE in this frame.
- NEVER invent or guess events that are not visible.
- If you cannot tell what's happening, say so honestly.
- If something is ambiguous, use POSSIBLE_ prefix and low confidence.

Return ONLY valid JSON (no markdown, no text before or after):
{
  "event_type": "COMBAT|CHASE|DIALOGUE|CUTSCENE|EXPLORATION|VEHICLE|MENU|LOADING|PUZZLE|STEALTH|INTERACTION|MINIGAME|TRAVEL|IDLE|UNKNOWN|POSSIBLE_<X>",
  "description": "One sentence describing what you see.",
  "characters": ["list of visible characters if identifiable, else empty"],
  "location": "visible setting if identifiable, else empty string",
  "actions": ["list of actions being performed, e.g. running, fighting"],
  "activity_level": 0.0,
  "visual_confidence": 0.0,
  "tags": ["any relevant free-form tags"]
}

activity_level: 0.0 = nothing happening (menu, loading, static), 1.0 = intense action (combat, chase)
visual_confidence: 0.0 = no idea, 1.0 = absolutely certain of what I see
If unsure about event_type, use "UNKNOWN" or "POSSIBLE_<best_guess>" with confidence < 0.5."""

REFINE_PROMPT = """You are analyzing {n} consecutive frames from a gameplay video, taken {interval}s apart within a {duration:.1f}s window starting at {start:.1f}s.
Frames are ordered chronologically (frame 1 = earliest).

Describe the EVENT that occurs across these frames. Unlike a single frame, you can now see MOTION and PROGRESSION.

CRITICAL RULES:
- Only describe what you can ACTUALLY SEE across these frames.
- NEVER invent events. If the frames are ambiguous, say so.
- If you see a clear action progressing (e.g. fight starting → punches → someone falling), describe that arc.
- If frames look similar (no change), report as EXPLORATION or IDLE with appropriate confidence.
- Ambiguous events get POSSIBLE_ prefix and confidence < 0.5.

Return ONLY valid JSON:
{{
  "event_type": "COMBAT|CHASE|DIALOGUE|CUTSCENE|EXPLORATION|VEHICLE|MENU|LOADING|PUZZLE|STEALTH|INTERACTION|MINIGAME|TRAVEL|IDLE|UNKNOWN|POSSIBLE_<X>",
  "description": "1-3 sentences describing the event across these frames.",
  "characters": ["identifiable characters"],
  "location": "setting if identifiable",
  "actions": ["actions observed, in order if progression is visible"],
  "activity_level": 0.0,
  "visual_confidence": 0.0,
  "tags": ["relevant tags"]
}}"""

INTERESTING_PROMPT = """You are an editorial assistant evaluating gameplay footage for video editing.

Given a gameplay event description, rate how INTERESTING it would be for a viewer watching a short video. This is NOT about how confident we are about what's happening — it's about whether this moment is worth showing.

High interesting_score (0.8-1.0): combat, chases, funny glitches, dramatic moments, unexpected events, intense action.
Medium (0.4-0.7): dialogue, exploration with visible action, interactions, vehicle sections.
Low (0.0-0.3): menus, loading screens, idle, long static shots, walking with nothing happening.

Event: {description}
Type: {event_type}

Return ONLY JSON: {{"interesting_score": 0.0}}"""


# ── Cascaded analysis prompts (player crop + environment) ────────────────────

PLAYER_ACTION_PROMPT = """You are analyzing a CROPPED and ENHANCED image of the PLAYER CHARACTER from a gameplay video.
The image has been cropped around the detected player and upscaled so you can see clearly what the player is doing.

Your job: identify the player's ACTION and STATE with high precision.

## What to identify

1. MOVEMENT STATE: What is the player doing right now?
   - on_foot: standing, walking, running
   - on_bike: riding a bicycle/BMX
   - on_skate: riding a skateboard
   - on_vehicle: driving a car/kart/motorcycle/boat
   - swimming, climbing, crawling, falling, etc.

2. COMBAT STATE: Is the player in combat?
   - fighting: punching, kicking, grappling
   - armed: holding a weapon (bat, gun, knife, etc.)
   - aiming: pointing a weapon at something
   - shooting: firing a weapon
   - hit: being hit/stunned
   - none: no combat

3. POSTURE: standing, crouching, prone, sitting, lying down

4. HELD ITEM: What is the player holding? (weapon, ball, nothing, etc.)

5. INTERACTION: Is the player interacting with something? (door, object, NPC)

## Context from object detection (YOLO)

An object detector scanned the full frame and found these objects near the
player's position:
{yolo_context}

IMPORTANT: This is GENERIC object detection from a standard taxonomy (COCO).
It does NOT know about game-specific objects. A "baseball bat" detection
might be a pipe, a stick, a fence post, or nothing. A "boat" might be a
go-kart. Use these hints ONLY to know what to LOOK FOR — then decide based
on what you ACTUALLY SEE in the cropped image. Do NOT trust the YOLO class
names blindly.

## CRITICAL — Anti-hallucination rules

- ONLY report a weapon if you can CLEARLY see it in the PLAYER'S HAND.
  A blurry shape that might be a weapon is NOT a weapon — report "none".
- ONLY report "fighting" if you can see active combat (punches, kicks,
  grappling). A character standing near another character is NOT fighting.
- Upscaled images may have artifacts that look like objects. If you're not
  SURE you see something, report "unknown" or "none" — do NOT guess.
- When in doubt, say LESS. "standing, no visible weapon" is better than
  "holding a gun" if you're not sure.
- The DEFAULT state is: on_foot, standing, combat=none, held_item=none.
  Only deviate from this if you have CLEAR visual evidence.

Return ONLY valid JSON:
{{
  "movement": "on_foot|on_bike|on_skate|on_vehicle|swimming|climbing|crawling|falling|unknown",
  "movement_detail": "walking|running|standing|sprinting|...",
  "combat_state": "fighting|armed|aiming|shooting|hit|none|unknown",
  "posture": "standing|crouching|prone|sitting|lying|unknown",
  "held_item": "none|weapon|ball|object|unknown",
  "held_item_detail": "baseball bat|gun|skateboard|...",
  "interaction": "none|door|object|npc|unknown",
  "action_description": "One sentence: what is the player doing right now?",
  "confidence": 0.0
}}

confidence: 0.0 = can't tell, 1.0 = absolutely certain.
If you are guessing or unsure, confidence should be below 0.5."""


ENVIRONMENT_PROMPT = """You are analyzing a FULL FRAME from a gameplay video recording.
This is the complete frame (not cropped). Your job: describe the ENVIRONMENT and SETTING.

The player's action is being analyzed separately from a cropped image. Your job
here is ONLY the environment/context.

## What to identify

1. LOCATION: Where is this? (school, street, indoor, outdoor, factory, etc.)
2. TIME_OF_DAY: day, night, dusk, dawn, indoor-lighting
3. WEATHER: clear, rain, snow, fog, indoor
4. SETTING_DETAILS: notable objects, buildings, landmarks visible
5. OTHER_CHARACTERS: Are there other characters visible? How many? What are they doing?
6. UI_ELEMENTS: Is there a HUD, minimap, health bar, dialogue box, menu visible?

## Rules

- Only describe what you can ACTUALLY SEE.
- Do NOT describe what the player is doing (that's analyzed separately).
- Focus on the environment and context.

Return ONLY valid JSON:
{{
  "location": "school|street|indoor|outdoor|factory|park|...",
  "location_detail": "more specific description",
  "time_of_day": "day|night|dusk|dawn|indoor",
  "weather": "clear|rain|snow|fog|indoor",
  "setting_details": "notable objects/buildings visible",
  "other_characters": "none|few|many",
  "other_characters_detail": "what are they doing",
  "ui_elements": "none|hud|minimap|dialogue|menu|...",
  "environment_description": "One sentence: describe the setting",
  "confidence": 0.0
}}"""


class VisionAnalyzer:
    """VLM abstraction for gameplay frame analysis.

    Wraps LLMClient.vision_json() with gameplay-specific prompts.
    The model can be swapped via config (gpcg_gameplay_vision_model).
    """

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        model: Optional[str] = None,
    ) -> None:
        self.llm = llm or LLMClient()
        s = get_settings()
        self.model = model or s.gpcg_gameplay_vision_model
        self.temperature = 0.2  # low temp for factual observation
        self.max_tokens = 512

    def analyze_single_frame(self, frame_path: Path) -> RawFrameObservation:
        """Analyze a single frame and return a structured observation.

        Raises LLMError on failure.
        """
        data = self.llm.vision_json(
            images=[frame_path],
            prompt=COARSE_PROMPT,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return self._parse_observation(data, timestamp=0.0)

    def analyze_frame_batch(
        self,
        frames: list[Path],
        *,
        start_time: float = 0.0,
        interval_sec: float = 3.0,
    ) -> RawFrameObservation:
        """Analyze multiple frames as a temporal sequence.

        The VLM sees all frames in one call and describes the event across them.
        Returns a single observation representing the whole batch.
        """
        if not frames:
            raise LLMError("no frames to analyze")
        if len(frames) == 1:
            obs = self.analyze_single_frame(frames[0])
            obs.timestamp = start_time
            return obs

        duration = len(frames) * interval_sec
        prompt = REFINE_PROMPT.format(
            n=len(frames),
            interval=interval_sec,
            duration=duration,
            start=start_time,
        )
        data = self.llm.vision_json(
            images=frames,
            prompt=prompt,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        obs = self._parse_observation(data, timestamp=start_time)
        return obs

    def score_interesting(
        self,
        description: str,
        event_type: str,
    ) -> float:
        """Rate the editorial interestingness of an event (0-1).

        This is SEPARATE from visual_confidence — a high-confidence boring
        event (walking) gets low interesting_score.
        """
        prompt = INTERESTING_PROMPT.format(description=description, event_type=event_type)
        try:
            data = self.llm.chat_json(
                system="You are an editorial scoring assistant. Return only JSON.",
                prompt=prompt,
                model=self.model,
                temperature=0.1,
                max_tokens=64,
            )
            score = float(data.get("interesting_score", 0.0))
            return max(0.0, min(1.0, score))
        except (LLMError, ValueError, TypeError) as e:
            log.warning(f"interesting score failed ({e}), defaulting to 0.0")
            return 0.0

    # ── Cascaded analysis (player crop + environment) ──────────────────────

    def analyze_player_action(
        self,
        crop_path: Path,
        yolo_context: str = "",
    ) -> dict:
        """Analyze a cropped+enhanced player image and return the player's action.

        This is the second stage of the cascaded pipeline: YOLO detected the
        player's bbox, the crop was extracted and enhanced, and now the VLM
        classifies what the player is doing (on foot, bike, skate, vehicle,
        armed, fighting, etc.).

        Args:
            crop_path: path to the enhanced crop image
            yolo_context: formatted string of YOLO detections near the player
                (e.g., "bicycle (conf=0.55), baseball bat (conf=0.60)")

        Returns:
            dict with keys: movement, movement_detail, combat_state, posture,
            held_item, held_item_detail, interaction, action_description,
            confidence

        Raises LLMError on failure.
        """
        prompt = PLAYER_ACTION_PROMPT.format(yolo_context=yolo_context or "(none)")
        data = self.llm.vision_json(
            images=[crop_path],
            prompt=prompt,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        if not isinstance(data, dict):
            return {
                "movement": "unknown",
                "movement_detail": "unknown",
                "combat_state": "unknown",
                "posture": "unknown",
                "held_item": "unknown",
                "held_item_detail": "unknown",
                "interaction": "unknown",
                "action_description": "",
                "confidence": 0.0,
            }
        return data

    def analyze_environment(
        self,
        frame_path: Path,
    ) -> dict:
        """Analyze the full frame and return the environment/setting description.

        This is the environment stage of the cascaded pipeline: while the
        player's action is analyzed from a crop, this call describes the
        setting (location, time of day, other characters, UI elements).

        Args:
            frame_path: path to the full frame image

        Returns:
            dict with keys: location, location_detail, time_of_day, weather,
            setting_details, other_characters, other_characters_detail,
            ui_elements, environment_description, confidence

        Raises LLMError on failure.
        """
        data = self.llm.vision_json(
            images=[frame_path],
            prompt=ENVIRONMENT_PROMPT,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        if not isinstance(data, dict):
            return {
                "location": "unknown",
                "location_detail": "",
                "time_of_day": "unknown",
                "weather": "unknown",
                "setting_details": "",
                "other_characters": "none",
                "other_characters_detail": "",
                "ui_elements": "none",
                "environment_description": "",
                "confidence": 0.0,
            }
        return data

    def _parse_observation(self, data: dict, timestamp: float) -> RawFrameObservation:
        """Parse VLM JSON response into RawFrameObservation with validation."""
        if not isinstance(data, dict):
            return RawFrameObservation(timestamp=timestamp)

        def _as_float(v, default=0.0) -> float:
            try:
                return float(v)
            except (ValueError, TypeError):
                return default

        def _as_list(v) -> list[str]:
            if isinstance(v, list):
                return [str(x) for x in v if x]
            if isinstance(v, str) and v:
                return [v]
            return []

        obs = RawFrameObservation(
            timestamp=timestamp,
            event_type=str(data.get("event_type", "UNKNOWN")).upper().strip(),
            description=str(data.get("description", "")).strip(),
            characters=_as_list(data.get("characters")),
            location=str(data.get("location", "")).strip(),
            actions=_as_list(data.get("actions")),
            activity_level=max(0.0, min(1.0, _as_float(data.get("activity_level")))),
            visual_confidence=max(0.0, min(1.0, _as_float(data.get("visual_confidence")))),
            tags=_as_list(data.get("tags")),
        )
        # Normalize event type
        obs.event_type = obs.normalize_type()
        return obs
