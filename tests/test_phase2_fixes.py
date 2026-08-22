"""Tests for Phase 2 fixes: inbox watcher config, worker healthcheck, domain config.

These tests verify the Phase 2 audit fixes:

1. gpcg_inbox_watcher_enabled config exists and defaults to True
2. Worker skips inbox scan when GPCG_INBOX_WATCHER_ENABLED=false
3. DomainConfig has all required domains (games, kids, movies)
4. DomainConfig themes have all required tokens
5. DomainConfig features are correctly set per domain
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


def test_inbox_watcher_config_exists():
    """gpcg_inbox_watcher_enabled should exist and default to True."""
    from gpcg.config import get_settings

    settings = get_settings()
    assert hasattr(settings, "gpcg_inbox_watcher_enabled")
    assert settings.gpcg_inbox_watcher_enabled is True


def test_inbox_watcher_can_be_disabled():
    """Setting GPCG_INBOX_WATCHER_ENABLED=false should disable the watcher."""
    from gpcg.config import Settings

    settings = Settings(gpcg_inbox_watcher_enabled=False)
    assert settings.gpcg_inbox_watcher_enabled is False


def test_worker_skips_inbox_when_disabled():
    """Worker should skip inbox scan when gpcg_inbox_watcher_enabled=False.

    We verify the config check logic without running the full worker loop
    (which would block forever). Instead we check that the worker code
    respects the flag by inspecting the source.
    """
    import pathlib

    worker_file = pathlib.Path(__file__).parent.parent / "src" / "gpcg" / "application" / "worker.py"
    content = worker_file.read_text()

    # The worker should check gpcg_inbox_watcher_enabled before scanning
    assert "gpcg_inbox_watcher_enabled" in content
    assert "ingestion.scan_once()" in content
    # The check should be in the same conditional block
    assert "getattr(settings, \"gpcg_inbox_watcher_enabled\", True)" in content


def test_domain_configs_have_all_domains():
    """DOMAIN_CONFIGS should have games, kids, and movies."""
    # This test imports from the frontend source — we verify the structure
    # by reading the file directly since we can't import TSX in Python
    import pathlib

    config_file = pathlib.Path(__file__).parent.parent / "frontend" / "src" / "lib" / "domain-config.tsx"
    content = config_file.read_text()

    assert "games:" in content
    assert "kids:" in content
    assert "movies:" in content
    assert "GAMES_THEME" in content
    assert "KIDS_THEME" in content
    assert "MOVIES_THEME" in content


def test_domain_configs_have_required_theme_tokens():
    """Each domain theme should have all 14+ design tokens."""
    import pathlib

    config_file = pathlib.Path(__file__).parent.parent / "frontend" / "src" / "lib" / "domain-config.tsx"
    content = config_file.read_text()

    required_tokens = [
        "accent:", "accentHover:", "accentGlow:", "accentWarm:",
        "bg:", "bgDeep:", "surface:", "surfaceElevated:", "surfaceHover:",
        "border:", "borderBright:",
        "text:", "textSecondary:", "textMuted:",
        "radius:", "logoIcon:", "appName:",
    ]

    for token in required_tokens:
        assert token in content, f"Missing theme token: {token}"


def test_domain_configs_have_features():
    """Each domain should have a features section with the right flags."""
    import pathlib

    config_file = pathlib.Path(__file__).parent.parent / "frontend" / "src" / "lib" / "domain-config.tsx"
    content = config_file.read_text()

    required_features = [
        "gameplayUpload:",
        "ideas:",
        "topics:",
        "gameRegistry:",
        "knowledgeItems:",
        "curiosityShorts:",
    ]

    for feature in required_features:
        assert feature in content, f"Missing feature flag: {feature}"


def test_games_features_enabled():
    """Games domain should have gameplayUpload, ideas, gameRegistry enabled."""
    import pathlib

    config_file = pathlib.Path(__file__).parent.parent / "frontend" / "src" / "lib" / "domain-config.tsx"
    content = config_file.read_text()

    # Find the games section and verify features
    games_start = content.index("games: {")
    games_end = content.index("},", games_start) + 2
    games_section = content[games_start:games_end]

    assert "gameplayUpload: true" in games_section
    assert "ideas: true" in games_section
    assert "gameRegistry: true" in games_section


def test_kids_features_enabled():
    """Kids domain should have topics enabled, gameplayUpload disabled."""
    import pathlib

    config_file = pathlib.Path(__file__).parent.parent / "frontend" / "src" / "lib" / "domain-config.tsx"
    content = config_file.read_text()

    kids_start = content.index("kids: {")
    kids_end = content.index("},", kids_start) + 2
    kids_section = content[kids_start:kids_end]

    assert "topics: true" in kids_section
    assert "gameplayUpload: false" in kids_section


def test_movies_not_implemented():
    """Movies domain should have implemented: false."""
    import pathlib

    config_file = pathlib.Path(__file__).parent.parent / "frontend" / "src" / "lib" / "domain-config.tsx"
    content = config_file.read_text()

    movies_start = content.index("movies: {")
    movies_end = content.index("},", movies_start) + 2
    movies_section = content[movies_start:movies_end]

    assert "implemented: false" in movies_section


def test_domain_persistence_uses_localStorage():
    """DomainProvider should persist domain to localStorage."""
    import pathlib

    config_file = pathlib.Path(__file__).parent.parent / "frontend" / "src" / "lib" / "domain-config.tsx"
    content = config_file.read_text()

    assert "DOMAIN_STORAGE_KEY" in content
    assert "localStorage.getItem" in content
    assert "localStorage.setItem" in content
    assert "gpcg-domain" in content
