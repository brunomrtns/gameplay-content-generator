"""FFmpeg / FFprobe helpers — deterministic media operations.

Never use AI for what FFprobe/FFmpeg can do.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class MediaError(Exception):
    """Raised when a media operation fails."""


@dataclass
class MediaInfo:
    """Probe result for a media file."""

    duration: float
    width: int
    height: int
    fps: float
    codec: str
    has_audio: bool
    audio_codec: Optional[str]
    file_size: int

    @property
    def aspect_ratio(self) -> str:
        if self.height == 0:
            return "0:0"
        from math import gcd

        g = gcd(self.width, self.height)
        return f"{self.width // g}:{self.height // g}"

    @property
    def is_vertical(self) -> bool:
        return self.height > self.width

    def to_dict(self) -> dict:
        return {
            "duration": self.duration,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "codec": self.codec,
            "has_audio": self.has_audio,
            "audio_codec": self.audio_codec,
            "file_size": self.file_size,
            "aspect_ratio": self.aspect_ratio,
        }


def _run(cmd: list[str], timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as e:
        raise MediaError(f"ffprobe/ffmpeg not found: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise MediaError(f"command timed out: {' '.join(cmd)}") from e
    if result.returncode != 0:
        raise MediaError(f"command failed: {' '.join(cmd)}\nstderr: {result.stderr[:500]}")
    return result.stdout


def probe(path: str | Path) -> MediaInfo:
    """Probe a media file with ffprobe. Raises MediaError on failure."""
    path = str(path)
    if not Path(path).exists():
        raise MediaError(f"file not found: {path}")

    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    out = _run(cmd)
    data = json.loads(out)

    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video_stream is None and audio_stream is None:
        raise MediaError(f"no media streams in {path}")

    duration = float(data.get("format", {}).get("duration", 0.0))
    if video_stream is None:
        # Audio-only file (e.g. TTS WAV) — return zeroed video fields
        return MediaInfo(
            duration=duration,
            width=0,
            height=0,
            fps=0.0,
            codec="audio-only",
            has_audio=True,
            audio_codec=audio_stream.get("codec_name") if audio_stream else None,
            file_size=int(data.get("format", {}).get("size", 0)) or Path(path).stat().st_size,
        )

    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))
    codec = video_stream.get("codec_name", "unknown")

    # FPS from r_frame_rate (e.g. "30/1")
    fps = 0.0
    rfr = video_stream.get("r_frame_rate", "0/1")
    try:
        num, den = rfr.split("/")
        den_f = float(den) or 1.0
        fps = float(num) / den_f
    except (ValueError, ZeroDivisionError):
        fps = 0.0

    file_size = int(data.get("format", {}).get("size", 0)) or Path(path).stat().st_size

    return MediaInfo(
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        codec=codec,
        has_audio=audio_stream is not None,
        audio_codec=audio_stream.get("codec_name") if audio_stream else None,
        file_size=file_size,
    )


def extract_clip(
    source: str | Path,
    output: str | Path,
    start: float,
    end: float,
    *,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> Path:
    """Extract a clip [start, end] from source, scaled to width×height.

    Uses stream copy when no rescaling needed, otherwise re-encodes.
    """
    source = Path(source)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    duration = max(0.0, end - start)
    if duration <= 0:
        raise MediaError(f"invalid clip range: start={start} end={end}")

    # Re-encode with scaling + crop to target (vertical 9:16)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps={fps}"
    )
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}",
        "-i", str(source),
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "21",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(output),
    ]
    _run(cmd, timeout=600)
    return output


def extract_image_clip(
    source: str | Path,
    output: str | Path,
    duration: float,
    *,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    ken_burns: bool = True,
) -> Path:
    """Create a video clip from a static image, scaled to width×height.

    Uses FFmpeg's zoompan filter for a subtle Ken Burns effect (slow
    zoom + pan) when ``ken_burns=True``. Otherwise, produces a static
    image video of the specified duration.

    Args:
        source: path to the image file (PNG, JPEG, WebP, etc)
        output: path to the output video file (.mp4)
        duration: how long the image should appear (seconds)
        width: target video width
        height: target video height
        fps: output frame rate
        ken_burns: if True, apply a slow zoom-in effect
    """
    source = Path(source)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if duration <= 0:
        raise MediaError(f"invalid image duration: {duration}")

    total_frames = int(duration * fps)

    if ken_burns:
        # Ken Burns: slow zoom from 1.0 to 1.1 over the duration
        # zoompan needs frame-based expressions
        # 'z' = zoom factor (starts at 1.0, increases to 1.1)
        # 'on' = output frame number (0 to total_frames)
        zoom_expr = f"min(zoom+0.0015,1.1)" if total_frames > 0 else "1.0"
        vf = (
            f"scale={width*2}:{height*2}:force_original_aspect_ratio=increase,"
            f"crop={width*2}:{height*2},"
            f"zoompan=z='{zoom_expr}':d={total_frames}:s={width}x{height}:fps={fps},"
            f"setsar=1"
        )
    else:
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps={fps}"
        )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(source),
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "21",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output),
    ]
    _run(cmd, timeout=600)
    return output


def extract_frames(
    source: str | Path,
    output_dir: str | Path,
    *,
    count: int = 5,
    prefix: str = "frame",
) -> list[Path]:
    """Extract N evenly-spaced frames from source for VLM analysis."""
    source = Path(source)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    info = probe(source)
    if info.duration <= 0:
        raise MediaError(f"cannot extract frames from zero-duration file: {source}")

    # Evenly space timestamps, avoiding the very start/end
    timestamps = []
    for i in range(count):
        t = (i + 1) / (count + 1) * info.duration
        timestamps.append(t)

    frames = []
    for i, t in enumerate(timestamps):
        out = output_dir / f"{prefix}_{i:03d}.jpg"
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{t:.3f}",
            "-i", str(source),
            "-frames:v", "1",
            "-q:v", "3",
            str(out),
        ]
        try:
            _run(cmd, timeout=30)
            if out.exists():
                frames.append(out)
        except MediaError:
            continue
    return frames


def generate_thumbnail(video: str | Path, output: str | Path, at: float = 1.0) -> Path:
    """Generate a thumbnail JPG from a video at a given timestamp."""
    video = Path(video)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{at:.3f}",
        "-i", str(video),
        "-frames:v", "1",
        "-q:v", "3",
        str(output),
    ]
    _run(cmd, timeout=30)
    return output


def file_hash(path: str | Path, *, prefix_bytes: int = 64 * 1024 * 1024) -> str:
    """Compute a fast hash: SHA-256 of (file_size + first prefix_bytes).

    Full-file hashing of multi-GB recordings is wasteful; the prefix + size
    is enough for duplicate detection of recordings.
    """
    import hashlib

    path = Path(path)
    size = path.stat().st_size
    h = hashlib.sha256()
    h.update(str(size).encode())
    with open(path, "rb") as f:
        read = 0
        while read < prefix_bytes:
            chunk = f.read(min(1024 * 1024, prefix_bytes - read))
            if not chunk:
                break
            h.update(chunk)
            read += len(chunk)
    return h.hexdigest()


def is_file_stable(path: str | Path, stable_seconds: int = 10) -> bool:
    """Check if a file's size hasn't changed for stable_seconds (heuristic for
    'recording finished'). Uses mtime + size snapshot."""
    import time

    path = Path(path)
    if not path.exists():
        return False
    try:
        s1 = path.stat()
    except OSError:
        return False
    time.sleep(min(2, stable_seconds))
    try:
        s2 = path.stat()
    except OSError:
        return False
    # Size stable and mtime not changing
    return s1.st_size == s2.st_size and s1.st_mtime == s2.st_mtime and s1.st_size > 0


def is_being_written(path: str | Path) -> bool:
    """Best-effort check if a file is open for writing by another process."""
    try:
        # Try opening for append — fails on some OSes if locked
        with open(path, "a"):
            pass
        return False
    except (PermissionError, OSError):
        return True
