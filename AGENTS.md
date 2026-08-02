# AGENTS.md — Gameplay Content Generator

## Build & Run Commands

- **Setup:** `./scripts/dev.sh setup` (creates venv, installs deps, builds frontend)
- **DB init:** `./scripts/dev.sh db` or `.venv/bin/gpcg db-init` (auto-runs on app startup)
- **Dev mode:** `./scripts/dev.sh run` (API :8787 + frontend :5173)
- **Worker (legacy):** `./scripts/dev.sh worker` (inbox watcher + job processor — runs on VPS)
- **Remote Worker (Compute Plane):** `gpcg remote-worker --vps-url <url> --worker-id <id> --api-key <secret>`
  (runs on local PC with GPU, connects to VPS API, processes mapping + generation jobs)
- **Tests:** `.venv/bin/pytest tests/ -q` (198 tests, ~45s)
- **Frontend build:** `cd frontend && npm run build`
- **Frontend typecheck:** `cd frontend && npm run typecheck`
- **Deploy:** `./scripts/deploy.sh` (syncs to VPS, builds Docker, updates nginx)

## Control Plane + Compute Plane Architecture (v0.3.0)

GPCG now uses a split architecture: the **VPS** acts as Control Plane
(web UI, API, DB, job orchestration) and a **local PC with GPU** acts as
Compute Plane (heavy processing: VLM, ASR, FFmpeg, rendering).

### Control Plane (VPS — `src/gpcg/api/worker_routes.py`)
- **Worker registry:** `workers` table tracks worker_id, status, heartbeat,
  GPU/CPU/RAM usage, current activity, capabilities, version.
- **Job queue:** `jobs` table with `worker_id`, `priority`, `required_capabilities`,
  `gameplay_source_id`. Atomic claim via conditional UPDATE.
- **File transfer:** Gameplays uploaded to VPS temp dir → worker downloads via
  token-authenticated streaming → worker confirms checksum → VPS deletes temp.
- **Result sync:** Worker sends mapping events (structured data only, no frames)
  and video files. VPS persists to DB.
- **Auth:** Workers use `X-Worker-Key` header (shared secret via
  `GPCG_WORKER_API_KEY`). NOT BI Identity SSO (which is for human users).
- **Endpoints:** `/api/workers/*` (register, heartbeat, status, list),
  `/api/jobs/claim`, `/api/jobs/{id}/status`, `/api/jobs/{id}/result`,
  `/api/jobs/{id}/data` (fetch all data for generation), `/api/jobs/{id}/sync`
  (sync results back), `/api/gameplays/{id}/download`,
  `/api/gameplays/{id}/confirm-download`, `/api/gameplays/{id}/mapping-result`,
  `/api/gameplays/{id}/events`, `/api/jobs/{id}/upload-video`,
  `/api/gameplays/{id}/create-mapping-job`.

### Compute Plane (Local PC — `src/gpcg/worker/remote_worker.py`)
- **RemoteWorker class:** registers with VPS, sends heartbeats (background
  thread), polls for jobs, downloads gameplays, runs processing, reports results.
- **Mapping jobs:** Downloads gameplay → confirms checksum → runs
  `GameplayAnalyzer` locally (VLM + ASR + merge + interesting score) →
  sends structured events to VPS → saves analysis JSON locally.
- **Generation jobs:** Fetches all data from VPS (`GET /api/jobs/{id}/data`)
  → populates local temp SQLite DB (`local_db_sync.py`) → runs
  `GenerationService` locally (GPU + video-generate subprocess) → uploads
  rendered video → syncs ContentPlan/Script/Video records back to VPS.
- **Local storage:** `/media/bruno/ToshibaHD/gpcg/{gameplays,mapped,renders,outputs}/`
- **CLI:** `gpcg remote-worker` (flags: `--vps-url`, `--worker-id`, `--api-key`,
  `--storage-dir`, `--capabilities`). Also reads from env vars.
- **systemd:** `scripts/gpcg-worker.service` (user service, auto-restart).

### Data Model Changes
- **Worker table:** `worker_id`, `hostname`, `status`, `last_heartbeat`,
  `gpu_name`, `gpu_usage`, `cpu_usage`, `ram_usage`, `current_job_id`,
  `current_activity`, `capabilities` (JSON), `worker_version`, `git_commit`.
