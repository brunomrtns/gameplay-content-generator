"""Application configuration — pydantic-settings based, reads from .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Project root = parent of src/
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """All runtime configuration. Loaded once, cached."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── General ──────────────────────────────────────────────────────────────
    gpcg_env: str = "development"
    gpcg_host: str = "127.0.0.1"
    gpcg_port: int = 8787
    gpcg_data_dir: str = "./data"
    gpcg_db_path: str = "./data/gpcg.db"

    # ── Authentication (BI Identity SSO) ─────────────────────────────────────
    # BI Identity Service URL (Docker internal network: http://bi-api:3300)
    bi_identity_url: str = "http://bi-api:3300"
    # Admin email — used to seed a local User row linked to BI Identity.
    # Actual admin authorization is determined by BI Identity roles.
    gpcg_admin_email: str = "brunomartinsss@gmail.com"
    # Deprecated: JWT settings (kept for backward compat, no longer used)
    gpcg_jwt_secret: str = "deprecated-sso"
    gpcg_jwt_expiry: int = 7 * 24 * 3600

    # ── Gameplay Inbox ───────────────────────────────────────────────────────
    gameplay_inbox_dir: str = "/media/bruno/ToshibaHD"
    gpcg_inbox_poll_interval: int = 30
    gpcg_inbox_stable_seconds: int = 10
    gpcg_inbox_min_size_mb: int = 5

    # ── Local AI (Ollama) ────────────────────────────────────────────────────
    ollama_host: str = "http://localhost:11434"
    gpcg_llm_model: str = "llama3.1:8b"
    gpcg_vlm_model: str = "gemma3:12b"
    gpcg_llm_timeout: int = 180

    # ── video-generate integration ───────────────────────────────────────────
    video_generate_dir: str = Field(
        default="/home/bruno/Desenvolvimento/brunointegrations/video-generate"
    )
    ai_media_core_dir: str = Field(
        default="/home/bruno/Desenvolvimento/brunointegrations/ai-media-core/src"
    )
    video_generate_python: str = Field(
        default="/home/bruno/Desenvolvimento/brunointegrations/video-generate/.venv/bin/python"
    )
    gpcg_tts_voice: str = "public/voices/bruno.wav"
    gpcg_tts_language: str = "pt"
    gpcg_render_timeout: int = 3600

    # ── Content defaults ─────────────────────────────────────────────────────
    gpcg_default_format: str = "youtube_short"
    gpcg_default_target_duration: int = 60
    gpcg_default_video_profile: str = "reel_9_16"
    gpcg_narration_min_chars: int = 800
    gpcg_narration_max_chars: int = 1000
    gpcg_max_repair_retries: int = 2
    # V2: Minimum acceptable video duration in seconds. Videos shorter than
    # this are flagged as QA failed (indicating a weak script/story).
    gpcg_min_video_duration: int = 45

    # ── Scene + video customization ──────────────────────────────────────────
    # Target duration of each gameplay scene in seconds. If scene_duration >=
    # narration_duration, a single contiguous scene is used. If a gameplay
    # video is shorter than scene_duration, multiple videos are chained.
    # Default 0 = auto (use individual asset durations, legacy behavior).
    gpcg_scene_duration: float = 0.0
    # Video format: "9:16", "16:9", "1:1", "4:5"
    gpcg_video_format: str = "9:16"
    # Subtitle customization (defaults from profile, overridden if non-empty)
    gpcg_subtitle_font: str = ""  # font family name, e.g. "DejaVuSans-Bold"
    gpcg_subtitle_font_size: int = 0  # 0 = auto from profile
    gpcg_subtitle_color: str = ""  # e.g. "white", "yellow"
    gpcg_subtitle_outline_color: str = ""  # e.g. "black"
    gpcg_subtitle_position: str = ""  # "top", "middle", "bottom"
    gpcg_subtitle_case: str = ""  # "upper", "lower", "none"
    # REFACTORY_V2: transition defaults (previously missing — video-generate
    # applied its own internal defaults when artifacts didn't include them).
    gpcg_transition_type: str = "fade"  # FFmpeg xfade name
    gpcg_transition_duration: float = 0.5  # seconds
    # REFACTORY_V2: gameplay clip cooldown — segments within N seconds of a
    # used range are penalized (not blocked) during selection. This prevents
    # always picking adjacent segments. Overlap > tolerance is still blocked.
    gpcg_gameplay_cooldown_sec: float = 30.0

    # ── Anti-plagiarism ──────────────────────────────────────────────────────
    # Max automatic rewrites when originality score < 70 (n-gram overlap check)
    gpcg_max_originality_rewrites: int = 3
    # Originality threshold (0-100). Scripts below this trigger a rewrite.
    gpcg_originality_threshold: float = 70.0
    # N-gram size for overlap detection (5 words = good balance for pt-BR)
    gpcg_originality_ngram_size: int = 5

    # ── Creative Engine (Qwen3-14B) ──────────────────────────────────────────
    # Master switch. When false, the pipeline runs without the creative stage
    # (legacy behavior). When true, an extra `creative_engine` stage runs
    # between content_planning and script, producing hooks/angles/punchlines
    # that feed into the script generator.
    gpcg_creative_engine_enabled: bool = False
    # Ollama model tag for the creative engine. qwen3:14b ships as Q4_K_M GGUF
    # (~9 GB) which fits in 12 GB VRAM alongside the smaller text/vision models.
    gpcg_creative_engine_model: str = "qwen3:14b"
    # Sampling params for the creative engine. Higher temperature = more creative.
    gpcg_creative_engine_temperature: float = 0.85
    gpcg_creative_engine_max_tokens: int = 2048
    # If true, a creative-engine failure logs an error and the pipeline
    # continues without creative material (legacy script path). If false,
    # the failure propagates and the job is marked failed.
    gpcg_creative_engine_fallback: bool = True
    # Default style preset name (see CREATIVE_PRESETS in creative_engine.py).
    # One of: humor, absurd, sarcastic, storytelling, curiosity, nostalgia,
    # dark_humor, high_energy.
    gpcg_creative_engine_style: str = "humor"
    # V2: beat-oriented creative material. When true, the engine generates
    # material oriented by narrative beats (3 hooks for the "hook" beat,
    # 3 angles for "development", 3 payoffs for "payoff", 3 observations
    # for commentary). When false, uses the generic prompt (5 of each).
    # See docs/EDITORIAL_REFACTOR_PLAN_V2.md §3.4, Fase 3.
    gpcg_creative_engine_beat_oriented: bool = False

    # ── Humanization (V2 — break AI patterns, ensure orality) ────────────────
    # NOTE: full config block is defined below (search for "Humanization (V2
    # editorial architecture)"). This section was a duplicate that caused
    # conflicting defaults. Removed in REFACTORY_V2.

    # ── Gameplay Understanding (semantic analysis) ──────────────────────────
    # Master switch for automatic gameplay analysis on ingestion.
    gpcg_gameplay_analysis_enabled: bool = True
    # VLM model for visual analysis (gemma3:12b works as VLM via Ollama).
    # Abstracted behind VisionAnalyzer so this can be swapped (e.g. qwen3-vl).
    gpcg_gameplay_vision_model: str = "gemma3:12b"
    # ASR model for audio transcription (faster-whisper).
    # Options: tiny, base, small, medium, large-v3
    gpcg_gameplay_asr_model: str = "large-v3"
    # Whether to use GPU for ASR (faster-whisper supports CUDA via ctranslate2).
    gpcg_gameplay_asr_device: str = "cuda"
    # Compute type for ASR: float16 (GPU), int8 (CPU), float32 (compat).
    gpcg_gameplay_asr_compute_type: str = "float16"
    # Coarse analysis: segment duration in seconds for the first pass.
    gpcg_gameplay_coarse_segment_sec: float = 30.0
    # Adaptive refinement: frame interval in seconds within high-activity zones.
    gpcg_gameplay_refine_interval_sec: float = 3.0
    # Activity threshold (0-1) above which a zone gets refined.
    gpcg_gameplay_activity_threshold: float = 0.5
    # Activity threshold (0-1) above which a zone gets ultra-refined.
    gpcg_gameplay_high_activity_threshold: float = 0.75
    # Ultra-refine interval in seconds for very dense zones.
    gpcg_gameplay_ultra_refine_interval_sec: float = 1.5
    # Interesting score threshold (0-1) for editorial retrieval.
    gpcg_gameplay_interesting_threshold: float = 0.4
    # Max frames to send to VLM in a single batch call.
    gpcg_gameplay_vlm_batch_size: int = 4
    # Analysis version tag (for reprocessing/versioning).
    gpcg_gameplay_analysis_version: str = "v1"
    # Directory for analysis JSON output (MVP verification).
    gpcg_gameplay_analysis_dir: str = "./data/gameplay_analysis"

    # ── Editorial Pipeline ───────────────────────────────────────────────────
    # Master switch for the editorial planning stage.
    gpcg_editorial_planning_enabled: bool = True
    # Model for serious/informative videos (gemma3:12b, already installed as VLM).
    gpcg_editorial_gemma_model: str = "gemma3:12b"
    # Model for videos with personality/commentary (qwen3:14b).
    gpcg_editorial_qwen_model: str = "qwen3:14b"
    # Temperature for the editorial planner LLM call.
    gpcg_editorial_temperature: float = 0.6
    # Max tokens for the editorial planner.
    gpcg_editorial_max_tokens: int = 2048

    # ── Curiosity Scoring (V2 editorial architecture) ────────────────────────
    # Master switch for curiosity scoring on facts. When false, fact scoring
    # uses only quality_score + novelty_score (legacy behavior). When true,
    # a curiosity_score is computed from 5 editorial sub-scores + 1 technical
    # sub-score and used to rank fact candidates in content planning.
    # See docs/EDITORIAL_REFACTOR_PLAN_V2.md §3.1, §4.2.
    gpcg_curiosity_scoring_enabled: bool = False
    # Model for curiosity scoring (uses default text model if empty).
    gpcg_curiosity_scorer_model: str = ""
    # Temperature for the curiosity scorer LLM call.
    gpcg_curiosity_scorer_temperature: float = 0.3
    # Max tokens for the curiosity scorer.
    gpcg_curiosity_scorer_max_tokens: int = 1024
    # Minimum curiosity_score for a fact to be considered a video candidate.
    # Facts below this threshold are filtered out in content planning.
    gpcg_curiosity_min_threshold: float = 40.0

    # ── Story Finder (V2 editorial architecture) ─────────────────────────────
    # Master switch for the story_finding stage. When false, the pipeline
    # skips story finding and goes straight to editorial_planning (legacy).
    # See docs/EDITORIAL_REFACTOR_PLAN_V2.md §4.1, Fase 2.
    gpcg_story_finder_enabled: bool = False
    # Model for the story finder (uses default text model if empty).
    gpcg_story_finder_model: str = ""
    # Temperature for the story finder LLM call.
    gpcg_story_finder_temperature: float = 0.6
    # Max tokens for the story finder.
    gpcg_story_finder_max_tokens: int = 1024
    # Minimum confidence for a StoryConcept to be accepted. Below this, the
    # pipeline tries the next fact candidate.
    gpcg_story_finder_min_confidence: float = 0.5
    # Max fact candidates to try when is_story=false before giving up.
    gpcg_story_finder_max_attempts: int = 3

    # ── Humanization (V2 editorial architecture) ─────────────────────────────
    # Master switch for the humanization stage. When false, the pipeline
    # skips humanization and uses the script as-is (legacy).
    # See docs/EDITORIAL_REFACTOR_PLAN_V2.md §4.3, Fase 4.
    gpcg_humanization_enabled: bool = False
    # Model for the humanization pass (uses default text model if empty).
    gpcg_humanization_model: str = ""
    # Temperature for the humanization LLM call.
    gpcg_humanization_temperature: float = 0.5
    # Max tokens for the humanization pass.
    gpcg_humanization_max_tokens: int = 2500

    # ── Script Critic (editorial review) ─────────────────────────────────────
    # Master switch for the script review stage.
    gpcg_script_critic_enabled: bool = True
    # Model for the critic (uses default text model if empty).
    gpcg_script_critic_model: str = ""
    # Max revision attempts (regenerate script with critic feedback).
    gpcg_script_critic_max_revisions: int = 3
    # Temperature for the critic LLM call.
    gpcg_script_critic_temperature: float = 0.3
    # Pass threshold (0-100). Below this, script is revised.
    gpcg_script_critic_pass_threshold: float = 70.0
    # V2 critic: new dimensions (hook_strength, retention, pacing, payoff,
    # curiosity, humanity, factual_accuracy) with hard gate on factual_accuracy.
    # When true, uses the V2 critic prompt + dimensions. When false, uses
    # the legacy critic (structure, naturalness, humor, coherence, gameplay,
    # factual_accuracy). See docs/EDITORIAL_REFACTOR_PLAN_V2.md §3.6, Fase 5.
    gpcg_script_critic_v2_enabled: bool = False
    # V2 critic: overall pass threshold (raised from 70 to 75).
    gpcg_script_critic_v2_pass_threshold: float = 75.0
    # V2 critic: per-dimension floor. Any dimension below this triggers REVISE.
    gpcg_script_critic_v2_dimension_floor: float = 50.0
    # V2 critic: hard gate on factual_accuracy (below this = REVISE always).
    gpcg_script_critic_v2_factual_gate: float = 70.0
    # V2: section-based review. When true, the critic reviews each SECTION
    # of the script (hook, development, payoff) separately, producing
    # per-section scores and issues. Falls back to holistic review when off.
    # See docs/EDITORIAL_REFACTOR_PLAN_V2.md §4.5, Fase 5.
    gpcg_script_critic_section_based: bool = False

    # ── Worker (Compute Plane) ───────────────────────────────────────────────
    gpcg_worker_poll_interval: int = 5
    gpcg_worker_concurrency: int = 1
    # Shared secret for worker API authentication. Workers send this as
    # X-Worker-Key header. If empty, worker endpoints are disabled.
    gpcg_worker_api_key: str = ""
    # Seconds without heartbeat before a worker is considered offline.
    gpcg_worker_heartbeat_timeout: int = 30

    # ── V2: Game Enrichment ──────────────────────────────────────────────────
    # Master switch for automatic game enrichment on creation.
    # When true, a game_enrich job is created when a new Game is added.
    # Manual enrichment (POST /api/games/{id}/enrich) works regardless of this flag.
    # See ARCHITECTURE_V2.md §6.5, Fase 2.
    gpcg_game_enrichment_enabled: bool = False
    # User-Agent for Wikidata/Wikipedia HTTP requests (politeness).
    gpcg_enrichment_user_agent: str = "GPCG/2.0 (gameplay-content-generator)"
    # LLM model for lore_summary generation (uses default text model if empty).
    gpcg_enrichment_llm_model: str = ""
    # Max chars of Wikipedia text to send to the LLM for lore generation.
    gpcg_enrichment_max_wiki_chars: int = 4000

    # ── V2: Content Intelligence ─────────────────────────────────────────────
    # Master switch for content intelligence (RSS collection + KnowledgeItems).
    # When true, content_collect jobs are scheduled by /api/automation/check.
    # See ARCHITECTURE_V2.md §7, Fase 3.
    gpcg_content_intelligence_enabled: bool = False
    # RSS feed URL for news collection (Google News search by game name).
    gpcg_rss_feed_url: str = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    # Collection interval in hours (how often to collect RSS for each game).
    gpcg_content_collection_interval_hours: int = 6
    # Minimum editorial_score for a KnowledgeItem to be considered for content.
    gpcg_content_min_editorial_score: int = 50
    # Retention period for news KnowledgeItems in days (items older than this are deleted).
    gpcg_news_retention_days: int = 30

    # ── V2: Public Gameplay Fallback ────────────────────────────────────────
    # When true (per-user via automation config `accept_public_gameplays`),
    # the gameplay selector falls back to public gameplays from other users
    # when the user's own gameplays for a game are exhausted (all clips used).
    # Public gameplays are GameplaySources with is_public=True.
    gpcg_public_gameplay_fallback_enabled: bool = True

    # ── V2: Gameplay Intelligence (cross-game) ───────────────────────────────
    # Master switch for cross-game gameplay expansion.
    # When true, GameplayRetriever expands game_ids by franchise/developer.
    # See ARCHITECTURE_V2.md §8, Fase 4.
    gpcg_cross_game_gameplay_enabled: bool = False

    # ── YouTube Upload (google-integration service) ──────────────────────────
    # Master switch for automatic YouTube upload after QA passes.
    # When true, the pipeline adds a `youtube_upload` stage that calls the
    # google-integration service to upload the rendered video to YouTube.
    gpcg_youtube_upload_enabled: bool = False
    # Base URL of the google-integration service (Fastify API).
    gpcg_google_integration_url: str = "http://localhost:3200"
    # Internal API secret shared with the google-integration service.
    # Must match INTERNAL_API_SECRET in the google-integration .env.
    gpcg_google_integration_secret: str = ""
    # YouTube channel user ID in the google-integration service.
    # User 4 = brunointegrations (admin, has Drive + YouTube scopes).
    gpcg_youtube_user_id: int = 4
    # Privacy status for uploaded videos: public | private | unlisted.
    # Use "unlisted" for testing, "public" for production.
    gpcg_youtube_privacy: str = "unlisted"
    # YouTube category ID. 22 = People & Blogs, 20 = Gaming.
    gpcg_youtube_category_id: int = 20
    # Default tags appended to every upload (in addition to plan-derived tags).
    gpcg_youtube_default_tags: str = "gameplay,curiosidades,gaming"

    # ── Metadata Generation (LLM-powered social metadata) ───────────────────
    # Master switch for LLM-generated title/description/tags before YouTube upload.
    # When true, adds a `metadata_generation` stage between QA and youtube_upload.
    gpcg_metadata_generation_enabled: bool = True
    # LLM model for metadata generation (default: same as script generation).
    gpcg_metadata_llm_model: str = "llama3.1:8b"

    # ── Derived helpers ──────────────────────────────────────────────────────
    @property
    def data_dir(self) -> Path:
        p = Path(self.gpcg_data_dir)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_path(self) -> Path:
        p = Path(self.gpcg_db_path)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def inbox_dir(self) -> Path:
        return Path(self.gameplay_inbox_dir)

    @property
    def videos_dir(self) -> Path:
        p = self.data_dir / "videos"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def docs_dir(self) -> Path:
        p = self.data_dir / "docs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def uploads_dir(self) -> Path:
        p = self.data_dir / "uploads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def temp_uploads_dir(self) -> Path:
        """Temporary storage for gameplay uploads on VPS.

        Files here are deleted after a worker confirms download (checksum OK).
        This is the only place the VPS stores heavy media files — and only
        transiently. All permanent storage is on the worker's local disk.
        """
        p = self.data_dir / "temp_uploads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def jobs_dir(self) -> Path:
        p = self.data_dir / "jobs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def voices_dir(self) -> Path:
        """Directory for uploaded TTS voice reference files (.wav/.mp3)."""
        p = self.data_dir / "voices"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def gameplay_analysis_dir(self) -> Path:
        p = Path(self.gpcg_gameplay_analysis_dir)
        if not p.is_absolute():
            p = self.data_dir / "gameplay_analysis"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def video_generate_root(self) -> Path:
        return Path(self.video_generate_dir)

    @property
    def video_generate_venv_python(self) -> Path:
        return Path(self.video_generate_python)

    @property
    def ai_media_core_src(self) -> Path:
        return Path(self.ai_media_core_dir)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
