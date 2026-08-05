"""Render plan builder — assembles the video-generate request_data from a
ContentPlan + Script + TTS + selected gameplay clips + music.

Produces:
  - A temp directory with scene_NNN.mp4 files (extracted + concatenated clips)
  - A scene_timeline list
  - The full request_data dict for process_video_request

When scene-based selection is used (clips have scene_index), clips belonging
to the same scene are concatenated into a single scene_NNN.mp4 file using
FFmpeg's concat filter. This supports the "long scene with chaining" use case
where scene_duration > individual gameplay video duration.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from itertools import groupby
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from gpcg.application.gameplay_selector import SelectedClip
from gpcg.config import get_settings
from gpcg.domain.models import ContentPlan, Script
from gpcg.domain.video_profiles import (
    SubtitleConfig,
    get_profile_dict,
    get_profile_name,
    get_resolution,
)
from gpcg.infrastructure.media import extract_clip
from gpcg.logging import get_logger

log = get_logger(__name__)


@dataclass
class RenderPlan:
    """Intermediate representation before calling video-generate."""

    content_plan_id: int
    script_id: int
    batch_id: str
    scene_dir: Path  # contains scene_001.mp4, scene_002.mp4, ...
    scene_timeline: list[dict]
    narration_wav: Path
    narration_text: str
    subtitle_mapping: dict
    music_path: Optional[Path]
    music_delay: int
    video_profile: str
    video_format: str
    subtitle_config: Optional[SubtitleConfig]
    request_data: dict = field(default_factory=dict)

    def cleanup(self) -> None:
        """Remove the temp scene dir."""
        shutil.rmtree(self.scene_dir, ignore_errors=True)


class RenderPlanBuilder:
    """Builds a RenderPlan from domain entities + TTS/music results."""

    def __init__(self) -> None:
        self.settings = get_settings()

    def build(
        self,
        session: Session,
        content_plan: ContentPlan,
        script: Script,
        narration_wav: Path,
        narration_duration: float,
        subtitle_mapping: dict,
        selected_clips: list[SelectedClip],
        music_path: Optional[Path],
        *,
        video_format: str = "",
        subtitle_config: Optional[SubtitleConfig] = None,
    ) -> RenderPlan:
        """Assemble the render plan. Extracts clips to scene_NNN.mp4 files.

        Args:
            video_format: Override for video format ("9:16", "16:9", "1:1", "4:5").
                Empty = use config default.
            subtitle_config: Override for subtitle customization.
                None = use config defaults.
        """
        batch_id = f"gpcg_{content_plan.id}_{int(time.time())}"

        # Resolve video format + resolution
        fmt = video_format or self.settings.gpcg_video_format or "9:16"
        w, h = get_resolution(fmt)
        profile_name = get_profile_name(fmt)

        # Resolve subtitle config
        if subtitle_config is None:
            # REFACTORY_V2: this path means the caller didn't pass per-job
            # subtitle config. Log a warning so it's visible if artifacts are
            # missing. GenerationService should always pass a non-None config
            # constructed from job.artifacts["subtitle_config"].
            log.warning(
                "RenderPlanBuilder.build: subtitle_config is None — "
                "falling back to global config defaults. This may indicate "
                "missing subtitle_config in job artifacts."
            )
            subtitle_config = SubtitleConfig(
                font=self.settings.gpcg_subtitle_font,
                font_size=self.settings.gpcg_subtitle_font_size,
                color=self.settings.gpcg_subtitle_color,
                outline_color=self.settings.gpcg_subtitle_outline_color,
                position=self.settings.gpcg_subtitle_position,
                case_transform=self.settings.gpcg_subtitle_case,
            )

        # Build the custom profile dict (with subtitle overrides)
        profile_dict = get_profile_dict(fmt, subtitle_config)

        # Create scene dir
        scene_dir = Path(tempfile.mkdtemp(prefix=f"gpcg_scenes_{batch_id}_"))

        # Group clips by scene_index — clips in the same scene get concatenated
        scene_timeline = []
        cumulative = 0.0
        scene_num = 0

        # Sort clips by scene_index to ensure proper ordering
        sorted_clips = sorted(selected_clips, key=lambda c: (c.scene_index,))

        for scene_idx, scene_clips in groupby(sorted_clips, key=lambda c: c.scene_index):
            scene_clips_list = list(scene_clips)
            scene_num += 1
            scene_file = scene_dir / f"scene_{scene_num:03d}.mp4"

            # Calculate total scene duration
            scene_dur = sum(c.duration for c in scene_clips_list)

            extraction_ok = False
            if len(scene_clips_list) == 1:
                # Single clip — extract directly
                clip = scene_clips_list[0]
                try:
                    extract_clip(
                        clip.source_path,
                        scene_file,
                        start=clip.start_sec,
                        end=clip.end_sec,
                        width=w,
                        height=h,
                    )
                    extraction_ok = True
                except Exception as e:
                    log.error(f"failed to extract scene {scene_num} from {clip.source_path}: {e}")
            else:
                # Multiple clips — extract each then concatenate
                try:
                    self._extract_and_concatenate(scene_clips_list, scene_file, w, h)
                    extraction_ok = True
                except Exception as e:
                    log.error(f"failed to build scene {scene_num} (concat): {e}")

            # Fallback: if extraction failed, reuse the last successful scene
            # to avoid cutting the video short (narration continues but visual
            # would end early). This ensures video duration ≈ narration duration.
            if not extraction_ok:
                last_scene_file = scene_dir / f"scene_{scene_num - 1:03d}.mp4"
                if last_scene_file.exists():
                    import shutil as _shutil
                    _shutil.copy2(last_scene_file, scene_file)
                    log.warning(
                        f"scene {scene_num} extraction failed — reusing scene {scene_num - 1} "
                        f"as fallback to preserve video duration"
                    )
                    extraction_ok = True
                else:
                    log.error(f"scene {scene_num} extraction failed and no fallback available — skipping")
                    continue

            scene_timeline.append(
                {
                    "scene_id": scene_num,
                    "start_time": round(cumulative, 3),
                    "end_time": round(cumulative + scene_dur, 3),
                    "duration": round(scene_dur, 3),
                    "block_name": f"gameplay_clip_{scene_num}",
                    "narrative_intent": content_plan.tone,
                }
            )
            cumulative += scene_dur

            # Mark assets as used
            for clip in scene_clips_list:
                clip.asset.used_count += 1

        if not scene_timeline:
            raise ValueError("no clips could be extracted — cannot build render plan")

        # V3: Padding — if total scene duration < narration duration, extend
        # the last scene by duplicating it to cover the gap. This prevents the
        # video from ending before the narration finishes (abrupt cut).
        # The gameplay selector may not always fill the exact target duration
        # (e.g. single-source GENERAL_TOPIC with limited available segments).
        gap = narration_duration - cumulative
        if gap > 0.5:
            import shutil as _shutil
            last_scene = scene_timeline[-1]
            last_scene_num = len(scene_timeline)
            source_scene_file = scene_dir / f"scene_{last_scene_num:03d}.mp4"
            if source_scene_file.exists():
                last_scene_dur = last_scene["duration"]
                padding_added = 0.0
                pad_scene_num = last_scene_num + 1
                while padding_added < gap - 0.5:
                    pad_file = scene_dir / f"scene_{pad_scene_num:03d}.mp4"
                    _shutil.copy2(source_scene_file, pad_file)
                    pad_dur = min(last_scene_dur, gap - padding_added)
                    scene_timeline.append({
                        "scene_id": pad_scene_num,
                        "start_time": round(cumulative + padding_added, 3),
                        "end_time": round(cumulative + padding_added + pad_dur, 3),
                        "duration": round(pad_dur, 3),
                        "block_name": f"gameplay_clip_{pad_scene_num}",
                        "narrative_intent": last_scene["narrative_intent"],
                    })
                    padding_added += pad_dur
                    pad_scene_num += 1
                cumulative += padding_added
                log.info(
                    f"padding: added {padding_added:.1f}s by duplicating last scene "
                    f"to cover narration gap (was {cumulative - padding_added:.1f}s, "
                    f"now {cumulative:.1f}s, narration={narration_duration:.1f}s)"
                )

        # Build the request_data for video-generate
        request_data: dict[str, Any] = {
            "audio_principal": str(narration_wav),
            "musica_fundo": str(music_path) if music_path else None,
            "delay_musica": 3,
            "img_dir": str(scene_dir),
            "audio_params": None,
            "original_narration_text": script.final,
            "subtitle_mapping": subtitle_mapping,
            "scene_timeline": scene_timeline,
            "request_id": int(content_plan.id),
            "batch_id": batch_id,
            "video_profile": profile_name,
            # Custom profile dict — the adapter will register this in video-generate
            # before calling process_video_request
            "_gpcg_custom_profile": profile_dict,
        }

        plan = RenderPlan(
            content_plan_id=content_plan.id,
            script_id=script.id,
            batch_id=batch_id,
            scene_dir=scene_dir,
            scene_timeline=scene_timeline,
            narration_wav=narration_wav,
            narration_text=script.final,
            subtitle_mapping=subtitle_mapping,
            music_path=music_path,
            music_delay=3,
            video_profile=profile_name,
            video_format=fmt,
            subtitle_config=subtitle_config,
            request_data=request_data,
        )
        log.info(
            f"render plan built: batch={batch_id} format={fmt} {w}x{h} "
            f"scenes={len(scene_timeline)} total_scene_dur={cumulative:.1f}s "
            f"narration={narration_duration:.1f}s"
        )
        return plan

    def _extract_and_concatenate(
        self,
        clips: list[SelectedClip],
        output: Path,
        width: int,
        height: int,
    ) -> None:
        """Extract multiple clips and concatenate them into a single file.

        Uses FFmpeg's concat demuxer: extract each clip to a temp file,
        write a concat list, then concatenate.
        """
        tmp_dir = output.parent / f"_tmp_{output.stem}"
        tmp_dir.mkdir(exist_ok=True)

        part_files: list[Path] = []
        for i, clip in enumerate(clips):
            part_file = tmp_dir / f"part_{i:03d}.mp4"
            extract_clip(
                clip.source_path,
                part_file,
                start=clip.start_sec,
                end=clip.end_sec,
                width=width,
                height=height,
            )
            part_files.append(part_file)

        # Write concat list file
        concat_list = tmp_dir / "concat.txt"
        with open(concat_list, "w") as f:
            for pf in part_files:
                f.write(f"file '{pf.absolute()}'\n")

        # Concatenate using the concat demuxer (re-encode for safety)
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c:v", "libx264", "-preset", "medium", "-crf", "21",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(output),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {result.stderr[-500:]}")

        # Cleanup temp files
        shutil.rmtree(tmp_dir, ignore_errors=True)
