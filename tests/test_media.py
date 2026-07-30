"""Tests for media helpers — FFprobe, clip extraction, hashing."""

from pathlib import Path

import pytest

from gpcg.infrastructure.media import (
    MediaError,
    extract_clip,
    extract_frames,
    file_hash,
    generate_thumbnail,
    is_file_stable,
    probe,
)


class TestProbe:
    def test_probe_returns_metadata(self, sample_video: Path):
        info = probe(sample_video)
        assert info.duration == pytest.approx(3.0, abs=0.5)
        assert info.width == 640
        assert info.height == 480
        assert info.has_audio is True
        assert info.codec == "h264"
        assert info.fps == pytest.approx(30.0, abs=1.0)

    def test_probe_missing_file(self):
        with pytest.raises(MediaError):
            probe("/nonexistent/file.mp4")

    def test_aspect_ratio(self, sample_video: Path):
        info = probe(sample_video)
        assert info.is_vertical is False
        assert info.aspect_ratio == "4:3"

    def test_vertical_aspect(self, sample_video_vertical: Path):
        info = probe(sample_video_vertical)
        assert info.is_vertical is True
        assert info.aspect_ratio == "9:16"


class TestExtractClip:
    def test_extract_clip_creates_file(self, sample_video: Path, tmp_path: Path):
        out = tmp_path / "clip.mp4"
        result = extract_clip(sample_video, out, start=0.5, end=2.0, width=640, height=480)
        assert result.exists()
        info = probe(out)
        assert info.duration == pytest.approx(1.5, abs=0.3)
        assert info.width == 640

    def test_extract_clip_vertical(self, sample_video: Path, tmp_path: Path):
        out = tmp_path / "clip_v.mp4"
        extract_clip(sample_video, out, start=0.0, end=1.0, width=1080, height=1920)
        info = probe(out)
        assert info.width == 1080
        assert info.height == 1920

    def test_extract_clip_invalid_range(self, sample_video: Path, tmp_path: Path):
        with pytest.raises(MediaError):
            extract_clip(sample_video, tmp_path / "x.mp4", start=5.0, end=2.0)


class TestExtractFrames:
    def test_extract_frames_count(self, sample_video: Path, tmp_path: Path):
        frames = extract_frames(sample_video, tmp_path, count=3)
        assert len(frames) == 3
        for f in frames:
            assert f.exists()
            assert f.suffix == ".jpg"


class TestFileHash:
    def test_hash_stable(self, sample_video: Path):
        h1 = file_hash(sample_video)
        h2 = file_hash(sample_video)
        assert h1 == h2
        assert len(h1) == 64  # sha256 hex

    def test_hash_differs_for_different_files(self, sample_video: Path, sample_video_vertical: Path):
        h1 = file_hash(sample_video)
        h2 = file_hash(sample_video_vertical)
        assert h1 != h2


class TestIsFileStable:
    def test_stable_file(self, sample_video: Path):
        # Already-written file should be stable
        assert is_file_stable(sample_video, stable_seconds=1) is True


class TestThumbnail:
    def test_generate_thumbnail(self, sample_video: Path, tmp_path: Path):
        out = tmp_path / "thumb.jpg"
        generate_thumbnail(sample_video, out, at=1.0)
        assert out.exists()