- **Job:** Added `worker_id`, `priority` (low/normal/high),
  `required_capabilities` (JSON), `gameplay_source_id`.
- **GameplaySource:** Added `storage_key`, `upload_token`,
  `processing_status` (uploading→uploaded→waiting_worker→downloading→
  downloaded→mapping→mapped→ready→generating→finished→failed),
  `downloaded_at`, `downloaded_by_worker`.
- **Video:** Added `storage_key`, `youtube_url`, `youtube_video_id`.
- **New enums:** `WorkerStatus`, `WorkerCapability`, `JobPriority`,
  `GameplayProcessingStatus`. `JobType.mapping` added.

### Frontend
- **Dashboard:** WorkerStatusCard shows live worker status (online/busy/offline,
  GPU/CPU/RAM usage, current activity, capabilities, heartbeat).
- **Jobs page:** `/jobs` — queue visualization with filters (queued/running/
  completed/failed), progress bars, stage labels, worker assignment.
- **Content page:** Shows `processing_status` badge (uploaded→mapping→ready),
  "Solicitar mapeamento" button to create mapping jobs manually.

## Multi-User Platform (v0.2.0)

GPCG is now a multi-user platform for automated YouTube channel video generation.

- **Auth:** BI Identity SSO (cookie-based). Reads `bi_auth` cookie, validates via Identity Service at `http://bi-api:3300/api/auth/check`. Auth module at `src/gpcg/infrastructure/auth.py` (`get_current_user`, `get_admin_user`, `get_optional_user` deps). Local User is find-or-created by email match. No local login/register routes — login is handled by BI Identity at `/id/login`.
- **Data isolation:** All models have `user_id` column. API routes filter by
  `user.id` from the local User (found/created from BI Identity email). Migration via `_ensure_column()` in `init_db()`.
- **Automation model:** One per user (`Automation` table). Stores all video
  generation config (format, subtitles, transitions, voice, creative style,
  YouTube upload settings) in `config` and `upload_config` JSON fields.
  Routes at `src/gpcg/api/automation_routes.py` (`/api/automation/*`).
- **YouTube OAuth per-user:** `User.google_user_id` links to google-integration
  service. `/api/youtube/connect` returns OAuth URL, `/api/youtube/status`
  checks connection, `/api/youtube/disconnect` revokes.
- **Dashboard:** `/api/dashboard` returns aggregated stats (gameplays, jobs,
  videos, YouTube connection, automation status) for the current user.
- **Gameplay upload:** `POST /api/gameplays/upload` (multipart file upload,
  saves to `data/gameplays/{user_id}/`, creates GameplaySource, triggers probing).
- **Frontend:** React + Vite + Tailwind. Design system from portfolio-v2
  (dark theme, teal accent, glass effects). Pages: login, dashboard, content
  (gameplay upload), automation (config + live preview), videos (gallery),
  admin (user management). Served under `/gpcg/` in production.
- **Deployment:** Docker (single container, Python + built frontend).
  docker-compose.prod.yml on VPS at `/opt/gpcg/`. Nginx reverse proxy via
  trivestia-nginx with `/gpcg/` path prefix. SQLite at `/app/data/gpcg.db`
  (Docker volume `gpcg-data`).

## Architecture Notes

