"""Frame sampler — adaptive temporal frame extraction via FFmpeg.

Supports two modes:
  1. Coarse sampling: one frame per N-second segment (for the first pass)
  2. Dense sampling: frames at a fixed interval within a time range (for refinement)

Frames are extracted as JPGs to a temp directory and cleaned up by the caller.
Does NOT use a fixed "1 frame every X seconds" strategy as the primary mode —
the coarse pass uses segment-midpoint sampling, and refinement is targeted.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from gpcg.infrastructure.media import MediaError, probe, _run


@dataclass
class SampledFrame:
    """A single extracted frame with its timestamp."""
    path: Path
    timestamp: float


class FrameSampler:
    """Extracts frames from a video at specified timestamps."""

    def __init__(self, *, jpeg_quality: int = 5) -> None:
        # q:v 2-5 is good for VLM analysis (5 = smaller, still clear enough)
        self.jpeg_quality = jpeg_quality

    def extract_at_timestamps(
        self,
        source: str | Path,
        timestamps: list[float],
        output_dir: Optional[Path] = None,
    ) -> list[SampledFrame]:
        """Extract one frame at each timestamp.

        Args:
            source: video file path
            timestamps: list of positions in seconds
            output_dir: where to put JPGs (temp dir if None)

        Returns:
            List of SampledFrame (only successful extractions, in order)
        """
        source = Path(source)
        cleanup = False
        if output_dir is None:
            output_dir = Path(tempfile.mkdtemp(prefix="gpcg_frames_"))
            cleanup = True
        else:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

        frames: list[SampledFrame] = []
        try:
            for i, t in enumerate(timestamps):
                if t < 0:
                    continue
                out = output_dir / f"frame_{i:04d}_{t:.1f}s.jpg"
                cmd = [
                    "ffmpeg", "-y",
                    "-ss", f"{t:.3f}",
                    "-i", str(source),
                    "-frames:v", "1",
                    "-q:v", str(self.jpeg_quality),
                    str(out),
                ]
                try:
                    _run(cmd, timeout=30)
                    if out.exists() and out.stat().st_size > 0:
                        frames.append(SampledFrame(path=out, timestamp=t))
                except MediaError:
                    continue
        finally:
            if cleanup:
                # Only clean up if we created the dir; caller handles otherwise
                pass  # frames point to files in the temp dir; caller should clean

        return frames

    def coarse_sample(
        self,
        source: str | Path,
        segment_sec: float = 30.0,
        output_dir: Optional[Path] = None,
    ) -> list[SampledFrame]:
        """Coarse pass: one frame at the midpoint of each segment.

        For a 120s video with segment_sec=30: frames at 15, 45, 75, 105.
        This is the low-resolution first pass to identify boundaries.
        """
        source = Path(source)
        info = probe(source)
        if info.duration <= 0:
            raise MediaError(f"cannot sample zero-duration file: {source}")

        timestamps = []
        t = segment_sec / 2  # midpoint of first segment
        while t < info.duration:
            timestamps.append(t)
            t += segment_sec

        return self.extract_at_timestamps(source, timestamps, output_dir)

    def dense_sample(
        self,
        source: str | Path,
        start: float,
        end: float,
        interval_sec: float = 3.0,
        output_dir: Optional[Path] = None,
    ) -> list[SampledFrame]:
        """Dense pass: frames at fixed interval within [start, end].

        Used for adaptive refinement of high-activity zones.
        For a 20s zone with interval=3: frames at start+1.5, +4.5, +7.5, ...
        (offset by half-interval to avoid landing exactly on boundaries)
        """
        source = Path(source)
        if end <= start:
            return []

        timestamps = []
        # Offset by half interval to center frames within sub-segments
        t = start + interval_sec / 2
        while t < end:
            timestamps.append(t)
            t += interval_sec

        return self.extract_at_timestamps(source, timestamps, output_dir)

    def extract_audio(
        self,
        source: str | Path,
        output_path: Optional[Path] = None,
    ) -> Path:
        """Extract audio track as WAV for ASR transcription.

        Returns path to the WAV file. Raises MediaError if no audio or failure.
        """
        source = Path(source)
        info = probe(source)
        if not info.has_audio:
            raise MediaError(f"no audio stream in {source}")

        if output_path is None:
            output_path = Path(tempfile.mktemp(suffix=".wav", prefix="gpcg_audio_"))
        else:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

        # Extract to 16kHz mono WAV (optimal for Whisper)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(source),
            "-vn",  # no video
            "-ac", "1",  # mono
            "-ar", "16000",  # 16kHz
            "-c:a", "pcm_s16le",
            str(output_path),
        ]
        _run(cmd, timeout=600)
        return output_path

    def cleanup_dir(self, dir_path: Path) -> None:
        """Remove a temporary frame directory."""
        dir_path = Path(dir_path)
        if dir_path.exists() and dir_path.is_dir():
            shutil.rmtree(dir_path, ignore_errors=True)
