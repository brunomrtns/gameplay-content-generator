"""Kids domain generation pipeline.

Implements the full Kids video generation pipeline:
    Topic → ContentPlan → Script → TTS → ImageSelection → Render → Publish

Key differences from Games GenerationService:
- Uses KidsTopic instead of Game
- Uses StoryAsset (images) instead of GameplaySource (video clips)
- Uses Kids-specific prompts (kid-friendly language, educational tone)
- No gameplay analysis, no VLM, no events, no semantic clip selection
- Images are converted to video clips with Ken Burns effect for rendering

The pipeline shares infrastructure with Games (TTS, music, render, QA)
but uses its own domain logic for content planning, scripting, and visual
selection.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.core.models import (
    ContentPlan,
    Fact,
    Job,
    JobStage,
    JobStatus,
    Script,
    ScriptStatus,
    Video,
    VideoStatus,
    ChannelProfile,
)
from gpcg.domains.kids.models import KidsTopic, StoryAsset, AssetProcessingStatus
from gpcg.domains.kids.prompts import (
    DRAFT_SYSTEM,
    PLAN_DRAFT_SYSTEM,
    OPTIMIZE_SYSTEM,
    REWRITE_SYSTEM,
    CONTENT_PLANNING_SYSTEM,
    METADATA_SYSTEM,
)
from gpcg.infrastructure.llm import LLMClient, LLMError, get_llm
from gpcg.infrastructure.video_generate_adapter import VideoGenerateAdapter, VideoGenerateError
from gpcg.logging import get_logger

log = get_logger(__name__)


class KidsGenerationError(Exception):
    """Raised when a Kids pipeline stage fails."""

    def __init__(self, message: str, stage: str = JobStage.render.value):
        super().__init__(message)
        self.stage = stage


class KidsGenerationService:
    """Orchestrates end-to-end Kids video generation.

    This is the Kids domain's pipeline, separate from Games' GenerationService.
    The domain registry dispatches to this class when domain == "kids".
    """

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        vg_adapter: Optional[VideoGenerateAdapter] = None,
        session_scope=None,
    ) -> None:
        self.llm = llm
        self.vg_adapter = vg_adapter
        self.settings = get_settings()
        self._session_scope = session_scope
        self.plan_builder = None  # Lazy init — uses RenderPlanBuilder from Games infra

    def run_job(self, job_id: int) -> bool:
        """Run a Kids generation job to completion."""
        with self._session_scope() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise ValueError(f"job #{job_id} not found")
            if job.status in (JobStatus.completed.value, JobStatus.running.value):
                return job.status == JobStatus.completed.value

            job.status = JobStatus.running.value
            session.flush()

        try:
            return self._run_pipeline(job_id)
        except KidsGenerationError as e:
            self._mark_failed(job_id, str(e), e.stage)
            return False
        except Exception as e:
            log.exception(f"unexpected error in Kids job #{job_id}")
            self._mark_failed(job_id, str(e))
            return False

    def _run_pipeline(self, job_id: int) -> bool:
        """The Kids pipeline: planning → script → TTS → images → render → QA."""
        llm = self.llm or get_llm()
        vg = self.vg_adapter or VideoGenerateAdapter()

        # ── Load job + topic + channel context ──────────────────────────────
        with self._session_scope() as session:
            job = session.get(Job, job_id)
            topic_id = (job.artifacts or {}).get("topic_id")
            if not topic_id:
                raise KidsGenerationError(
                    "Kids job missing topic_id in artifacts",
                    JobStage.content_planning.value,
                )

            topic = session.get(KidsTopic, topic_id)
            if not topic:
                raise KidsGenerationError(
                    f"KidsTopic #{topic_id} not found",
                    JobStage.content_planning.value,
                )

            # Load channel profile for context
            channel_context = ""
            profile = session.query(ChannelProfile).filter(
                ChannelProfile.user_id == job.user_id
            ).first()
            if profile:
                channel_context = profile.to_prompt_context()

            # Load facts for this topic (if any — from uploaded documents)
            facts = session.query(Fact).filter(
                Fact.user_id == job.user_id,
            ).order_by(Fact.quality_score.desc()).limit(20).all()

            # Load story assets for this topic
            assets = session.query(StoryAsset).filter(
                StoryAsset.topic_id == topic.id,
                StoryAsset.processing_status == AssetProcessingStatus.ready.value,
            ).all()

            topic_title = topic.title
            topic_description = topic.description
            topic_age_range = topic.age_range
            topic_category = topic.category
            fact_claims = [f.claim for f in facts[:5]]  # top 5 facts
            asset_count = len(assets)

        if asset_count == 0:
            raise KidsGenerationError(
                f"no story assets available for topic '{topic_title}' — upload images first",
                JobStage.visual_selection.value,
            )

        log.info(
            f"Kids job #{job_id}: topic='{topic_title}', "
            f"facts={len(fact_claims)}, assets={asset_count}"
        )

        # ── Stage: content_planning ─────────────────────────────────────────
        self._set_stage(job_id, JobStage.content_planning)
        with self._session_scope() as session:
            job = session.get(Job, job_id)
            plan = self._create_content_plan(
                llm, session, job, topic_title, topic_description,
                topic_age_range, topic_category, fact_claims, channel_context,
            )
            job.content_plan_id = plan.id
            job.artifacts = {**job.artifacts, "content_plan_id": plan.id}
            session.flush()

        # ── Stage: script ───────────────────────────────────────────────────
        self._set_stage(job_id, JobStage.script)
        with self._session_scope() as session:
            job = session.get(Job, job_id)
            plan = session.get(ContentPlan, job.content_plan_id)
            script = self._generate_script(llm, session, plan, topic_description, channel_context)
            job.artifacts = {**job.artifacts, "script_id": script.id}
            script_id = script.id
            session.flush()

        # ── Stage: tts ──────────────────────────────────────────────────────
        self._set_stage(job_id, JobStage.tts)
        try:
            get_llm().unload_all_models()
        except Exception:
            pass

        voice_path = (self._get_artifact(job_id, "voice_path") or "")
        if voice_path and not Path(voice_path).exists():
            voice_path = ""  # let TTS use default

        with self._session_scope() as session:
            job = session.get(Job, job_id)
            script = session.get(Script, script_id)
            plan = session.get(ContentPlan, script.content_plan_id)

            tts_dir = self.settings.jobs_dir / f"job_{job_id}"
            tts_dir.mkdir(parents=True, exist_ok=True)
            narration_wav = tts_dir / "narration.wav"

            try:
                tts_result = vg.synthesize_tts(
                    script.final, narration_wav,
                    voice_path=voice_path,
                )
            except VideoGenerateError as e:
                raise KidsGenerationError(f"TTS failed: {e}", JobStage.tts.value)

            job.artifacts = {
                **job.artifacts,
                "narration_wav": str(narration_wav),
                "narration_duration": tts_result.duration_sec,
                "subtitle_mapping": tts_result.subtitle_mapping,
            }
            narration_duration = tts_result.duration_sec
            subtitle_mapping = tts_result.subtitle_mapping
            session.flush()

        # ── Stage: visual_selection ─────────────────────────────────────────
        self._set_stage(job_id, JobStage.visual_selection)
        with self._session_scope() as session:
            job = session.get(Job, job_id)
            # Select images: cycle through available assets to fill the duration
            scene_duration = (job.artifacts or {}).get("scene_duration") or self.settings.gpcg_scene_duration
            assets = session.query(StoryAsset).filter(
                StoryAsset.topic_id == topic_id,
                StoryAsset.processing_status == AssetProcessingStatus.ready.value,
            ).all()

            selected_images = self._select_images(
                assets, narration_duration, scene_duration
            )
            job.artifacts = {
                **job.artifacts,
                "selected_images": selected_images,
                "scene_duration": scene_duration,
            }
            session.flush()

        # ── Stage: music_selection ──────────────────────────────────────────
        self._set_stage(job_id, JobStage.music_selection)
        with self._session_scope() as session:
            job = session.get(Job, job_id)
            plan = session.get(ContentPlan, job.content_plan_id)
            try:
                music_path = vg.select_music(plan.music_mood, min_duration=narration_duration)
            except VideoGenerateError:
                music_path = None
            job.artifacts = {**job.artifacts, "music_path": str(music_path) if music_path else None}
            session.flush()

        # ── Stage: render_plan ──────────────────────────────────────────────
        self._set_stage(job_id, JobStage.render_plan)
        music_path_str = self._get_artifact(job_id, "music_path")
        selected_images = self._get_artifact(job_id, "selected_images")
        video_format = self._get_artifact(job_id, "video_format") or self.settings.gpcg_video_format
        sub_cfg_dict = self._get_artifact(job_id, "subtitle_config") or {}

        with self._session_scope() as session:
            job = session.get(Job, job_id)
            script = session.get(Script, script_id)
            plan = session.get(ContentPlan, job.content_plan_id)

            # Convert images to video clips (Ken Burns effect)
            clips = self._images_to_clips(
                selected_images, narration_duration, scene_duration, video_format
            )

            # Build render plan using the shared RenderPlanBuilder
            from gpcg.application.render_plan_builder import RenderPlanBuilder
            from gpcg.application.gameplay_selector import SelectedClip
            from gpcg.domain.video_profiles import SubtitleConfig

            plan_builder = RenderPlanBuilder()
            subtitle_config = SubtitleConfig(
                font=sub_cfg_dict.get("font", self.settings.gpcg_subtitle_font),
                font_size=sub_cfg_dict.get("font_size", self.settings.gpcg_subtitle_font_size),
                color=sub_cfg_dict.get("color", self.settings.gpcg_subtitle_color),
                outline_color=sub_cfg_dict.get("outline_color", self.settings.gpcg_subtitle_outline_color),
                position=sub_cfg_dict.get("position", self.settings.gpcg_subtitle_position),
                case_transform=sub_cfg_dict.get("case_transform", self.settings.gpcg_subtitle_case),
            )

            rp = plan_builder.build(
                session,
                plan,
                script,
                narration_wav=Path(self._get_artifact(job_id, "narration_wav")),
                narration_duration=narration_duration,
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
        with self._session_scope() as session:
            job = session.get(Job, job_id)
            plan = session.get(ContentPlan, job.content_plan_id)
            script = session.get(Script, script_id)

            batch_id = job.artifacts.get("batch_id")
            scene_dir = Path(job.artifacts.get("scene_dir", ""))

            try:
                video_path = vg.render_video(
                    plan=plan,
                    script=script,
                    narration_wav=Path(self._get_artifact(job_id, "narration_wav")),
                    music_path=Path(music_path_str) if music_path_str else None,
                    scene_dir=scene_dir,
                    batch_id=batch_id,
                    subtitle_mapping=subtitle_mapping,
                    video_format=video_format,
                )
            except VideoGenerateError as e:
                raise KidsGenerationError(f"Render failed: {e}", JobStage.render.value)

            job.artifacts = {**job.artifacts, "video_path": str(video_path)}
            session.flush()

        # ── Stage: create Video record ──────────────────────────────────────
        self._set_stage(job_id, JobStage.output)
        with self._session_scope() as session:
            job = session.get(Job, job_id)
            plan = session.get(ContentPlan, job.content_plan_id)
            video_path = Path(job.artifacts.get("video_path", ""))

            # Probe the video for metadata
            from gpcg.infrastructure.media import probe
            try:
                info = probe(video_path)
                duration = info.duration
                width = info.width
                height = info.height
            except Exception:
                duration = narration_duration
                width = 0
                height = 0

            video = Video(
                user_id=job.user_id,
                content_plan_id=plan.id,
                status=VideoStatus.pending.value,
                file_path=str(video_path),
                duration=duration,
                width=width,
                height=height,
                metadata_json={"domain": "kids", "topic_id": topic_id},
            )
            session.add(video)
            session.flush()
            job.artifacts = {**job.artifacts, "video_id": video.id}
            session.flush()

        # ── Stage: done ─────────────────────────────────────────────────────
        self._set_stage(job_id, JobStage.done)
        with self._session_scope() as session:
            job = session.get(Job, job_id)
            job.status = JobStatus.completed.value
            job.progress = 100.0
            session.flush()

        log.info(f"Kids job #{job_id} completed successfully")
        return True

    # ── Helper methods ──────────────────────────────────────────────────────

    def _create_content_plan(
        self,
        llm: LLMClient,
        session: Session,
        job: Job,
        topic_title: str,
        topic_description: str,
        age_range: str,
        category: str,
        fact_claims: list[str],
        channel_context: str,
    ) -> ContentPlan:
        """Create a ContentPlan for a Kids topic using LLM."""
        target_duration = (job.artifacts or {}).get("target_duration") or 60

        # Build the user prompt
        facts_text = "\n".join(f"- {c}" for c in fact_claims) if fact_claims else "(no specific facts — create educational content about the topic)"
        user_prompt = f"""Topic: {topic_title}
