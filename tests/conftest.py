"""Test fixtures — small synthetic media files for testing."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    """Ensure the database schema is initialized for tests.

    Uses a temporary SQLite database to avoid polluting the real one.
    """
    os.environ.setdefault("GPCG_DB_PATH", "/tmp/gpcg_test.db")
    # Remove old test DB to start fresh
    db_path = os.environ["GPCG_DB_PATH"]
    if Path(db_path).exists():
        Path(db_path).unlink()
    from gpcg.infrastructure.database import init_db
    init_db()
    yield
    # Cleanup
    if Path(db_path).exists():
        Path(db_path).unlink()


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    return FIXTURES


@pytest.fixture(scope="session")
def sample_video(fixtures_dir: Path) -> Path:
    """A 3-second 640x480 test video with audio."""
    out = fixtures_dir / "sample_3s.mp4"
    if out.exists():
        return out
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=640x480:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


@pytest.fixture(scope="session")
def sample_video_vertical(fixtures_dir: Path) -> Path:
    """A 2-second 1080x1920 vertical test video."""
    out = fixtures_dir / "sample_vertical.mp4"
    if out.exists():
        return out
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=2:size=1080x1920:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=300:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out
