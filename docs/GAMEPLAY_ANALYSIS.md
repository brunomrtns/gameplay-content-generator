# Gameplay Analysis — Automatic Semantic Understanding

The Gameplay Analysis pipeline analyzes gameplay recordings ONCE and builds a
semantic index of events that video generation can query later. This separates
the expensive analysis (VLM + ASR) from video generation, making the per-video
cost much lower.

## Overview

```
Gameplay Recording (MP4)
        ↓
GameplayAnalyzer.analyze()
        ↓
  ┌─────────────────────────────────────────────┐
  │ 1. COARSE PASS                               │
  │    1 frame per 30s segment → VLM → boundaries│
  │                                              │
  │ 2. ADAPTIVE REFINEMENT                       │
  │    Dense sampling only in high-activity zones│
  │    (boundaries + activity >= threshold)      │
  │                                              │
  │ 3. AUDIO/ASR                                 │
  │    faster-whisper transcribes audio track    │
  │                                              │
  │ 4. MERGE                                     │
  │    Visual events + transcript → final events │
  │                                              │
  │ 5. INTERESTING SCORE                         │
  │    VLM rates editorial usefulness (0-1)      │
  └─────────────────────────────────────────────┘
        ↓
EventTimeline (70+ events)
        ↓
GameplayIndexService.persist_timeline()
        ↓
GameplayEvent table (semantic index)
```

## CLI Usage

```bash
# Analyze a gameplay source by DB ID
gpcg analyze-gameplay -s 13

# Analyze a file directly (no DB persistence)
gpcg analyze-gameplay -s /path/to/gameplay.mp4 --no-persist

# Fast visual-only pass (no ASR, no interesting scoring)
gpcg analyze-gameplay -s 13 --no-asr --no-score

# Save analysis JSON without persisting to DB
gpcg analyze-gameplay -s 13 --no-persist --save-json

# Force cascaded pipeline (YOLO + crop + VLM) for a specific run
gpcg analyze-gameplay -s 13 -c third_person

# Save player crops for debugging (saved to data/gameplay_analysis/crops/)
gpcg analyze-gameplay -s 13 -c third_person --save-crops
```

### Setting Camera Type per Game

The cascaded pipeline requires knowing the game's camera perspective. Set it
once per game (stored in the `games.camera_type` column):

```bash
# Set camera type for a game (by name or ID)
gpcg set-camera-type -g Bully -c third_person
gpcg set-camera-type -g "Call of Duty" -c first_person
gpcg set-camera-type -g "League of Legends" -c top_down
gpcg set-camera-type -g "Baldur's Gate 3" -c isometric

# Reset to legacy full-frame analysis
gpcg set-camera-type -g Bully -c unknown
```

Once set, `gpcg analyze-gameplay` automatically uses the cascaded pipeline
for that game's sources. No need to pass `-c` every time.

## Cascaded Pipeline (YOLO + Crop + VLM)

When `camera_type` is set (not "unknown"), the analyzer uses a **cascaded
pipeline** instead of analyzing the full frame directly. This dramatically
improves accuracy for third-person games where the player character is small
in the frame.

### The Problem

In third-person games (Bully, GTA, Spider-Man), the camera is far from the
player. A 1920x1080 frame contains the entire scene, but the player might
only be 100x200 pixels — too small for the VLM to identify what the player
is doing (on foot? on bike? armed? fighting?). The VLM ends up describing
the environment ("a room with furniture") instead of the player's action.

### The Solution: Cascade Detection

Inspired by plate-recognition pipelines (detect car → crop → detect plate →
crop → OCR), the cascaded pipeline uses multiple stages:

```
Full Frame (1920x1080)
        ↓
  ┌─────────────────────────────────────────────────────┐
  │ STAGE 1: YOLO Object Detection                       │
  │   Detect all persons, vehicles, weapons in frame     │
  │   Select the most likely player (largest + most      │
  │   central-bottom for third_person games)             │
  │   Filter weapons to those overlapping the player     │
  └─────────────────────────────────────────────────────┘
        ↓
  ┌─────────────────────────────────────────────────────┐
  │ STAGE 2a: Crop + Enhance (player region)             │
  │   Crop bbox + 30% padding                            │
  │   Upscale to min 640px (Lanczos)                     │
  │   Sharpen (unsharp mask)                             │
  │   CLAHE contrast enhancement                         │
  └─────────────────────────────────────────────────────┘
        ↓
  ┌─────────────────────────────────────────────────────┐
  │ STAGE 2b: VLM Player Action (on crop)                │
  │   "What is the player doing?"                        │
  │   → movement (on_foot, on_bike, on_skate, on_vehicle)│
  │   → combat_state (fighting, armed, none)             │
  │   → held_item (weapon, ball, none)                   │
  │   → posture, interaction                             │
  │   Anti-hallucination: only report weapons CLEARLY    │
  │   visible in the player's hand                       │
  └─────────────────────────────────────────────────────┘
        ↓
  ┌─────────────────────────────────────────────────────┐
  │ STAGE 2c: VLM Environment (on full frame)            │
  │   "Describe the setting"                             │
  │   → location (school, street, indoor)                │
  │   → time_of_day, weather                             │
  │   → other_characters, ui_elements                    │
  └─────────────────────────────────────────────────────┘
        ↓
  ┌─────────────────────────────────────────────────────┐
  │ MERGE: Combine player action + environment           │
  │   → event_type inferred from player state            │
  │   → description = player action + environment        │
  │   → activity_level from combat/movement              │
  │   → tags include movement, combat, location          │
  └─────────────────────────────────────────────────────┘
        ↓
RawFrameObservation (much more accurate than full-frame VLM)
```