Description: {topic_description}
Age range: {age_range}
Category: {category}
Target duration: {target_duration}s

Available facts:
{facts_text}

Channel context: {channel_context}

Create a content plan for a kid-friendly YouTube Short about this topic."""

        try:
            response = llm.chat(
                system=CONTENT_PLANNING_SYSTEM,
                user=user_prompt,
                temperature=0.7,
            )
            plan_data = json.loads(response)
        except (LLMError, json.JSONDecodeError, Exception) as e:
            log.warning(f"LLM content planning failed, using fallback: {e}")
            plan_data = {
                "topic": topic_title,
                "hook": f"Sabia que {topic_title} é incrível?",
                "tone": "curious",
                "energy": 0.7,
                "music_mood": "cheerful",
                "visual_strategy": "image_slideshow",
            }

        plan = ContentPlan(
            user_id=job.user_id,
            game_id=None,  # Kids has no game
            format="youtube_short",
            target_duration=target_duration,
            topic=plan_data.get("topic", topic_title),
            hook=plan_data.get("hook", ""),
            tone=plan_data.get("tone", "curious"),
            energy=plan_data.get("energy", 0.7),
            music_mood=plan_data.get("music_mood", "cheerful"),
            visual_strategy="image_slideshow",  # Kids always uses image slideshow
            metadata_json={
                "domain": "kids",
                "topic_id": (job.artifacts or {}).get("topic_id"),
                "age_range": age_range,
                "category": category,
            },
        )
        session.add(plan)
        session.flush()
        return plan

    def _generate_script(
        self,
        llm: LLMClient,
        session: Session,
        plan: ContentPlan,
        topic_description: str,
        channel_context: str,
    ) -> Script:
        """Generate a kid-friendly script using Kids prompts."""
        target_chars = int(plan.target_duration * 14)  # ~14 chars/sec for pt-BR

        user_prompt = f"""Topic: {plan.topic}
