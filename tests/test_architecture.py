"""Architecture tests — enforce Core/Domain boundary.

These tests verify that the Core layer does not depend on any domain layer
(Games or Kids), and that domains do not depend on each other.

Approved rules:
1. gpcg.core.models must not import gpcg.domains.games.models or gpcg.domains.kids.models
2. gpcg.core package must not import from gpcg.domains
3. Importing gpcg.core.models does not pull in gpcg.domains
4. gpcg.domains.games must not import gpcg.domains.kids
5. gpcg.domains.kids must not import gpcg.domains.games
"""

from __future__ import annotations

import importlib
import sys

import pytest


def test_core_models_does_not_import_games_models():
    """gpcg.core.models must not import from gpcg.domains.games.models."""
    # Clear any cached imports
    mods_to_clear = [
        k for k in sys.modules
        if k.startswith("gpcg.core") or k.startswith("gpcg.domains")
    ]
    for k in mods_to_clear:
        del sys.modules[k]

    import gpcg.core.models as core_models

    # Check that games models module is NOT in sys.modules
    assert "gpcg.domains.games.models" not in sys.modules, (
        "gpcg.core.models transitively imports gpcg.domains.games.models — "
        "Core must not depend on Games."
    )


def test_core_models_does_not_import_kids_models():
    """gpcg.core.models must not import from gpcg.domains.kids.models."""
    mods_to_clear = [
        k for k in sys.modules
        if k.startswith("gpcg.core") or k.startswith("gpcg.domains")
    ]
    for k in mods_to_clear:
        del sys.modules[k]

    import gpcg.core.models  # noqa: F811

    assert "gpcg.domains.kids.models" not in sys.modules, (
        "gpcg.core.models transitively imports gpcg.domains.kids.models — "
        "Core must not depend on Kids."
    )


def test_core_package_does_not_import_domains():
    """No module under gpcg.core should import from gpcg.domains."""
    # Clear cached imports
    mods_to_clear = [
        k for k in sys.modules
        if k.startswith("gpcg.core") or k.startswith("gpcg.domains")
    ]
    for k in mods_to_clear:
        del sys.modules[k]

    import gpcg.core.models

    # Verify no gpcg.domains module was loaded as a side effect
    domain_mods = [k for k in sys.modules if k.startswith("gpcg.domains")]
    assert len(domain_mods) == 0, (
        f"gpcg.core transitively imports gpcg.domains modules: {domain_mods}. "
        "Core must not depend on any domain."
    )


def test_games_models_imports_core_base():
    """gpcg.domains.games.models must import Base from gpcg.core.models.

    This verifies the dependency direction: Games depends on Core, not
    the reverse.
    """
    import gpcg.domains.games.models as games_models

    from gpcg.core.models import Base

    # Games models must use the same Base
    assert issubclass(games_models.Game, Base)
    assert issubclass(games_models.GameplaySource, Base)


def test_kids_models_imports_core_base():
    """gpcg.domains.kids.models must import Base from gpcg.core.models.

    This verifies the dependency direction: Kids depends on Core, not
    the reverse.
    """
    import gpcg.domains.kids.models as kids_models

    from gpcg.core.models import Base

    # Kids models must use the same Base
    assert issubclass(kids_models.KidsTopic, Base)
    assert issubclass(kids_models.StoryAsset, Base)


def test_games_does_not_import_kids():
    """gpcg.domains.games must not import gpcg.domains.kids.

    Uses static source analysis to avoid SQLAlchemy table redefinition
    issues that occur when re-importing modules after clearing sys.modules.
    """
    import pkgutil
    from pathlib import Path

    games_pkg = importlib.import_module("gpcg.domains.games")
    games_dir = Path(games_pkg.__path__[0])

    forbidden = "gpcg.domains.kids"

    for py_file in games_dir.rglob("*.py"):
        content = py_file.read_text()
        # Check for import statements referencing the forbidden module
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if forbidden in stripped and ("import" in stripped):
                pytest.fail(
                    f"{py_file.name} imports {forbidden}: {stripped}"
                )