### Camera-Type Strategies

| Camera Type | Strategy | Example Games |
|-------------|----------|---------------|
| `third_person` | YOLO detects person, selects most central-bottom, crop + upscale | Bully, GTA, Spider-Man, God of War |
| `first_person` | No player visible. Crop lower third for weapons/hands | CS, Doom, Call of Duty, Minecraft |
| `top_down` | YOLO detects small centered person, crop + upscale | LoL, Dota 2, Diablo |
| `isometric` | Same as top_down | Baldur's Gate 3, Tactics Ogre |
| `fixed` | YOLO detects person anywhere in frame | Resident Evil (classic), point-and-click |
| `unknown` | Legacy full-frame VLM (no cascade) | (default — not yet configured) |

### Why YOLO + VLM (not VLM alone)?

YOLO is fast and reliable for **localization** (where is the person?) but
its class taxonomy (COCO: 80 classes) doesn't cover game-specific objects
(go-karts, skateboards in stylized art, etc.). The VLM is excellent at
**classification** in context ("that's a go-kart") but fails when the target
is small. The cascade combines both: YOLO finds the player → crop amplifies
→ VLM classifies with context.

### Anti-Hallucination in the Cascade

The cascaded pipeline has multiple anti-hallucination safeguards:

1. **YOLO weapon filtering**: Only weapons that **overlap** the player bbox
   are passed to the VLM. A "baseball bat" detected elsewhere in the frame
   is NOT the player's weapon.
2. **Neutral YOLO context**: YOLO detections are passed as "objects detected
   near player" (not "weapons"), avoiding suggestion.
3. **VLM anti-hallucination prompt**: The VLM is instructed to only report
   weapons CLEARLY visible in the player's hand, and to default to "none"
   when unsure.
4. **Confidence tracking**: Low-confidence detections are preserved and can
   be filtered downstream.

### Performance

The cascaded pipeline is slower than legacy (2× VLM calls per frame + YOLO),
but analysis runs **once per gameplay recording**, not per generated video.
A 22-minute Bully recording takes ~15-20 minutes to analyze with the cascade
(vs ~8-10 minutes legacy). The result is stored in the semantic index and
reused for all future video generations.

### Debugging

Use `--save-crops` to save the player crops to `data/gameplay_analysis/crops/`.
Each crop is named `crop_<timestamp>.jpg` and shows exactly what the VLM saw.
This is invaluable for validating the player detection and tuning the
enhancement pipeline.

## Key Principles

### Adaptive, NOT Fixed-Interval Sampling

The coarse pass samples 1 frame per 30-second segment. Only segments with
high activity or detected boundaries get refined with denser sampling. This
avoids wasting VLM calls on static menus/loading screens.

### Anti-Hallucination

The VLM is instructed to NEVER invent events it cannot see. Ambiguous
observations get the `POSSIBLE_` prefix and low confidence:

- `COMBAT` (conf=0.9) — clearly a fight
- `POSSIBLE_COMBAT` (conf=0.4) — might be a fight, unclear
- `UNKNOWN` (conf=0.2) — no idea what's happening

### visual_confidence ≠ interesting_score

These are SEPARATE dimensions:

- `visual_confidence` — how sure the VLM is about what it sees
- `interesting_score` — how useful this moment is for video editing

A high-confidence boring event (walking) gets `visual_confidence=0.9` but
`interesting_score=0.1`. A high-confidence exciting event (combat) gets both
high.

### No Physical Clip Files

The analysis stores only temporal references (`start_time`, `end_time`).
The actual video segments are extracted on-demand during rendering. This
avoids duplicating video data and makes re-analysis cheap.

### Versioned Analysis

`AnalysisConfig.to_hash()` produces a stable hash of the analysis parameters.
When the config changes (different model, different segment size), the
`GameplayIndexService.needs_reprocessing()` method detects the mismatch and
the source can be re-analyzed.

## Architecture

### Domain Models

- `GameplayEvent` (SQLAlchemy, `src/gpcg/domain/models.py`): persisted event
  with `start_time`, `end_time`, `event_type`, `description`, `characters`,
  `location`, `actions`, `tags`, `transcript`, `visual_confidence`,
  `interesting_score`, `analysis_version`, `metadata_json`.
