"""Legacy module — models have been split into core and games domains.

Core models: ``gpcg.core.models``
Games models: ``gpcg.domains.games.models``

This module is intentionally empty. Do not add re-exports here.
Both modules must be imported before any ``create_all()`` or query is
executed. ``database.init_db()`` handles this.
"""