Hook: {plan.hook}
Tone: {plan.tone}
Energy: {plan.energy}
Target duration: {plan.target_duration}s (~{target_chars} characters)
Topic description: {topic_description}
Channel context: {channel_context}

Write a kid-friendly narration script in pt-BR. Target ~{target_chars} characters."""

        try:
            response = llm.chat(
                system=PLAN_DRAFT_SYSTEM,
                user=user_prompt,
                temperature=0.7,
            )
            script_data = json.loads(response)
            script_text = script_data.get("script", "")
        except (LLMError, json.JSONDecodeError, Exception) as e:
            log.warning(f"LLM script generation failed, using fallback: {e}")
            script_text = f"{plan.hook} {topic_description}"

        # Optimize the script
        try:
            opt_response = llm.chat(
                system=OPTIMIZE_SYSTEM,
                user=f"Script: {script_text}\nTarget: ~{target_chars} characters",
                temperature=0.3,
            )
            opt_data = json.loads(opt_response)
            final_text = opt_data.get("script", script_text)
        except (LLMError, json.JSONDecodeError, Exception):
            final_text = script_text

        script = Script(
            content_plan_id=plan.id,
            draft=script_text,
            optimized=final_text,
            final=final_text,
            status=ScriptStatus.completed.value,
            char_count=len(final_text),
        )
        session.add(script)
        session.flush()
        return script

    def _select_images(
        self,
        assets: list[StoryAsset],
        narration_duration: float,
        scene_duration: float,
    ) -> list[dict]:
        """Select images to fill the narration duration.

        Cycles through available assets to fill the full duration.
        Each image is displayed for scene_duration seconds.
        """
        if not assets:
            return []

        num_scenes = max(1, int(narration_duration / scene_duration))
        selected = []
        for i in range(num_scenes):
            asset = assets[i % len(assets)]  # cycle through assets
            local_path = (asset.metadata_json or {}).get("local_path", "")
            selected.append({
                "asset_id": asset.id,
                "filename": asset.filename,
                "local_path": local_path,
                "scene_index": i,
                "duration": scene_duration,
            })
        return selected

    def _images_to_clips(
        self,
        selected_images: list[dict],
        narration_duration: float,
        scene_duration: float,
        video_format: str,
    ) -> list:
        """Convert images to video clips with Ken Burns effect.

        Creates a temporary video clip for each image using FFmpeg.
        Returns SelectedClip-like objects compatible with RenderPlanBuilder.
        """
        from gpcg.application.gameplay_selector import SelectedClip
        from gpcg.infrastructure.media import get_resolution

        w, h = get_resolution(video_format or "9:16")
        scene_dir = Path(tempfile.mkdtemp(prefix="kids_scenes_"))

        clips = []
        for img_info in selected_images:
            local_path = img_info.get("local_path", "")
            if not local_path or not Path(local_path).exists():
                log.warning(f"Image not found: {local_path}, skipping")
                continue

            scene_num = img_info["scene_index"] + 1
            scene_file = scene_dir / f"scene_{scene_num:03d}.mp4"
            duration = img_info.get("duration", scene_duration)

            try:
                # Create a video from the image with Ken Burns effect
                self._image_to_video(
                    Path(local_path), scene_file, duration, w, h
                )
                clips.append(SelectedClip(
                    asset=None,  # Kids doesn't use GameplayAsset
                    source_path=str(scene_file),
                    start_sec=0.0,
                    end_sec=duration,
                    duration=duration,
                    scene_index=img_info["scene_index"],
                ))
            except Exception as e:
                log.error(f"Failed to convert image to video: {e}")
                # Create a solid color fallback
                try:
                    self._solid_color_video(scene_file, duration, w, h)
                    clips.append(SelectedClip(
                        asset=None,
                        source_path=str(scene_file),
                        start_sec=0.0,
                        end_sec=duration,
                        duration=duration,
                        scene_index=img_info["scene_index"],
                    ))
                except Exception:
                    pass

        return clips

    def _image_to_video(
        self, image_path: Path, output_path: Path, duration: float, w: int, h: int
    ) -> None:
        """Convert an image to a video clip with Ken Burns zoom effect."""
        # FFmpeg: create a video from an image with a slow zoom
        # scale to fit, pad to target resolution, apply zoompan
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"zoompan=z='min(zoom+0.0015,1.3)':d={int(duration*30)}:s={w}x{h}:fps=30"
        )
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", str(image_path),
            "-t", str(duration), "-vf", vf,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-r", "30", str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed: {result.stderr[:500]}")

    def _solid_color_video(self, output_path: Path, duration: float, w: int, h: int) -> None:
        """Create a solid color video as fallback."""
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            f"color=c=black:s={w}x{h}:d={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(output_path),
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    def _set_stage(self, job_id: int, stage: JobStage) -> None:
        with self._session_scope() as session:
            job = session.get(Job, job_id)
            if job:
                job.stage = stage.value
                session.flush()
        log.info(f"Kids job #{job_id} → stage={stage.value}")

    def _get_artifact(self, job_id: int, key: str):
        with self._session_scope() as session:
            job = session.get(Job, job_id)
            return (job.artifacts or {}).get(key) if job else None

    def _mark_failed(self, job_id: int, error: str, stage: str = "render") -> None:
        with self._session_scope() as session:
            job = session.get(Job, job_id)
            if job:
                job.status = JobStatus.failed.value
                job.stage = stage
                job.error = error[:2000]
                session.flush()