- `AnalysisStatus` enum: `pending`, `analyzing`, `ready`, `failed`.
- `GameplaySource.metadata_json`: stores analysis status, config hash,
  compatibility flags.

### Dataclasses (`src/gpcg/domain/gameplay_events.py`)

In-flight representations during analysis:

- `RawFrameObservation` — VLM output for a single frame
- `CoarseSegment` — result of the first (low-res) pass
- `RefinedEvent` — result of adaptive refinement
- `AudioSegment` — ASR transcript segment with timestamps
- `GameplayEventRecord` — final merged event (maps to ORM)
- `EventTimeline` — ordered collection of events for a source
- `AnalysisConfig` — parameters controlling the analysis

### Infrastructure

- `FrameSampler` (`src/gpcg/infrastructure/frame_sampler.py`): FFmpeg-based
  frame extraction (coarse + dense modes) and audio extraction for ASR.
- `VisionAnalyzer` (`src/gpcg/infrastructure/vision_analyzer.py`): VLM
  abstraction with gameplay-specific prompts. Default: gemma3:12b.
- `ASRTranscriber` (`src/gpcg/infrastructure/asr_transcriber.py`):
  faster-whisper wrapper with lazy model loading and graceful fallback.

### Application

- `GameplayAnalyzer` (`src/gpcg/application/gameplay_analyzer.py`):
  orchestrates the 5-pass pipeline. Dependencies are injectable for testing.
- `GameplayIndexService` (`src/gpcg/application/gameplay_index_service.py`):
  CRUD + queries for the semantic index. Manages analysis status,
  compatibility flags, reprocessing detection.

## Event Types

Canonical taxonomy (uppercase):

| Type | Description |
|------|-------------|
| `COMBAT` | Fighting, battles, aggressive encounters |
| `CHASE` | Pursuit, running from/toward something |
| `DIALOGUE` | Conversation, talking to NPCs |
| `CUTSCENE` | Non-interactive cinematic |
| `EXPLORATION` | Looking around, discovering |
| `VEHICLE` | Driving, riding, piloting |
| `MENU` | In-game menu, UI, pause |
| `LOADING` | Loading screen |
| `PUZZLE` | Solving a puzzle |
| `STEALTH` | Sneaking, avoiding detection |
| `INTERACTION` | Interacting with objects/environment |
| `MINIGAME` | Mini-game within the game |
| `TRAVEL` | Moving from A to B |
| `IDLE` | Nothing happening, static |
| `UNKNOWN` | VLM cannot determine what's happening |

Ambiguous events use the `POSSIBLE_` prefix (e.g. `POSSIBLE_COMBAT`).

## Configuration

See `.env.example` for all `GPCG_GAMEPLAY_*` config keys. Key settings:

| Key | Default | Description |
|-----|---------|-------------|
| `GPCG_GAMEPLAY_ANALYSIS_ENABLED` | true | Master switch |
| `GPCG_GAMEPLAY_VISION_MODEL` | gemma3:12b | VLM for frame analysis |
| `GPCG_GAMEPLAY_ASR_MODEL` | large-v3 | faster-whisper model size |
| `GPCG_GAMEPLAY_COARSE_SEGMENT_SEC` | 30.0 | Coarse pass segment duration |
| `GPCG_GAMEPLAY_REFINE_INTERVAL_SEC` | 3.0 | Refinement frame interval |
| `GPCG_GAMEPLAY_ACTIVITY_THRESHOLD` | 0.5 | Activity level to trigger refinement |
| `GPCG_GAMEPLAY_INTERESTING_THRESHOLD` | 0.4 | Min interesting score for "interesting" events |

## MVP Results

Tested on a 22.6-minute Bully gameplay recording (1920x1080, 30fps):

- **45 coarse segments** (1 per 30s)
- **31 segments refined** (adaptive — only high-activity/boundary zones)
- **70 granular events** detected
- **~20 minutes** total analysis time (visual-only, no ASR)
- **Event types found**: COMBAT, CHASE, DIALOGUE, EXPLORATION, TRAVEL,
  VEHICLE, INTERACTION, IDLE
- **High confidence** (0.80-0.90) on most events
- **Rich descriptions**: "man in blue jacket aggressively approaching a man
  on a bicycle", "player character is tagging a wall with graffiti",
  "character is climbing a tree"

## Testing

```bash
# Run gameplay analyzer tests (29 tests, uses mocks — no Ollama needed)
.venv/bin/pytest tests/test_gameplay_analyzer.py -q

# Run gameplay retriever tests (7 tests, uses fresh DB)
.venv/bin/pytest tests/test_gameplay_retriever.py -q
```

Tests A-F cover:
- A: rapid events (walk → dialogue → combat → chase → flee)
- B: long stable periods (no redundant events)
- C: abrupt change (boundary detection)
- D: dialogue with transcript (vision + ASR merge)
- E: ambiguous gameplay (UNKNOWN, low confidence, no invented events)
- F: long gameplay (batch processing)