def test_kids_does_not_import_games():
    """gpcg.domains.kids must not import gpcg.domains.games.

    Uses static source analysis to avoid SQLAlchemy table redefinition
    issues that occur when re-importing modules after clearing sys.modules.
    """
    from pathlib import Path

    kids_pkg = importlib.import_module("gpcg.domains.kids")
    kids_dir = Path(kids_pkg.__path__[0])

    forbidden = "gpcg.domains.games"

    for py_file in kids_dir.rglob("*.py"):
        content = py_file.read_text()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if forbidden in stripped and ("import" in stripped):
                pytest.fail(
                    f"{py_file.name} imports {forbidden}: {stripped}"
                )


def test_all_tables_registered_when_all_imported():
    """When core, games, and kids models are imported, all 29 tables must be
    registered in Base.metadata. This ensures database.init_db() creates
    all tables correctly.
    """
    import gpcg.core.models  # noqa: F401
    import gpcg.domains.games.models  # noqa: F401
    import gpcg.domains.kids.models  # noqa: F401
    from gpcg.core.models import Base

    expected_tables = {
        # Core
        "users", "automations", "documents", "facts", "content_plans",
        "scripts", "workers", "jobs", "videos", "channel_profiles",
        "knowledge_chunks", "knowledge_items", "knowledge_item_embeddings",
        "knowledge_item_usages", "channel_profile_embeddings",
        "editorial_signals", "app_releases",
        # Games
        "games", "game_aliases", "gameplay_sources", "gameplay_downloads",
        "gameplay_assets", "gameplay_clip_usage", "gameplay_events",
        "gameplay_event_embeddings",
        # Kids
        "kids_topics", "story_assets", "kids_ideas", "asset_clip_usage",
        "kids_media_events",
    }

    actual_tables = set(Base.metadata.tables.keys())
    missing = expected_tables - actual_tables
    assert not missing, f"Missing tables: {missing}"
    assert len(actual_tables) == 30, f"Expected 30 tables, got {len(actual_tables)}"


def test_core_models_owns_core_entities():
    """Verify that core entities are in gpcg.core.models, not in games or kids."""
    from gpcg.core.models import (
        User, Automation, Document, Fact, ContentPlan, Script,
        Worker, Job, Video, ChannelProfile, KnowledgeChunk,
        KnowledgeItem, KnowledgeItemEmbedding, KnowledgeItemUsage,
        ChannelProfileEmbedding, EditorialSignal,
    )

    # Verify these are NOT in games models
    import gpcg.domains.games.models as games
    import gpcg.domains.kids.models as kids

    core_classes = {
        User, Automation, Document, Fact, ContentPlan, Script,
        Worker, Job, Video, ChannelProfile, KnowledgeChunk,
        KnowledgeItem, KnowledgeItemEmbedding, KnowledgeItemUsage,
        ChannelProfileEmbedding, EditorialSignal,
    }

    for cls in core_classes:
        assert not hasattr(games, cls.__name__), (
            f"{cls.__name__} should not be in gpcg.domains.games.models"
        )
        assert not hasattr(kids, cls.__name__), (
            f"{cls.__name__} should not be in gpcg.domains.kids.models"
        )


def test_games_models_owns_games_entities():
    """Verify that games entities are in gpcg.domains.games.models, not in core or kids."""
    from gpcg.domains.games.models import (
        Game, GameAlias, GameplaySource, GameplayDownload,
        GameplayAsset, GameplayClipUsage, GameplayEvent,
        GameplayEventEmbedding,
    )

    import gpcg.core.models as core
    import gpcg.domains.kids.models as kids

    games_classes = {
        Game, GameAlias, GameplaySource, GameplayDownload,
        GameplayAsset, GameplayClipUsage, GameplayEvent,
        GameplayEventEmbedding,
    }

    for cls in games_classes:
        assert not hasattr(core, cls.__name__), (
            f"{cls.__name__} should not be in gpcg.core.models"
        )
        assert not hasattr(kids, cls.__name__), (
            f"{cls.__name__} should not be in gpcg.domains.kids.models"
        )


def test_kids_models_owns_kids_entities():
    """Verify that kids entities are in gpcg.domains.kids.models, not in core or games."""
    from gpcg.domains.kids.models import KidsTopic, StoryAsset

    import gpcg.core.models as core
    import gpcg.domains.games.models as games

    kids_classes = {KidsTopic, StoryAsset}

    for cls in kids_classes:
        assert not hasattr(core, cls.__name__), (
            f"{cls.__name__} should not be in gpcg.core.models"
        )
        assert not hasattr(games, cls.__name__), (
            f"{cls.__name__} should not be in gpcg.domains.games.models"
        )
