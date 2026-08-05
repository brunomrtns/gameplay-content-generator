"""Generation service — orchestrates the full pipeline for a single video.

Stages: content_planning → [editorial_planning] → [creative_engine] → script
        → [script_review] → tts → gameplay_selection → music_selection
        → render_plan → render → qa → output

The `editorial_planning` stage (NEW) produces a VideoCreativePlan that
decides video type, central idea, narrative beats, tone, humor plan, and
model recommendation. Gated by GPCG_EDITORIAL_PLANNING_ENABLED.

The `creative_engine` stage is optional (gated by GPCG_CREATIVE_ENGINE_ENABLED).
When enabled, a dedicated LLM (default: Qwen3-14B via Ollama) produces
hooks/angles/punchlines/observations that feed into the script generator.
When a VideoCreativePlan is available, the engine respects the plan's
HumorPlan (skipped if humor disabled, style adjusted by intensity).

The `script_review` stage (NEW) runs the ScriptCritic to evaluate the
script. If REVISE and under max_revisions, the script is regenerated with
the critic's feedback. Gated by GPCG_SCRIPT_CRITIC_ENABLED.

Each stage persists progress on the Job. The worker calls this service and
handles retries/auto-repair.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from gpcg.application.clip_usage_service import record_clip_usage, release_clip_usage
from gpcg.application.content_planning_service import ContentPlanningService
from gpcg.application.creative_engine import CreativeEngine, CreativeMaterial
from gpcg.application.editorial_planner import EditorialPlanner
from gpcg.application.gameplay_retriever import GameplayRetriever
from gpcg.application.gameplay_selector import GameplaySelector
from gpcg.application.humanization import Humanizer, HumanizationResult
from gpcg.application.metadata_generator import MetadataGenerator
from gpcg.application.qa_service import QAService, persist_qa_result
from gpcg.application.render_plan_builder import RenderPlanBuilder
from gpcg.application.script_critic import ScriptCritic, CRITIC_VERDICT_REVISE
from gpcg.application.script_service import ScriptService
from gpcg.application.story_finder import StoryFinder
from gpcg.config import get_settings
from gpcg.domain.creative_plan import StoryConcept, VideoCreativePlan
from gpcg.domain.game_repository import find_by_name
from gpcg.domain.models import (
    Automation,
    ContentPlan,
    Fact,
    Game,
    Job,
    JobStage,
    JobStatus,
    JobType,
    Script,
    Video,
    VideoStatus,
)
from gpcg.infrastructure.database import session_scope
from gpcg.infrastructure.google_integration_adapter import (
    GoogleIntegrationAdapter,
)
from gpcg.infrastructure.llm import LLMClient, get_llm
from gpcg.infrastructure.video_generate_adapter import (
    VideoGenerateAdapter,
    VideoGenerateError,
)
from gpcg.logging import get_logger

log = get_logger(__name__)


class GenerationError(Exception):
    """Raised when a pipeline stage fails irrecoverably."""

    def __init__(self, message: str, stage: str = JobStage.render.value):
        super().__init__(message)
        self.stage = stage


class GenerationService:
    """Orchestrates end-to-end video generation."""

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        vg_adapter: Optional[VideoGenerateAdapter] = None,
        creative_engine: Optional[CreativeEngine] = None,
        editorial_planner: Optional[EditorialPlanner] = None,
        script_critic: Optional[ScriptCritic] = None,
        story_finder: Optional[StoryFinder] = None,
        humanizer: Optional[Humanizer] = None,
    ) -> None:
        self.llm = llm
        self.vg_adapter = vg_adapter
        self.settings = get_settings()
        self.selector = GameplaySelector()
        self.plan_builder = RenderPlanBuilder()
        self.qa = QAService(llm=llm)
        # Creative engine is optional and only invoked when enabled in config.
        # A custom instance can be injected (e.g. for tests with a mock LLM).
        self.creative_engine = creative_engine or CreativeEngine(llm=llm)
        # Editorial planner + script critic (new editorial pipeline stages)
        self.editorial_planner = editorial_planner or EditorialPlanner(llm=llm)
        self.script_critic = script_critic or ScriptCritic(llm=llm)
        # V2: Story Finder (transforms fact into story before editorial planning)
        self.story_finder = story_finder or StoryFinder(llm=llm)
        # V2: Humanizer (breaks AI patterns, ensures orality)
        self.humanizer = humanizer or Humanizer(llm=llm)
        # Gameplay retriever (uses semantic index when plan is available)
        self.gameplay_retriever = GameplayRetriever()
        # YouTube upload adapter (google-integration service)
        self.youtube_adapter = GoogleIntegrationAdapter()
        # Metadata generator (LLM-powered social metadata for YouTube uploads)
        self.metadata_generator = MetadataGenerator(llm=llm)

    def create_job(
        self,
        game_name_or_id: str | int,
        *,
        user_id: Optional[int] = None,
        scene_duration: float = 0.0,
        video_format: str = "",
        subtitle_font: str = "",
        subtitle_font_size: int = 0,
        subtitle_color: str = "",
        subtitle_outline_color: str = "",
        subtitle_position: str = "",
        subtitle_case: str = "",
        voice_path: str = "",
        creative_style: str = "",
        transition_type: str = "",
        transition_duration: float = 0.0,
        subtitle_box_enabled: Optional[bool] = None,
        subtitle_box_color: str = "",
        subtitle_box_padding: int = 0,
        subtitle_stroke_color: str = "",
        subtitle_stroke_width: int = 0,
        subtitle_rounded_box: Optional[bool] = None,
    ) -> Job:
        """Create a queued generation job for a game.

        Customization params override config defaults when non-empty/non-zero.
        `creative_style` overrides the CreativeEngine style preset for this job
        (one of CREATIVE_PRESETS). Only used when GPCG_CREATIVE_ENGINE_ENABLED=true.
        """
        with session_scope() as session:
            if isinstance(game_name_or_id, int):
                game = session.get(Game, game_name_or_id)
            else:
                game = find_by_name(session, game_name_or_id)
            if game is None:
                raise ValueError(f"game '{game_name_or_id}' not found")

            artifacts: dict = {}
            if scene_duration > 0:
                artifacts["scene_duration"] = scene_duration
            if video_format:
                artifacts["video_format"] = video_format
            if voice_path:
                artifacts["voice_path"] = voice_path
            if creative_style:
                artifacts["creative_style"] = creative_style
            if transition_type:
                artifacts["transition_type"] = transition_type
            if transition_duration > 0:
                artifacts["transition_duration"] = transition_duration
            sub_cfg = {}
            if subtitle_font:
                sub_cfg["font"] = subtitle_font
            if subtitle_font_size > 0:
                sub_cfg["font_size"] = subtitle_font_size
            if subtitle_color:
                sub_cfg["color"] = subtitle_color
            if subtitle_outline_color:
                sub_cfg["outline_color"] = subtitle_outline_color
            if subtitle_position:
                sub_cfg["position"] = subtitle_position
            if subtitle_case:
                sub_cfg["case_transform"] = subtitle_case
            if subtitle_box_enabled is not None:
                sub_cfg["box_enabled"] = subtitle_box_enabled
            if subtitle_box_color:
                sub_cfg["box_color"] = subtitle_box_color
            if subtitle_box_padding > 0:
                sub_cfg["box_padding"] = subtitle_box_padding
            if subtitle_stroke_color:
                sub_cfg["stroke_color"] = subtitle_stroke_color
            if subtitle_stroke_width > 0:
                sub_cfg["stroke_width"] = subtitle_stroke_width
            if subtitle_rounded_box is not None:
                sub_cfg["rounded_box"] = subtitle_rounded_box
            if sub_cfg:
                artifacts["subtitle_config"] = sub_cfg

            job = Job(
                job_uuid=str(uuid.uuid4()),
                user_id=user_id,
                type=JobType.generate_short.value,
                game_id=game.id,
                status=JobStatus.queued.value,
                stage=JobStage.content_planning.value,
                progress=0.0,
                max_attempts=self.settings.gpcg_max_repair_retries + 1,
                artifacts=artifacts,
            )
            session.add(job)
            session.flush()
            log.info(f"created job #{job.id} (uuid={job.job_uuid}) for game '{game.canonical_name}'")
            session.refresh(job)
            return job

    def create_curiosity_job(
        self,
        background_game_id: int,
        fact_id: Optional[int] = None,
        *,
        user_id: Optional[int] = None,
        scene_duration: float = 0.0,
        video_format: str = "",
        subtitle_font: str = "",
        subtitle_font_size: int = 0,
        subtitle_color: str = "",
        subtitle_outline_color: str = "",
        subtitle_position: str = "",
        subtitle_case: str = "",
        voice_path: str = "",
        creative_style: str = "",
        transition_type: str = "",
        transition_duration: float = 0.0,
        subtitle_box_enabled: Optional[bool] = None,
        subtitle_box_color: str = "",
        subtitle_box_padding: int = 0,
        subtitle_stroke_color: str = "",
        subtitle_stroke_width: int = 0,
        subtitle_rounded_box: Optional[bool] = None,
    ) -> Job:
        """Create a queued curiosity_short job.

        The fact comes from the general pool (game_id=NULL).
        The background_game_id is the game whose gameplay runs in the background.
        If fact_id is omitted, the system auto-picks the best general fact.
        Customization params override config defaults when non-empty/non-zero.
        `creative_style` overrides the CreativeEngine style preset for this job.
        """
        with session_scope() as session:
            bg_game = session.get(Game, background_game_id)
            if bg_game is None:
                raise ValueError(f"background game #{background_game_id} not found")

            artifacts: dict = {
                "background_game_id": background_game_id,
                "general_fact_id": fact_id,
            }
            if scene_duration > 0:
                artifacts["scene_duration"] = scene_duration
            if video_format:
                artifacts["video_format"] = video_format
            if voice_path:
                artifacts["voice_path"] = voice_path
            if creative_style:
                artifacts["creative_style"] = creative_style
            if transition_type:
                artifacts["transition_type"] = transition_type
            if transition_duration > 0:
                artifacts["transition_duration"] = transition_duration
            sub_cfg = {}
            if subtitle_font:
                sub_cfg["font"] = subtitle_font
            if subtitle_font_size > 0:
                sub_cfg["font_size"] = subtitle_font_size
            if subtitle_color:
                sub_cfg["color"] = subtitle_color
            if subtitle_outline_color:
                sub_cfg["outline_color"] = subtitle_outline_color
            if subtitle_position:
                sub_cfg["position"] = subtitle_position
            if subtitle_case:
                sub_cfg["case_transform"] = subtitle_case
            if subtitle_box_enabled is not None:
                sub_cfg["box_enabled"] = subtitle_box_enabled
            if subtitle_box_color:
                sub_cfg["box_color"] = subtitle_box_color
            if subtitle_box_padding > 0:
                sub_cfg["box_padding"] = subtitle_box_padding
            if subtitle_stroke_color:
                sub_cfg["stroke_color"] = subtitle_stroke_color
            if subtitle_stroke_width > 0:
                sub_cfg["stroke_width"] = subtitle_stroke_width
            if subtitle_rounded_box is not None:
                sub_cfg["rounded_box"] = subtitle_rounded_box
            if sub_cfg:
                artifacts["subtitle_config"] = sub_cfg

            job = Job(
                job_uuid=str(uuid.uuid4()),
                user_id=user_id,
                type=JobType.curiosity_short.value,
                game_id=None,
                status=JobStatus.queued.value,
                stage=JobStage.content_planning.value,
                progress=0.0,
                max_attempts=self.settings.gpcg_max_repair_retries + 1,
                artifacts=artifacts,
            )
            session.add(job)
            session.flush()
            log.info(
                f"created curiosity job #{job.id} (uuid={job.job_uuid}) "
                f"bg='{bg_game.canonical_name}' fact_id={fact_id}"
            )
            session.refresh(job)
            return job

    def run_job(self, job_id: int) -> bool:
        """Run a job to completion. Returns True on success, False on failure."""
        with session_scope() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise ValueError(f"job #{job_id} not found")
            if job.status in (JobStatus.completed.value, JobStatus.running.value):
                return job.status == JobStatus.completed.value

            job.status = JobStatus.running.value
            job.started_at = job.started_at or job.updated_at
            session.flush()

        try:
            return self._run_pipeline(job_id)
        except GenerationError as e:
            self._mark_failed(job_id, str(e), e.stage)
            return False
        except Exception as e:
            log.exception(f"unexpected error in job #{job_id}")
            self._mark_failed(job_id, str(e))
            return False

    def _run_pipeline(self, job_id: int) -> bool:
        llm = self.llm or get_llm()
        vg = self.vg_adapter or VideoGenerateAdapter()

        # Determine job type to branch the pipeline
        with session_scope() as session:
            job = session.get(Job, job_id)
            is_curiosity = job.type == JobType.curiosity_short.value
            bg_game_id = job.artifacts.get("background_game_id") if is_curiosity else None
            general_fact_id = job.artifacts.get("general_fact_id") if is_curiosity else None
            # Editorial decision may specify a fact_id for generate_short
            editorial_fact_id = job.artifacts.get("fact_id") if not is_curiosity else None
            editorial_decision = job.artifacts.get("editorial_decision", {})
            # User idea queue: if the job was created from a queued KnowledgeItem,
            # force the content planner to use that specific KI.
            queued_ki_id = job.artifacts.get("queued_knowledge_item_id")

            # ── Load channel profile once and propagate through pipeline ──
            # The ChannelProfile is loaded a single time here and its
            # stage-relevant context is injected into content_planning,
            # story_finding, editorial_planning, and the script stage. Only
            # stage-relevant fields are passed to each stage (via
            # `to_stage_context`) instead of dumping the full profile.
            channel_profile = None
            channel_context = ""
            cp_context_planning = ""
            cp_context_story = ""
            cp_context_editorial = ""
            cp_user_id = job.artifacts.get("user_id") or job.user_id
            if cp_user_id:
                try:
                    from gpcg.domain.models import ChannelProfile
                    channel_profile = session.query(ChannelProfile).filter(
                        ChannelProfile.user_id == cp_user_id
                    ).first()
                    if channel_profile is not None:
                        channel_context = channel_profile.to_prompt_context()
                        cp_context_planning = channel_profile.to_stage_context("content_planning")
                        cp_context_story = channel_profile.to_stage_context("story_finding")
                        cp_context_editorial = channel_profile.to_stage_context("editorial_planning")
                        job.artifacts = {**job.artifacts, "channel_context": channel_context}
                except Exception as e:
                    log.warning(f"Could not load channel profile: {e}")

        # ── Stage: content_planning ─────────────────────────────────────────
        self._set_stage(job_id, JobStage.content_planning)
        with session_scope() as session:
            job = session.get(Job, job_id)
            planner = ContentPlanningService(llm=llm)
            if is_curiosity:
                bg_game = session.get(Game, bg_game_id)
                if bg_game is None:
                    raise GenerationError(
                        f"background game #{bg_game_id} not found",
                        JobStage.content_planning.value,
                    )
                # If the job came from the idea queue, force the KI
                if queued_ki_id:
                    plan = planner.plan_for_knowledge_item(
                        session, queued_ki_id,
                        background_game_id=bg_game_id,
                        user_id=job.user_id,
                        channel_context=cp_context_planning,
                    )
                else:
                    plan = planner.plan_for_general_curiosity(
                        session, bg_game_id, fact_id=general_fact_id,
                        user_id=job.user_id,
                        channel_context=cp_context_planning,
                    )
                if plan is None:
                    raise GenerationError(
                        f"no content plan could be created for curiosity short "
                        "(need scored general facts — upload general documents and extract facts first)",
                        JobStage.content_planning.value,
                    )
            else:
                game = session.get(Game, job.game_id)
                # If the job came from the idea queue with a game-specific KI,
                # force the content planner to use that KI.
                if queued_ki_id:
                    plan = planner.plan_for_knowledge_item(
                        session, queued_ki_id,
                        background_game_id=None,
                        user_id=job.user_id,
                        channel_context=cp_context_planning,
                    )
                else:
                    # Pass editorial fact_id if the editorial strategy picked one,
                    # and recent topics to avoid repetition
                    recent_topics = [
                        r[0] for r in session.execute(
                            select(ContentPlan.topic)
                            .where(ContentPlan.user_id == job.user_id)
                            .order_by(ContentPlan.created_at.desc())
                            .limit(10)
                        ).scalars().all() if r
                    ]
                    plan = planner.plan_for_game(
                        session, job.game_id,
                        fact_id=editorial_fact_id,
                        avoid_topics=recent_topics,
                        user_id=job.user_id,
                        channel_context=cp_context_planning,
                    )
                if plan is None:
                    raise GenerationError(
                        f"no content plan could be created for '{game.canonical_name}' "
                        "(need scored facts — upload documents and extract facts first)",
                        JobStage.content_planning.value,
                    )
            job.content_plan_id = plan.id
            job.artifacts = {**job.artifacts, "content_plan_id": plan.id}
            session.flush()

        # ── Stage: story_finding (V2 — transforms fact into story) ──────────
        story_concept: Optional[StoryConcept] = None
        if self.settings.gpcg_story_finder_enabled:
            self._set_stage(job_id, JobStage.story_finding)
            story_concept = self._run_story_finding(
                job_id, llm=llm, is_curiosity=is_curiosity, bg_game_id=bg_game_id,
                channel_context=cp_context_story,
            )

        # ── Stage: editorial_planning (NEW — produces VideoCreativePlan) ────
        creative_plan: Optional[VideoCreativePlan] = None
        if self.settings.gpcg_editorial_planning_enabled:
            self._set_stage(job_id, JobStage.editorial_planning)
            creative_plan = self._run_editorial_planning(
                job_id, llm=llm, is_curiosity=is_curiosity, bg_game_id=bg_game_id,
                story_concept=story_concept,
                channel_context=cp_context_editorial,
            )

        # ── Stage: creative_engine (optional, Qwen3-14B) ─────────────────────
        # When enabled, generate hooks/angles/punchlines/observations that
        # feed into the script generator. When disabled or when fallback is
        # triggered, the script stage runs with the legacy prompt path.
        # When a VideoCreativePlan is available, the engine respects the
        # plan's HumorPlan (skipped if humor disabled).
        if self.settings.gpcg_creative_engine_enabled:
            self._set_stage(job_id, JobStage.creative_engine)
            creative_material = self._run_creative_engine(
                job_id, llm=llm, creative_plan=creative_plan
            )
        else:
            creative_material = None

        # ── Stage: script ───────────────────────────────────────────────────
        self._set_stage(job_id, JobStage.script)
        with session_scope() as session:
            job = session.get(Job, job_id)

            # ── Channel context (per-channel personalization) ──
            # The channel profile was loaded once at the start of the pipeline
            # and its full prompt context is reused here (avoiding a second DB
            # query). If the early load failed (e.g. profile created
            # mid-pipeline), fall back to loading it now so the script stage
            # still gets channel personalization.
            # NOTE: File-upload knowledge base (RAG) retrieval has been removed.
            # Channel knowledge is now managed via manual ideas (KnowledgeItem
            # with source_type="manual"). Legacy Document/KnowledgeChunk data is
            # preserved but no longer used.
            user_id = job.artifacts.get("user_id") or job.user_id
            if not channel_context and user_id:
                try:
                    from gpcg.domain.models import ChannelProfile
                    profile = session.query(ChannelProfile).filter(
                        ChannelProfile.user_id == user_id
                    ).first()
                    if profile:
                        channel_context = profile.to_prompt_context()
                except Exception as e:
                    log.warning(f"Failed to load channel profile for user {user_id}: {e}")

            svc = ScriptService(llm=llm)
            script = svc.generate_script(
                session, job.content_plan_id,
                creative_material=creative_material,
                creative_plan=creative_plan,
                story_concept=story_concept,
                channel_context=channel_context,
                knowledge_context="",
                user_id=job.user_id,
            )
            if script is None:
                raise GenerationError("script generation failed", JobStage.script.value)
            job.artifacts = {**job.artifacts, "script_id": script.id}
            session.flush()

        # ── Stage: humanization (V2 — break AI patterns, ensure orality) ────
        if self.settings.gpcg_humanization_enabled:
            self._set_stage(job_id, JobStage.humanization)
            self._run_humanization(job_id, llm=llm, creative_plan=creative_plan)

        # ── Stage: script_review (NEW — ScriptCritic evaluates + may revise) ─
        if self.settings.gpcg_script_critic_enabled:
            self._set_stage(job_id, JobStage.script_review)
            script = self._run_script_review(
                job_id, llm=llm, creative_plan=creative_plan
            )

        # ── Stage: tts ──────────────────────────────────────────────────────
        self._set_stage(job_id, JobStage.tts)
        # Unload all Ollama models from VRAM before TTS — XTTS needs the full
        # GPU memory. Ollama keeps models loaded for 5min after use by default,
        # which would cause TTS to fail with OOM on GPUs with limited VRAM.
        try:
            get_llm().unload_all_models()
        except Exception as e:
            log.warning(f"could not unload Ollama models before TTS: {e}")
        script_id = self._get_artifact(job_id, "script_id")
        # Read voice override from job artifacts (absolute path to uploaded voice)
        # NOTE: The voice_path sent by the VPS is an absolute path inside the
        # VPS Docker container (e.g. /app/data/voices/user_2/bruno.wav). On the
        # remote worker (local PC), that path does NOT exist. We resolve it
        # locally by filename, checking the user's isolated dir first, then
        # the shared dir, matching the VPS resolution logic.
        voice_path = self._get_artifact(job_id, "voice_path")
        if voice_path and not Path(voice_path).exists():
            voice_filename = Path(voice_path).name
            # Get user_id from job artifacts (fallback to job.user_id)
            _job_user_id = self._get_artifact(job_id, "user_id")
            if not _job_user_id:
                with session_scope() as session:
                    _job_user_id = session.get(Job, job_id).user_id
            resolved = None
            if _job_user_id:
                user_voice = self.settings.voices_dir / f"user_{_job_user_id}" / voice_filename
                if user_voice.exists():
                    resolved = user_voice
            if not resolved:
                shared_voice = self.settings.voices_dir / voice_filename
                if shared_voice.exists():
                    resolved = shared_voice
            if resolved:
                log.info(f"voice_path resolved locally: {voice_path} → {resolved}")
                voice_path = str(resolved)
            else:
                log.warning(
                    f"voice_path {voice_path} not found locally "
                    f"(checked user_{_job_user_id}/ and shared). "
                    f"TTS will use video-generate default."
                )
                voice_path = ""  # let synthesize_tts use its own fallback
        with session_scope() as session:
            job = session.get(Job, job_id)
            script = session.get(Script, script_id)
            plan = session.get(ContentPlan, script.content_plan_id)

            # REFACTORY_V2: diagnostic checks (warnings, not hard gates)
            # 1. min_chars — log warning if script is very short
            script_chars = len(script.final or "")
            min_chars = self.settings.gpcg_script_min_chars
            if script_chars < min_chars:
                log.warning(
                    f"job #{job_id}: script is {script_chars} chars "
                    f"(below min_chars={min_chars}). This is a diagnostic "
                    f"warning, not a gate — short scripts can be legitimate."
                )
            # 2. target_duration — estimate narration duration and warn if
            # below target * (1 - tolerance). Average narration speed ~150 wpm.
            target_dur = plan.target_duration or self.settings.gpcg_default_target_duration
            tolerance = self.settings.gpcg_target_duration_tolerance
            word_count = len((script.final or "").split())
            estimated_dur = (word_count / 150.0) * 60.0  # 150 wpm → seconds
            acceptable_dur = target_dur * (1.0 - tolerance)
            if estimated_dur < acceptable_dur:
                log.warning(
                    f"job #{job_id}: estimated narration duration {estimated_dur:.1f}s "
                    f"is below target {target_dur:.1f}s * (1 - {tolerance:.0%}) "
                    f"= {acceptable_dur:.1f}s. Words={word_count}. "
                    f"This is a diagnostic warning, not a gate."
                )
            # TTS output path
            tts_dir = self.settings.jobs_dir / f"job_{job_id}"
            tts_dir.mkdir(parents=True, exist_ok=True)
            narration_wav = tts_dir / "narration.wav"
            try:
                tts_result = vg.synthesize_tts(
                    script.final, narration_wav,
                    voice_path=voice_path,
                )
            except VideoGenerateError as e:
                raise GenerationError(f"TTS failed: {e}", JobStage.tts.value)
            job.artifacts = {
                **job.artifacts,
                "narration_wav": str(narration_wav),
                "narration_duration": tts_result.duration_sec,
                "subtitle_mapping": tts_result.subtitle_mapping,
            }
            session.flush()

        # ── Stage: gameplay_selection ───────────────────────────────────────
        self._set_stage(job_id, JobStage.gameplay_selection)
        narration_dur = self._get_artifact(job_id, "narration_duration")
        # Read scene_duration from job artifacts (or config default)
        scene_duration = self._get_artifact(job_id, "scene_duration") or self.settings.gpcg_scene_duration
        with session_scope() as session:
            job = session.get(Job, job_id)
            # For curiosity shorts: select from background_game_id; else: job.game_id
            select_game_id = bg_game_id if is_curiosity else job.game_id
            # Use GameplayRetriever when a creative plan is available
            # (semantic index lookup); falls back to random selection
            video_type = "GENERAL_TOPIC" if is_curiosity else "GAME_RELATED"
            # V2: pass user_id and accept_public for user-scoped selection
            # with public gameplay fallback
            # REFACTORY_V2: fallback_policy = "stop" | "allow_public"
            # (backward compat: accept_public_gameplays boolean still works)
            user_id = job.user_id
            accept_public = False
            if user_id is not None:
                # Check automation config for fallback policy
                auto = session.execute(
                    select(Automation).where(Automation.user_id == user_id)
                ).scalars().first()
                if auto and isinstance(auto.config, dict):
                    # REFACTORY_V2: prefer fallback_policy string, fall back
                    # to legacy accept_public_gameplays boolean
                    fallback_policy = auto.config.get("fallback_policy")
                    if fallback_policy == "allow_public":
                        accept_public = True
                    elif fallback_policy == "stop":
                        accept_public = False
                    else:
                        # Legacy boolean field
                        accept_public = auto.config.get("accept_public_gameplays", False)
                    # V3: Read max_clip_uses from automation config (default=1)
                    max_clip_uses = auto.config.get("max_clip_uses", 1)
                else:
                    max_clip_uses = 1

            # V3: Read gameplay preference + reuse override from job artifacts
            gameplay_preference = job.artifacts.get("gameplay_preference")
            reuse_override = job.artifacts.get("reuse_override")

            # V3: Determine effective max_uses for this job
            # Precedence: override explícito da ideia > configuração do usuário > default
            if reuse_override == "allow_reuse":
                effective_max_uses = max(max_clip_uses, 2)  # at least allow 2
            elif reuse_override == "skip":
                effective_max_uses = max_clip_uses  # strict, no override
            else:
                effective_max_uses = max_clip_uses

            clips = self.gameplay_retriever.retrieve(
                session, select_game_id, target_duration=narration_dur,
                creative_plan=creative_plan,
                scene_duration=scene_duration,
                video_type=video_type,
                user_id=user_id,
                accept_public=accept_public,
                narrative_beats=job.artifacts.get("creative_plan", {}).get("narrative_beats", []),
                max_uses=effective_max_uses,
                gameplay_preference_game_id=gameplay_preference,
            )
            if not clips:
                bg_name = session.get(Game, select_game_id).canonical_name if select_game_id else "N/A"
                raise GenerationError(
                    f"no gameplay assets available for '{bg_name}' — define clips first",
                    JobStage.gameplay_selection.value,
                )
            # Stash clip info (asset ids + ranges + scene_index) for the plan builder
            # V2: also include source_id for clip usage tracking
            # V3: include event_id + selection_reason + usage_count for auditability
            job.artifacts = {
                **job.artifacts,
                "selected_clips": [
                    {
                        "asset_id": c.asset.id,
                        "source_id": c.asset.source_id,
                        "source_path": c.source_path,
                        "start": c.start_sec,
                        "end": c.end_sec,
                        "duration": c.duration,
                        "scene_index": c.scene_index,
                        "event_id": c.event_id,
                        "selection_reason": c.selection_reason,
                        "usage_count_at_selection": c.usage_count_at_selection,
                    }
                    for c in clips
                ],
                "max_uses_configured": max_clip_uses,
                "max_uses_effective": effective_max_uses,
                "reuse_override": reuse_override,
            }
            session.flush()

        # ── Stage: music_selection ──────────────────────────────────────────
        self._set_stage(job_id, JobStage.music_selection)
        with session_scope() as session:
            job = session.get(Job, job_id)
            plan = session.get(ContentPlan, job.content_plan_id)
            try:
                music_path = vg.select_music(plan.music_mood, min_duration=narration_dur)
            except VideoGenerateError as e:
                log.warning(f"music selection failed: {e}")
                music_path = None
            job.artifacts = {**job.artifacts, "music_path": str(music_path) if music_path else None}
            session.flush()

        # ── Stage: render_plan ──────────────────────────────────────────────
        self._set_stage(job_id, JobStage.render_plan)
        music_path_str = self._get_artifact(job_id, "music_path")
        clips_info = self._get_artifact(job_id, "selected_clips")
        subtitle_mapping = self._get_artifact(job_id, "subtitle_mapping")
        # Read video format + subtitle config from job artifacts (or config defaults)
        video_format = self._get_artifact(job_id, "video_format") or self.settings.gpcg_video_format
        sub_cfg_dict = self._get_artifact(job_id, "subtitle_config") or {}
        with session_scope() as session:
            job = session.get(Job, job_id)
            script = session.get(Script, script_id)
            plan = session.get(ContentPlan, job.content_plan_id)
            # Reconstruct SelectedClip-like objects (with scene_index)
            from gpcg.application.gameplay_selector import SelectedClip
            from gpcg.domain.models import GameplayAsset
            from gpcg.domain.video_profiles import SubtitleConfig

            clips = []
            for ci in clips_info:
                asset = session.get(GameplayAsset, ci["asset_id"])
                clips.append(SelectedClip(
                    asset=asset,
                    source_path=ci["source_path"],
                    start_sec=ci["start"],
                    end_sec=ci["end"],
                    duration=ci["duration"],
                    scene_index=ci.get("scene_index", 0),
                ))

            # Build subtitle config from job artifacts + config defaults
            subtitle_config = SubtitleConfig(
                font=sub_cfg_dict.get("font", self.settings.gpcg_subtitle_font),
                font_size=sub_cfg_dict.get("font_size", self.settings.gpcg_subtitle_font_size),
                color=sub_cfg_dict.get("color", self.settings.gpcg_subtitle_color),
                outline_color=sub_cfg_dict.get("outline_color", self.settings.gpcg_subtitle_outline_color),
                position=sub_cfg_dict.get("position", self.settings.gpcg_subtitle_position),
                case_transform=sub_cfg_dict.get("case_transform", self.settings.gpcg_subtitle_case),
                box_enabled=sub_cfg_dict.get("box_enabled"),
                box_color=sub_cfg_dict.get("box_color", ""),
                box_padding=sub_cfg_dict.get("box_padding", 0),
                stroke_color=sub_cfg_dict.get("stroke_color", ""),
                stroke_width=sub_cfg_dict.get("stroke_width", 0),
                rounded_box=sub_cfg_dict.get("rounded_box"),
            )

            rp = self.plan_builder.build(
                session,
                plan,
                script,
                narration_wav=Path(self._get_artifact(job_id, "narration_wav")),
                narration_duration=narration_dur,
                subtitle_mapping=subtitle_mapping,
                selected_clips=clips,
                music_path=Path(music_path_str) if music_path_str else None,
                video_format=video_format,
                subtitle_config=subtitle_config,
            )
            job.artifacts = {**job.artifacts, "batch_id": rp.batch_id, "scene_dir": str(rp.scene_dir)}
            session.flush()

        # ── Stage: render ───────────────────────────────────────────────────
        self._set_stage(job_id, JobStage.render)
        with session_scope() as session:
            job = session.get(Job, job_id)
            plan = session.get(ContentPlan, job.content_plan_id)
            script = session.get(Script, script_id)
            # Use the request_data built by RenderPlanBuilder (contains
            # _gpcg_custom_profile for custom formats + subtitle overrides)
            request_data = rp.request_data
            # Ensure narration path + music are up-to-date
            request_data["audio_principal"] = str(self._get_artifact(job_id, "narration_wav"))
            request_data["musica_fundo"] = music_path_str
            # Pass transition overrides as top-level request_data fields
            # (resolve_video_profile in video-generate applies these as overrides)
            # REFACTORY_V2: fall back to config defaults if not in artifacts
            # (previously video-generate applied its own internal defaults).
            trans_type = self._get_artifact(job_id, "transition_type") or self.settings.gpcg_transition_type
            trans_dur = self._get_artifact(job_id, "transition_duration") or self.settings.gpcg_transition_duration
            if trans_type:
                request_data["transition_type"] = trans_type
            if trans_dur:
                request_data["transition_duration"] = trans_dur
            try:
                render_result = vg.render_video(request_data)
            except VideoGenerateError as e:
                raise GenerationError(f"render failed: {e}", JobStage.render.value)
            if not render_result.success:
                raise GenerationError("render produced no output file", JobStage.render.value)

            # Copy to our videos dir
            dest = self.settings.videos_dir / f"{render_result.batch_id}.mp4"
            shutil.copy2(render_result.video_path, dest)
            # Update artifacts using the SAME session (avoid nested session_scope → SQLite lock)
            job.artifacts = {**job.artifacts, "video_path": str(dest)}
            session.flush()

        # ── Stage: qa ───────────────────────────────────────────────────────
        self._set_stage(job_id, JobStage.qa)
        video_path = Path(self._get_artifact(job_id, "video_path"))
        with session_scope() as session:
            job = session.get(Job, job_id)
            plan = session.get(ContentPlan, job.content_plan_id)
            script = session.get(Script, script_id)
            qa_result = self.qa.evaluate(video_path, script, plan, plan.target_duration)
            # V2: extract knowledge_item_id from plan metadata (if content intelligence used)
            plan_meta = plan.metadata_json or {}
            ki_id = plan_meta.get("knowledge_item_id")
            # Persist Video record
            video = Video(
                user_id=job.user_id,
                job_id=job.id,
                content_plan_id=plan.id,
                game_id=job.game_id,
                file_path=str(video_path),
                status=VideoStatus.pending.value,
                knowledge_item_id=ki_id,  # V2: link video to KnowledgeItem
            )
            session.add(video)
            session.flush()
            persist_qa_result(session, video, qa_result, video_path)
            # V2: Record clip usage to prevent reusing the same gameplay segments
            # REFACTORY_V2: pass consumer_user_id for per-consumer usage history
            selected_clips = job.artifacts.get("selected_clips", [])
            for clip_info in selected_clips:
                source_id = clip_info.get("source_id")
                start = clip_info.get("start", 0.0)
                end = clip_info.get("end", 0.0)
                if source_id and end > start:
                    record_clip_usage(
                        session, video.id, source_id, start, end,
                        consumer_user_id=job.user_id,
                    )
            # Update artifacts using the SAME session (avoid nested session_scope → SQLite lock)
            job.artifacts = {**job.artifacts, "video_id": video.id, "qa_passed": qa_result.passed}
            # Mark KnowledgeItem as used now that Video is persisted.
            # For private KIs: set status=used (only owner consumes).
            # For public KIs: record per-consumer usage (KI stays fresh globally).
            if ki_id:
                try:
                    from gpcg.application.knowledge_item_service import record_usage
                    record_usage(session, ki_id, job.user_id, video_id=video.id)
                except Exception as e:
                    log.warning(f"Failed to record KI usage for #{ki_id}: {e}")
            session.flush()

        # ── Stage: metadata_generation (optional — LLM-generated social metadata)
        qa_passed = self._get_artifact(job_id, "qa_passed")
        if qa_passed and self.settings.gpcg_metadata_generation_enabled:
            self._set_stage(job_id, JobStage.metadata_generation)
            self._run_metadata_generation(job_id)

        # ── Stage: youtube_upload (optional — auto-upload to YouTube) ──────
        if qa_passed and self.settings.gpcg_youtube_upload_enabled:
            self._set_stage(job_id, JobStage.youtube_upload)
            self._run_youtube_upload(job_id)

        # ── Stage: output / done ────────────────────────────────────────────
        if qa_passed:
            self._complete(job_id)
            # Cleanup scene dir
            scene_dir = self._get_artifact(job_id, "scene_dir")
            if scene_dir:
                shutil.rmtree(scene_dir, ignore_errors=True)
            return True
        else:
            # QA failed — caller (worker) decides on auto-repair
            raise GenerationError(
                f"QA failed with score {self._get_qa_score(job_id):.1f}",
                JobStage.qa.value,
            )

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _run_metadata_generation(self, job_id: int) -> None:
        """Generate social metadata (title, description, tags) via LLM.

        Uses the MetadataGenerator to produce YouTube-optimized metadata from
        the content plan + script. Stores the result in job.artifacts so
        _run_youtube_upload can use it. Non-fatal: on failure, the upload
        stage falls back to simple topic/script-based metadata.
        """
        script_id = self._get_artifact(job_id, "script_id")
        if not script_id:
            log.warning(f"metadata_generation: missing script_id for job {job_id}")
            return

        with session_scope() as session:
            job = session.get(Job, job_id)
            script = session.get(Script, script_id)
            plan = session.get(ContentPlan, job.content_plan_id)
            if not script or not plan:
                log.warning(f"metadata_generation: script/plan not found for job {job_id}")
                return

            game = None
            if plan.game_id:
                game = session.get(Game, plan.game_id)

            try:
                model = self.settings.gpcg_metadata_llm_model or None
                metadata = self.metadata_generator.generate(
                    plan, script, game, model=model
                )
                job.artifacts = {
                    **job.artifacts,
                    "social_title": metadata.title,
                    "social_description": metadata.description,
                    "social_tags": metadata.tags,
                }
                log.info(
                    f"metadata_generation: success — title='{metadata.title[:50]}...' "
                    f"tags={len(metadata.tags)}"
                )
            except Exception as e:
                log.warning(f"metadata_generation: failed ({e}), upload will use fallback")
            session.flush()

    def _run_youtube_upload(self, job_id: int) -> None:
        """Upload the generated video to YouTube via google-integration service.

        Reads video path, script, and plan from job artifacts. Builds title,
        description, and tags from the script + plan. Persists the YouTube
        video ID and URL into job.artifacts on success.

        Non-fatal: if the upload fails, the job still completes (the video
        is already rendered and QA-passed). The error is logged and stored
        in artifacts["youtube_upload_error"].
        """
        video_path = self._get_artifact(job_id, "video_path")
        script_id = self._get_artifact(job_id, "script_id")
        if not video_path or not script_id:
            log.warning(f"youtube_upload: missing video_path or script_id for job {job_id}")
            return

        with session_scope() as session:
            job = session.get(Job, job_id)
            script = session.get(Script, script_id)
            plan = session.get(ContentPlan, job.content_plan_id)
            if not script or not plan:
                log.warning(f"youtube_upload: script/plan not found for job {job_id}")
                return

            # Use LLM-generated social metadata if available (from metadata_generation stage),
            # otherwise fall back to simple topic/script-based metadata.
            social_title = self._get_artifact(job_id, "social_title")
            social_description = self._get_artifact(job_id, "social_description")
            social_tags = self._get_artifact(job_id, "social_tags")

            if social_title:
                title = social_title
                description = social_description or script.final[:5000]
                tags = list(social_tags or [])
            else:
                # Fallback: build title from plan topic (truncated to 100 chars)
                title = (plan.topic or "Gameplay Curiosidade")[:100]
                description = script.final[:5000]
                tags: list[str] = []
                if plan.game_id:
                    game = session.get(Game, plan.game_id)
                    if game:
                        tags.append(game.canonical_name.lower())
                # Add hashtags from description
                for word in description.split():
                    if word.startswith("#") and len(word) <= 30:
                        tags.append(word.lstrip("#").lower())

            try:
                # Use per-user YouTube OAuth (user.google_user_id) if available,
                # otherwise fall back to the global config user ID.
                yt_user_id = job.user_id if job.user_id else self.settings.gpcg_youtube_user_id
                result = self.youtube_adapter.upload_to_youtube(
                    video_path,
                    title=title,
                    description=description,
                    tags=tags,
                    user_id=yt_user_id,
                )
            except Exception as e:
                log.error(f"youtube_upload: adapter error: {e}")
                result = type(result).__new__(type(result))
                result.success = False
                result.error = str(e)

            if result.success:
                log.info(
                    f"youtube_upload: success — {result.youtube_video_id} "
                    f"({result.youtube_url})"
                )
                job.artifacts = {
                    **job.artifacts,
                    "youtube_video_id": result.youtube_video_id,
                    "youtube_url": result.youtube_url,
                    "youtube_upload_job_id": result.job_id,
                }
            else:
                log.error(f"youtube_upload: failed — {result.error}")
                job.artifacts = {
                    **job.artifacts,
                    "youtube_upload_error": result.error,
                }
            session.flush()

    def _run_editorial_planning(
        self,
        job_id: int,
        *,
        llm: LLMClient,
        is_curiosity: bool,
        bg_game_id: Optional[int],
        story_concept: Optional[StoryConcept] = None,
        channel_context: str = "",
    ) -> Optional[VideoCreativePlan]:
        """Run the editorial planning stage for a job.

        Produces a VideoCreativePlan that decides video type, central idea,
        narrative beats, tone, humor plan, and model recommendation.
        When a StoryConcept is available (V2), it's passed to the planner so
        the angle becomes the central idea and the frame informs the plan.
        Persists the plan into job.artifacts["creative_plan"].
        """
        with session_scope() as session:
            job = session.get(Job, job_id)
            plan = session.get(ContentPlan, job.content_plan_id)
            if plan is None:
                log.warning(f"editorial_planning: no content plan for job #{job_id}, skipping")
                return None

            job_type = JobType.curiosity_short.value if is_curiosity else JobType.generate_short.value
            creative_plan = self.editorial_planner.plan(
                session, plan, job_type=job_type, background_game_id=bg_game_id,
                story_concept=story_concept,
                channel_context=channel_context,
            )

            if creative_plan.success:
                job.artifacts = {**job.artifacts, "creative_plan": creative_plan.to_dict()}
                session.flush()
                log.info(
                    f"editorial_plan for job #{job_id}: type={creative_plan.video_type} "
                    f"model={creative_plan.model.model} humor={creative_plan.humor.enabled}/{creative_plan.humor.intensity} "
                    f"beats={len(creative_plan.narrative_beats)} latency={creative_plan.latency_ms}ms"
                )
            else:
                log.warning(f"editorial_planning failed for job #{job_id}: {creative_plan.error}")
                job.artifacts = {**job.artifacts, "creative_plan_error": creative_plan.error}
                session.flush()

            return creative_plan

    def _run_story_finding(
        self,
        job_id: int,
        *,
        llm: LLMClient,
        is_curiosity: bool,
        bg_game_id: Optional[int],
        channel_context: str = "",
    ) -> Optional[StoryConcept]:
        """Run the story finding stage for a job (V2).

        Transforms the selected fact into a story by finding the editorial
        angle. If the fact has no story potential (is_story=false or
        confidence below threshold), the pipeline tries the next fact
        candidate. If no candidate yields a story, returns an empty
        StoryConcept (the editorial planner will fall back to the raw fact).

        Persists the concept into job.artifacts["story_concept"].
        """
        with session_scope() as session:
            job = session.get(Job, job_id)
            plan = session.get(ContentPlan, job.content_plan_id)
            if plan is None:
                log.warning(f"story_finding: no content plan for job #{job_id}, skipping")
                return None

            concept = self.story_finder.find_story(
                session, plan, background_game_id=bg_game_id,
                channel_context=channel_context,
            )

            if concept.success:
                job.artifacts = {**job.artifacts, "story_concept": concept.to_dict()}
                session.flush()
                log.info(
                    f"story_concept for job #{job_id}: is_story={concept.is_story} "
                    f"is_insight={concept.is_insight} confidence={concept.confidence:.2f} "
                    f"angle='{concept.angle[:50]}'"
                )
            else:
                log.warning(f"story_finding failed for job #{job_id}: {concept.error}")
                job.artifacts = {**job.artifacts, "story_concept_error": concept.error}
                session.flush()

            return concept

    def _run_humanization(
        self,
        job_id: int,
        *,
        llm: LLMClient,
        creative_plan: Optional[VideoCreativePlan] = None,
    ) -> Optional[HumanizationResult]:
        """Run the humanization stage for a job (V2).

        Takes the final script and applies the humanization pass (regex
        detection + LLM correction). Updates the Script.final in-place with
        the humanized version. Persists the result into
        job.artifacts["humanization"].

        Non-fatal: if humanization fails, the original script is kept.
        """
        with session_scope() as session:
            job = session.get(Job, job_id)
            script_id = job.artifacts.get("script_id")
            if not script_id:
                log.warning(f"humanization: no script for job #{job_id}, skipping")
                return None
            script = session.get(Script, script_id)
            if script is None:
                log.warning(f"humanization: script #{script_id} not found, skipping")
                return None

            original_final = script.final
            result = self.humanizer.humanize(original_final, creative_plan=creative_plan)

            if result.success and result.humanized and result.humanized != original_final:
                # Update the script in-place with the humanized version
                script.final = result.humanized
                # Also update the draft if it's the same (so revisions use the
                # humanized version as the baseline)
                if script.draft == original_final:
                    script.draft = result.humanized
                session.flush()
                log.info(
                    f"humanization for job #{job_id}: {len(result.changes)} changes, "
                    f"{len(result.detected_issues)} issues detected, "
                    f"len {len(original_final)}→{len(result.humanized)}"
                )
            elif result.success and not result.detected_issues:
                log.info(f"humanization for job #{job_id}: no issues detected, no changes")
            else:
                log.warning(f"humanization failed for job #{job_id}: {result.error}")

            job.artifacts = {**job.artifacts, "humanization": result.to_dict()}
            session.flush()

            return result

    def _run_script_review(
        self,
        job_id: int,
        *,
        llm: LLMClient,
        creative_plan: Optional[VideoCreativePlan],
    ) -> Optional[Script]:
        """Run the script critic stage for a job.

        Evaluates the script and may trigger revisions (up to max_revisions).
        Persists the review into job.artifacts["script_reviews"].
        Returns the final Script (possibly revised).
        """
        script_id = self._get_artifact(job_id, "script_id")
        if script_id is None:
            log.warning(f"script_review: no script for job #{job_id}, skipping")
            return None

        reviews: list[dict] = []
        current_script: Optional[Script] = None
        revision_count = 0
        max_revisions = self.settings.gpcg_script_critic_max_revisions

        # Fetch the source fact for factual_accuracy checking
        source_fact = ""
        with session_scope() as session:
            job = session.get(Job, job_id)
            current_script = session.get(Script, script_id)
            if current_script is None:
                return None
            # Get the content plan and fact for factual accuracy checking
            plan = session.get(ContentPlan, job.content_plan_id)
            if plan and plan.fact_id:
                from gpcg.domain.models import Fact
                fact = session.get(Fact, plan.fact_id)
                if fact:
                    source_fact = fact.claim
            # V2: if plan was based on a KnowledgeItem, use its content as source
            if not source_fact and plan:
                plan_meta = plan.metadata_json or {}
                ki_id = plan_meta.get("knowledge_item_id")
                if ki_id:
                    from gpcg.domain.models import KnowledgeItem
                    ki = session.get(KnowledgeItem, ki_id)
                    if ki:
                        source_fact = ki.content[:500]  # truncate for prompt

        while revision_count <= max_revisions:
            # Review the current script (pass source_fact for hallucination detection)
            # V2: use section-based review when enabled
            if getattr(self.settings, "gpcg_script_critic_section_based", False):
                review = self.script_critic.review_sections(
                    current_script.final,
                    creative_plan or VideoCreativePlan(),
                    revision_count=revision_count,
                    source_fact=source_fact,
                )
            else:
                review = self.script_critic.review(
                    current_script.final,
                    creative_plan or VideoCreativePlan(),
                    revision_count=revision_count,
                    source_fact=source_fact,
                )
            reviews.append(review.to_dict())
            log.info(
                f"script_review job #{job_id} rev={revision_count}: "
                f"verdict={review.verdict} score={review.overall_score:.1f}"
            )

            # Check if we should revise
            if not self.script_critic.should_revise(review, revision_count):
                break

            # Revise the script
            revision_count += 1
            log.info(f"script_review job #{job_id}: revising (attempt {revision_count})")
            with session_scope() as session:
                svc = ScriptService(llm=llm)
                revised = svc.generate_script(
                    session, current_script.content_plan_id,
                    creative_plan=creative_plan,
                    critic_feedback=review.feedback,
                    previous_script=current_script.final,
                    user_id=job.user_id,
                )
                if revised is None:
                    log.error(f"script revision failed for job #{job_id}")
                    break
                current_script = revised
                # Update the job's script_id in the SAME session (no nesting)
                job = session.get(Job, job_id)
                job.artifacts = {**job.artifacts, "script_id": revised.id}
                session.flush()

        # Persist all reviews
        with session_scope() as session:
            job = session.get(Job, job_id)
            job.artifacts = {
                **job.artifacts,
                "script_reviews": reviews,
                "script_review_count": len(reviews),
                "script_review_final_verdict": reviews[-1]["verdict"] if reviews else "PASS",
            }
            session.flush()

        # After exhausting max_revisions, proceed with the best script we have.
        # The critic may still say REVISE, but we've done our best — blocking
        # TTS would prevent any video from being generated. Log a warning so
        # quality issues are visible, but continue to TTS.
        final_verdict = reviews[-1]["verdict"] if reviews else "PASS"
        if final_verdict == CRITIC_VERDICT_REVISE:
            log.warning(
                f"script_review: script still flagged as REVISE after "
                f"{max_revisions} attempts (final score="
                f"{reviews[-1].get('overall_score', 0):.1f}). "
                f"Proceeding with best version — quality may be suboptimal."
            )

        return current_script

    def _run_creative_engine(
        self,
        job_id: int,
        *,
        llm: LLMClient,
        creative_plan: Optional[VideoCreativePlan] = None,
    ) -> Optional[CreativeMaterial]:
        """Run the creative engine stage for a job.

        Reads the content plan + fact, resolves the style preset (job-specific
        override or config default), calls CreativeEngine, and persists the
        material into job.artifacts["creative_material"].

        When a VideoCreativePlan is provided, the engine respects the plan's
        HumorPlan (skipped if humor disabled, style adjusted by intensity)
        and uses the plan's recommended model.

        Returns the CreativeMaterial (may be empty if fallback triggered).
        Returns None if the engine is disabled.
        """
        from gpcg.application.creative_engine import get_style
        from gpcg.domain.models import Fact

        with session_scope() as session:
            job = session.get(Job, job_id)
            plan = session.get(ContentPlan, job.content_plan_id)
            if plan is None:
                log.warning(f"creative_engine: no content plan for job #{job_id}, skipping")
                return None

            fact_text = ""
            if plan.fact_id:
                fact = session.get(Fact, plan.fact_id)
                if fact:
                    fact_text = fact.claim

            # Build context line (game name or general curiosity)
            if plan.game is not None:
                context = f"Game: {plan.game.canonical_name}"
            elif plan.background_game is not None:
                context = (
                    f"Curiosidade geral (não sobre o jogo). "
                    f"Gameplay de fundo: {plan.background_game.canonical_name}"
                )
            else:
                context = "Curiosidade geral"

            # Resolve style: job override > config default
            style_name = job.artifacts.get("creative_style") or self.settings.gpcg_creative_engine_style
            style = get_style(style_name)

            # Extract humor plan + model from creative plan if available
            humor_plan = creative_plan.humor if creative_plan and creative_plan.success else None
            model_override = creative_plan.model.model if creative_plan and creative_plan.success else None
            # V2: extract narrative beats + central_idea for beat-oriented generation
            narrative_beats = creative_plan.narrative_beats if creative_plan and creative_plan.success else None
            central_idea = creative_plan.central_idea if creative_plan and creative_plan.success else ""

        # Run the engine (outside the session — it does its own LLM calls)
        # V2: use beat-oriented generation when the flag is on and beats are available
        if self.settings.gpcg_creative_engine_beat_oriented and narrative_beats:
            material = self.creative_engine.generate_beat_oriented_material(
                topic=plan.topic,
                fact=fact_text or plan.hook or plan.topic,
                context=context,
                style=style,
                humor_plan=humor_plan,
                model_override=model_override,
                narrative_beats=narrative_beats,
                central_idea=central_idea,
            )
        else:
            material = self.creative_engine.generate_creative_material(
                topic=plan.topic,
                fact=fact_text or plan.hook or plan.topic,
                context=context,
                style=style,
                humor_plan=humor_plan,
                model_override=model_override,
            )

        # Persist into job artifacts
        with session_scope() as session:
            job = session.get(Job, job_id)
            job.artifacts = {**job.artifacts, "creative_material": material.to_dict()}
            session.flush()

        log.info(f"creative_engine for job #{job_id}: {material.summary()}")
        return material

    def _set_stage(self, job_id: int, stage: JobStage) -> None:
        with session_scope() as session:
            job = session.get(Job, job_id)
            job.stage = stage.value
            # Progress: rough mapping
            stage_order = [
                JobStage.content_planning,
                JobStage.story_finding,
                JobStage.editorial_planning,
                JobStage.creative_engine,
                JobStage.script,
                JobStage.humanization,
                JobStage.script_review,
                JobStage.tts,
                JobStage.gameplay_selection,
                JobStage.music_selection,
                JobStage.render_plan,
                JobStage.render,
                JobStage.qa,
                JobStage.done,
            ]
            idx = stage_order.index(stage) if stage in stage_order else 0
            job.progress = idx / len(stage_order) * 100
            session.flush()
        log.info(f"job #{job_id} → stage={stage.value}")

    def _get_artifact(self, job_id: int, key: str):
        with session_scope() as session:
            job = session.get(Job, job_id)
            return job.artifacts.get(key)

    def _get_qa_score(self, job_id: int) -> float:
        with session_scope() as session:
            job = session.get(Job, job_id)
            vid_id = job.artifacts.get("video_id")
            if vid_id:
                v = session.get(Video, vid_id)
                return v.qa_score if v else 0.0
            return 0.0

    def _complete(self, job_id: int) -> None:
        from datetime import datetime, timezone

        with session_scope() as session:
            job = session.get(Job, job_id)
            job.status = JobStatus.completed.value
            job.stage = JobStage.done.value
            job.progress = 100.0
            job.completed_at = datetime.now(timezone.utc)
            session.flush()
        log.info(f"job #{job_id} completed ✓")

    def _mark_failed(self, job_id: int, error: str, stage: str = "render") -> None:
        with session_scope() as session:
            job = session.get(Job, job_id)
            job.status = JobStatus.failed.value
            job.stage = stage
            job.error = error[:2000]
            # Rollback: if this job consumed a queued KnowledgeItem, re-queue it
            # at the top so the user's intent is preserved.
            queued_ki_id = (job.artifacts or {}).get("queued_knowledge_item_id")
            if queued_ki_id:
                try:
                    from gpcg.domain.models import Automation, KnowledgeItem, KnowledgeItemStatus
                    from sqlalchemy.orm.attributes import flag_modified
                    ki = session.get(KnowledgeItem, queued_ki_id)
                    if ki and ki.status == KnowledgeItemStatus.fresh.value:
                        # Only re-queue if KI is still fresh (not yet used by a successful video)
                        auto = session.query(Automation).filter(
                            Automation.user_id == job.user_id
                        ).first()
                        if auto:
                            cfg = dict(auto.config or {})
                            q = list(cfg.get("idea_queue", []))
                            if queued_ki_id not in q:
                                q.insert(0, queued_ki_id)  # top of queue
                                cfg["idea_queue"] = q
                                auto.config = cfg
                                flag_modified(auto, "config")
                                log.info(f"job #{job_id} failed: re-queued KI #{queued_ki_id} at top of idea queue")
                except Exception as e:
                    log.warning(f"job #{job_id} failed: could not re-queue KI #{queued_ki_id}: {e}")
            session.flush()
        log.error(f"job #{job_id} FAILED at {stage}: {error}")
