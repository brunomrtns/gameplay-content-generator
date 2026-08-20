"""Architecture tests — enforce Core/Domain boundary.

These tests verify that the Core layer does not depend on the Games domain
layer. They fail when a prohibited dependency is introduced.

Approved rules:
1. gpcg.core.models must not import gpcg.domains.games.models
2. gpcg.core package must not import from gpcg.domains
3. Importing gpcg.core.models does not pull in gpcg.domains.games.models
"""

from __future__ import annotations

import importlib
import sys


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
        "Core must not depend on Games domain."
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


def test_all_tables_registered_when_both_imported():
    """When both core and games models are imported, all 24 tables must be
    registered in Base.metadata. This ensures database.init_db() creates
    all tables correctly.
    """
    import gpcg.core.models  # noqa: F401
    import gpcg.domains.games.models  # noqa: F401
    from gpcg.core.models import Base

    expected_tables = {
        # Core
        "users", "automations", "documents", "facts", "content_plans",
        "scripts", "workers", "jobs", "videos", "channel_profiles",
        "knowledge_chunks", "knowledge_items", "knowledge_item_embeddings",
        "knowledge_item_usages", "channel_profile_embeddings",
        "editorial_signals",
        # Games
        "games", "game_aliases", "gameplay_sources", "gameplay_downloads",
        "gameplay_assets", "gameplay_clip_usage", "gameplay_events",
        "gameplay_event_embeddings",
    }

    actual_tables = set(Base.metadata.tables.keys())
    missing = expected_tables - actual_tables
    assert not missing, f"Missing tables: {missing}"
    assert len(actual_tables) == 24, f"Expected 24 tables, got {len(actual_tables)}"


def test_core_models_owns_core_entities():
    """Verify that core entities are in gpcg.core.models, not in games."""
    from gpcg.core.models import (
        User, Automation, Document, Fact, ContentPlan, Script,
        Worker, Job, Video, ChannelProfile, KnowledgeChunk,
        KnowledgeItem, KnowledgeItemEmbedding, KnowledgeItemUsage,
        ChannelProfileEmbedding, EditorialSignal,
    )

    # Verify these are NOT in games models
    import gpcg.domains.games.models as games

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


def test_games_models_owns_games_entities():
    """Verify that games entities are in gpcg.domains.games.models, not in core."""
    from gpcg.domains.games.models import (
        Game, GameAlias, GameplaySource, GameplayDownload,
        GameplayAsset, GameplayClipUsage, GameplayEvent,
        GameplayEventEmbedding,
    )

    import gpcg.core.models as core

    games_classes = {
        Game, GameAlias, GameplaySource, GameplayDownload,
        GameplayAsset, GameplayClipUsage, GameplayEvent,
        GameplayEventEmbedding,
    }

    for cls in games_classes:
        assert not hasattr(core, cls.__name__), (
            f"{cls.__name__} should not be in gpcg.core.models"
        )