- **video-generate integration:** Always via subprocess (never import directly
  into GPCG's process). Follows the pattern from `videoclip-generator` and
  `trivestia-course-generator`. See `src/gpcg/infrastructure/video_generate_adapter.py`.
- **TTS chunking:** Long texts are chunked with
  `ai_media_core.speech.tts.text_processing.prepare_commercial_chunks` and
  synthesized per-chunk, then merged with FFmpeg. This avoids the known
  `wav_utils.smart_merge_wavs` bug when XTTS produces chunks with mismatched
  sample rates.
- **Subprocess result passing:** Use a `gpcg_bridge` module (JSON sidecar file)
  instead of parsing stdout — VG's prints are noisy.
- **JSON in subprocess scripts:** Pass via temp file + `json.load()`, never
  embed JSON directly in Python source (JSON `null` ≠ Python `None`).
- **probe() on audio-only files:** Returns `MediaInfo` with `width=0`,
  `height=0`, `codec="audio-only"`. Does NOT raise for audio-only WAVs.
- **Anti-plagiarism:** Source documents are THIRD-PARTY content. Three layers
  protect against plagiarism: (1) prompts instruct LLM to rewrite in own words,
  (2) deterministic n-gram overlap check (`src/gpcg/domain/originality.py`)
  compares script vs. sources + fact claims, (3) automatic rewrite if score
  < 70 (up to 3 retries). Metrics persisted on Script record
  (`originality_score`, `originality_report`, `rewrite_count`).
- **Two video formats:** `generate_short` (curiosity about a specific game,
  gameplay of that game in background) and `curiosity_short` (random curiosity
  NOT about a game, any game's gameplay in background). Curiosity shorts use
  `Document.game_id=NULL` / `Fact.game_id=NULL` for the general pool, and
  `ContentPlan.background_game_id` to specify the background game. UI has a
  dedicated "Curiosidades" page.
- **Video customization:** Per-job `scene_duration` (group clips into scenes
  of N seconds; if > target_duration → 1 long scene from random point; if
  gameplay < scene_dur → chain multiple videos), `video_format` (9:16, 16:9,
  1:1, 4:5 — registered as custom profiles in video-generate subprocess via
  `VideoProfileRegistry.register()`), subtitle overrides (font, size, color,
  outline, position, case, box_enabled, box_color, box_padding, stroke_color,
  stroke_width, rounded_box), and transition overrides (transition_type —
  FFmpeg xfade name like "zoomin", "fade", "dissolve"; transition_duration
  in seconds). Settings flow: API params → job.artifacts → GenerationService
  → RenderPlanBuilder → request_data (subtitle overrides in
  `_gpcg_custom_profile`, transition overrides as top-level fields applied
  by `resolve_video_profile` in video-generate) → VideoGenerateAdapter pops
  `_gpcg_custom_profile` and injects registration code into subprocess.
  **Critical:** the profile_registration code must be indented to 12 spaces
  (matching the template's indent) so `textwrap.dedent` works correctly.
  **Video-generate profile overrides:** `resolve_video_profile` in
  `src/profiles/profile_registry.py` applies request_data overrides
  (`transition_type`, `transition_duration`, `subtitle_box_enabled`, etc.)
  on top of the resolved profile, returning a copy (never mutates the
  registered profile). This allows any consumer (GPCG, videoclip-generator)
  to customize transitions/subtitles without building a custom profile.
- **Audio:** Final video = narration (TTS) + background music. Gameplay audio
  is muted (visual only). May be configurable in the future.
- **Voice upload:** TTS uses XTTS voice cloning — needs a reference audio file.
  Voices uploaded via `POST /api/voices/upload` → saved to `data/voices/`.
  Listed via `GET /api/voices`. Selected per-job via `voice=<filename>` param
  (resolved to absolute path, passed to `synthesize_tts(voice_path=...)` which
  overrides config default `GPCG_TTS_VOICE`). video-generate reads the file
  directly from the path (no copying).
- **Creative Engine (Qwen3-14B):** Optional creative layer
  (`src/gpcg/application/creative_engine.py`) gated by
  `GPCG_CREATIVE_ENGINE_ENABLED`. Runs as a new `creative_engine` stage
  between `content_planning` and `script`. Produces hooks/angles/punchlines/
  observations in a SINGLE LLM call (Qwen3-14B via Ollama, model overridable
  via `GPCG_CREATIVE_ENGINE_MODEL`). Material is persisted to
  `job.artifacts["creative_material"]` and fed to `ScriptService` as
  inspiration (anti-plagiarism still runs after). 8 style presets
  (`CREATIVE_PRESETS`): humor, absurd, sarcastic, storytelling, curiosity,
  nostalgia, dark_humor, high_energy. Per-job override via `creative_style`
  param (API/CLI). Fallback: on failure with `GPCG_CREATIVE_ENGINE_FALLBACK=true`
  (default), continues with legacy script path. CLI smoke test:
  `gpcg creative-test -t <topic> -f <fact> -s <style>`. See
  `docs/CREATIVE_ENGINE.md`.
  **Editorial Pipeline integration:** When a `VideoCreativePlan` is available
  (from the EditorialPlanner), the engine respects the plan's `HumorPlan`:
  skipped entirely if `humor.enabled=false`, style adjusted by intensity
  (low → reduced absurdity/sarcasm). Model also overridden by the plan's
  recommendation.
- **Editorial Pipeline (NEW):** Two new pipeline stages gated by
  `GPCG_EDITORIAL_PLANNING_ENABLED` and `GPCG_SCRIPT_CRITIC_ENABLED`.
  - `editorial_planning` stage (between `content_planning` and
    `creative_engine`): `EditorialPlanner` produces a `VideoCreativePlan`
    that decides video_type (GAME_RELATED/GENERAL_TOPIC), central_idea,
    narrative_beats, tone weights, HumorPlan (enabled/intensity/styles/
    frequency), gameplay_strategy, and model_recommendation (gemma3 vs
    qwen3). Persisted to `job.artifacts["creative_plan"]`. The plan orients
    the CreativeEngine (humor), ScriptService (model + plan-oriented
    prompts), and GameplayRetriever (semantic clip selection).
  - `script_review` stage (after `script`): `ScriptCritic` evaluates the
    script across 6 dimensions (structure, naturalness, humor, coherence,
    gameplay, factual_accuracy). If verdict=REVISE and under
    `GPCG_SCRIPT_CRITIC_MAX_REVISIONS` (default 3), the script is regenerated
    with the critic's feedback. **Critical rule:** when the critic flags bad
    humor, the instruction is "REMOVE this passage", NOT "replace with another
    joke". Silence > bad joke. The `factual_accuracy` dimension checks the
    script against the source fact (passed via `source_fact` param) to detect
    invented mechanics. Reviews persisted to `job.artifacts["script_reviews"]`.
    **Defensive parsing:** `dimension_scores` from the LLM is parsed
    defensively (handles malformed values, non-numeric entries, missing keys).
  See `docs/EDITORIAL_PIPELINE.md`.
- **Editorial Architecture V2 (NEW):** Refined editorial pipeline with 5 new
  components, all gated by feature flags (default: off). See
  `docs/EDITORIAL_REFACTOR_PLAN_V2.md` for the full plan. Pipeline stages
  (V2): `content_planning` → `story_finding` → `editorial_planning` →
  `creative_engine` → `script` → `humanization` → `script_review` → ...
  - **Curiosity Scoring** (`src/gpcg/application/curiosity_scorer.py`):
    Scores facts for editorial curiosity potential (0-100) from 5 editorial
    sub-scores (curiosity_gap 0.30, surprise_potential 0.25,
    retention_potential 0.20, familiarity 0.15, insight_quality 0.10) + 1
    technical sub-score (visual_potential, excluded from the weighted mean).
    Based on Loewenstein's information gap theory. Gated by
    `GPCG_CURIOSITY_SCORING_ENABLED`. When on, `content_planning_service`
    ranks facts by curiosity_score instead of quality*novelty. Config:
    `GPCG_CURIOSITY_SCORER_MODEL`, `GPCG_CURIOSITY_SCORER_TEMPERATURE`,
    `GPCG_CURIOSITY_MIN_THRESHOLD` (default 30). DB: `Fact.curiosity_score`
    + `Fact.curiosity_subscores` (JSON). Schema evolution in `init_db()`.
  - **Story Finder** (`src/gpcg/application/story_finder.py`): Transforms a
    fact into a story by finding the editorial ANGLE. Produces a
    `StoryConcept` (9 fields: fact_claim, angle, curiosity_gap,
    narrative_hook, frame, is_insight, is_story, confidence, success/error).
    If `is_story=false` or confidence < threshold, the fact has no narrative
    potential. Gated by `GPCG_STORY_FINDER_ENABLED`. Config:
    `GPCG_STORY_FINDER_MODEL`, `GPCG_STORY_FINDER_MIN_CONFIDENCE` (0.5).
    Stage `story_finding` (between `content_planning` and
    `editorial_planning`). Persisted to `job.artifacts["story_concept"]`.
    The StoryConcept flows into the EditorialPlanner (angle → central_idea)
    and ScriptService (narrative_hook → opening line, frame → presentation).
  - **Beat-oriented Creative Engine** (`creative_engine.py`):
    `generate_beat_oriented_material()` generates material ORIENTED BY
    NARRATIVE BEAT (3 hooks for "hook", 3 angles for "development", 3
    payoffs for "payoff", 3 observations for commentary) instead of generic
    5-of-each. Gated by `GPCG_CREATIVE_ENGINE_BEAT_ORIENTED`. Falls back to
    generic when off or when no beats available. Gate: skipped when
    `humor.enabled=false` (purely informative videos don't need creative
    material).
  - **Humanization Layer** (`src/gpcg/application/humanization.py`):
    Hybrid pass (regex detection + LLM correction) that breaks AI patterns
    and ensures orality. Regex detects: AI-isms ("você não vai acreditar",
    "prepare-se para"), redundancy ("ou seja", "em outras palavras"),
    repetitive structures (3+ sentences starting with same word), uniform
    rhythm, and MISSING identification with ignorance (Curse of Knowledge
    correction — "eu também não sabia"). LLM corrects the detected issues.
    Gated by `GPCG_HUMANIZATION_ENABLED`. Stage `humanization` (between
    `script` and `script_review`). Updates `Script.final` in-place. Config:
    `GPCG_HUMANIZATION_MODEL`, `GPCG_HUMANIZATION_TEMPERATURE` (0.4),
    `GPCG_HUMANIZATION_MAX_TOKENS`. Non-fatal: on failure, original kept.
  - **Section-based Script Critic** (`script_critic.py`):
    `review_sections()` reviews each SECTION of the script (hook,
    development, payoff) separately, producing per-section scores and
    issues. Per-section issues are merged into the top-level issues list
    with location prefixed by section label (e.g. "[hook] first sentence").
    Gated by `GPCG_SCRIPT_CRITIC_SECTION_BASED`. Falls back to holistic
    `review()` when off. `_split_into_sections()` aligns sections with
    narrative beats when available, else splits by sentence groups.
  - **New JobStage values:** `story_finding` (between `content_planning`
    and `editorial_planning`), `humanization` (between `script` and
    `script_review`). Stage order in `_set_stage()` updated.
  - **New config flags:** `GPCG_CURIOSITY_SCORING_ENABLED`,
    `GPCG_CURIOSITY_SCORER_MODEL`, `GPCG_CURIOSITY_SCORER_TEMPERATURE`,
    `GPCG_CURIOSITY_SCORER_MAX_TOKENS`, `GPCG_CURIOSITY_MIN_THRESHOLD`,
    `GPCG_STORY_FINDER_ENABLED`, `GPCG_STORY_FINDER_MODEL`,
    `GPCG_STORY_FINDER_TEMPERATURE`, `GPCG_STORY_FINDER_MAX_TOKENS`,
    `GPCG_STORY_FINDER_MIN_CONFIDENCE`,
    `GPCG_CREATIVE_ENGINE_BEAT_ORIENTED`,
    `GPCG_HUMANIZATION_ENABLED`, `GPCG_HUMANIZATION_MODEL`,
    `GPCG_HUMANIZATION_TEMPERATURE`, `GPCG_HUMANIZATION_MAX_TOKENS`,
    `GPCG_SCRIPT_CRITIC_SECTION_BASED`.
  - **Tests:** `tests/test_curiosity_scoring.py` (9 tests),
    `tests/test_story_finder.py` (11 tests),
    `tests/test_creative_engine.py::TestBeatOrientedCreativeEngine` (7 tests),
    `tests/test_humanization.py` (12 tests),
    `tests/test_script_critic_sections.py` (8 tests). Total: 47 new tests.
    All 245 tests pass.
- **Editorial Planner gameplay_query fallback:** When the LLM doesn't generate
  a `gameplay_query` (leaves it empty) but `gameplay_strategy` is "related" or
  "thematic_match", the planner extracts a keyword from the fact claim
  (`_extract_gameplay_query_from_plan`). Keywords: skate, bike, car, combat,
  weapon, neve, food, etc. This ensures the GameplayRetriever can do semantic
  search even when the LLM ignores the instruction. The fact claim is also
  included in the planner prompt (was missing before, causing empty queries).
- **GameplayIndexService.search_events:** Searches `description`, `transcript`,
  `location`, `tags` (JSON), `actions` (JSON), and `event_type` — not just
  description/transcript/location as before. The cascaded pipeline produces
  rich tags (`on_skate`, `on_bike`, `combat`, `riding`) that are the most
  reliable way to find specific player actions.
- **YouTube Upload (NEW):** After QA passes, the pipeline can auto-upload the
  rendered video to YouTube via the `google-integration` service (sibling repo
  at `../google-integration`). Gated by `GPCG_YOUTUBE_UPLOAD_ENABLED`.
  - Stage `metadata_generation` (between `qa` and `youtube_upload`): when
    `GPCG_METADATA_GENERATION_ENABLED=true` (default), calls
    `MetadataGenerator` (`src/gpcg/application/metadata_generator.py`) which
    uses the LLM (model: `GPCG_METADATA_LLM_MODEL`, default llama3.1:8b) to
    generate an optimized title (<=100 chars), description, and 8-12 tags
    from the content plan + script + game name. Result stored in
    `job.artifacts["social_title"]`, `["social_description"]`,
    `["social_tags"]`. Non-fatal: on failure, falls back to simple
    topic/script-based metadata.
  - Stage `youtube_upload` (between `metadata_generation` and `output`):
    calls `GoogleIntegrationAdapter`
    (`src/gpcg/infrastructure/google_integration_adapter.py`)
    which POSTs to `/api/upload/youtube` on the google-integration service.
  - Title/description/tags from LLM-generated social metadata (if
    metadata_generation ran), otherwise fallback: title from
    `ContentPlan.topic`, description from `Script.final`, tags from game
    name + hashtags + `GPCG_YOUTUBE_DEFAULT_TAGS`.
  - Non-fatal: if upload fails, the job still completes. Error stored in
    `job.artifacts["youtube_upload_error"]`. Success stores
    `youtube_video_id` and `youtube_url`.
  - The google-integration service handles OAuth, BullMQ queueing, retry.
    GPCG just enqueues and polls for completion (timeout 600s).
  - Config: `GPCG_GOOGLE_INTEGRATION_URL`, `GPCG_GOOGLE_INTEGRATION_SECRET`
    (must match `INTERNAL_API_SECRET` in google-integration `.env`),
    `GPCG_YOUTUBE_USER_ID` (4 = brunointegrations), `GPCG_YOUTUBE_PRIVACY`
    (unlisted for testing, public for production), `GPCG_YOUTUBE_CATEGORY_ID`
    (20 = Gaming), `GPCG_METADATA_GENERATION_ENABLED`,
    `GPCG_METADATA_LLM_MODEL`.
- **Gameplay Understanding (NEW):** Separate pipeline from video generation.
  Analyzes gameplay recordings ONCE and builds a semantic index
  (`GameplayEvent` table) that video generation queries later.
  - CLI: `gpcg analyze-gameplay -s <source_id_or_path>` runs the full
    analysis (coarse → adaptive refine → ASR → merge → interesting score).
    Flags: `--no-asr`, `--no-score`, `--save-json/--no-save-json`,
    `--persist/--no-persist`.
  - `GameplayAnalyzer` (`src/gpcg/application/gameplay_analyzer.py`):
    adaptive temporal sampling (coarse pass identifies boundaries, dense
    refinement only in high-activity zones), VLM analysis via
    `VisionAnalyzer` (gemma3:12b), ASR via `ASRTranscriber`
    (faster-whisper large-v3), merge visual+audio, interesting score per
    event. Anti-hallucination: VLM instructed to NEVER invent events;
    ambiguous → `POSSIBLE_` prefix + low confidence.
  - `GameplayIndexService` (`src/gpcg/application/gameplay_index_service.py`):
    persists `EventTimeline` → `GameplayEvent` rows, manages analysis
    status (`AnalysisStatus` enum: pending/analyzing/ready/failed) in
    `GameplaySource.metadata_json`, compatibility flags
    (`game_related`/`general_topic`), reprocessing (config hash check).
  - `GameplayRetriever` (`src/gpcg/application/gameplay_retriever.py`):
    replaces random `GameplaySelector` when a `VideoCreativePlan` with
    `gameplay_strategy="related"` is available. Queries the semantic index
    for events matching the plan's `gameplay_query`, respects compatibility
    flags, supplements with random when semantic clips don't fill target.
    Falls back to `GameplaySelector` when no plan, strategy=background_filler,
    or no analyzed events.
  - **No physical clip files:** Only temporal references (start_time,
    end_time) stored. Clips extracted on-demand during rendering.
  - **Versioned analysis:** `AnalysisConfig.to_hash()` allows reprocessing
    detection when config changes.
  See `docs/GAMEPLAY_ANALYSIS.md`.

- **Cascaded gameplay analysis (YOLO + crop + VLM):** When a game's
  `camera_type` is set (via `gpcg set-camera-type -g <game> -c <type>`),
  the GameplayAnalyzer uses a cascaded pipeline instead of full-frame VLM:
  YOLOv8 detects the player bbox → crop + upscale (Lanczos) + sharpen + CLAHE
  → VLM classifies the player's action on the crop (movement, combat, held
  item) + VLM describes the environment on the full frame → merge into a
  single observation. Camera types: `third_person` (Bully, GTA),
  `first_person` (CS, Doom), `top_down` (LoL, Dota), `isometric` (BG3),
  `fixed` (RE classic), `unknown` (legacy full-frame, default).
  Anti-hallucination: YOLO weapon detections are filtered to only those
  overlapping the player bbox; YOLO context is passed neutrally (not as
  "weapons"); VLM prompt defaults to "no weapon" unless clearly visible.
  CLI: `gpcg analyze-gameplay -s <id> -c third_person --save-crops` (crops
  saved to `data/gameplay_analysis/crops/` for debugging).
  Dependencies: `opencv-python-headless`, `ultralytics` (YOLOv8), `torch`
  (CUDA). GPU recommended (RTX 3060+).
  See `docs/GAMEPLAY_ANALYSIS.md` → "Cascaded Pipeline".

## Game Resolution Layers

- L1 (deterministic): filename candidate vs. alias registry — confidence 0.95
- L2 (prior): capture_source → game historical association — confidence 0.5
- L3 (VLM): gemma3:12b frame analysis — confidence from LLM
- Fallback: parsed candidate as `needs_review` — confidence 0.3

## Key File Paths

- Domain models: `src/gpcg/domain/models.py`
- Creative plan dataclasses: `src/gpcg/domain/creative_plan.py`
- Gameplay event dataclasses: `src/gpcg/domain/gameplay_events.py`
- VG adapter: `src/gpcg/infrastructure/video_generate_adapter.py`
- Generation orchestration: `src/gpcg/application/generation_service.py`
- Editorial planner: `src/gpcg/application/editorial_planner.py`
- Script critic: `src/gpcg/application/script_critic.py`
- Gameplay analyzer: `src/gpcg/application/gameplay_analyzer.py`
- Gameplay index service: `src/gpcg/application/gameplay_index_service.py`
- Gameplay retriever: `src/gpcg/application/gameplay_retriever.py`
- Frame sampler: `src/gpcg/infrastructure/frame_sampler.py`
- Player detector (YOLO): `src/gpcg/infrastructure/player_detector.py`
- Image enhancer (crop+upscale): `src/gpcg/infrastructure/image_enhancer.py`
- Vision analyzer (VLM): `src/gpcg/infrastructure/vision_analyzer.py`
- ASR transcriber (faster-whisper): `src/gpcg/infrastructure/asr_transcriber.py`
- API routes: `src/gpcg/api/routes.py`
- Frontend pages: `frontend/src/pages/`

## Environment

- Python 3.12, venv at `.venv/`
- Node 20+, frontend at `frontend/`
- Ollama at `localhost:11434` (models: llama3.1:8b, gemma3:12b, qwen3:14b, nomic-embed-text)
- faster-whisper (large-v3, cuda/float16) for ASR
- video-generate at `../video-generate` (sibling repo)
- ai-media-core at `../ai-media-core` (sibling repo)
