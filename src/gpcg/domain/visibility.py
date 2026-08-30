"""Visibility filters for the hybrid content pool model (REFACTORY_V2).

The GPCG uses a hybrid ownership/visibility model for content entities
(Fact, Document, KnowledgeItem, GameplaySource):

- ``user_id IS NULL``  → system-collected (shared pool, visible to all users)
- ``user_id == X AND is_public = False`` → private to owner X
- ``user_id == X AND is_public = True``  → shared with other users

This module provides reusable SQLAlchemy filter expressions that enforce
this model, so every query site applies the same rule consistently.

See docs/REFACTORY_V2_DIAGNOSTIC.md §I.1 for the decision rationale.
"""

from __future__ import annotations

from typing import TypeVar

from sqlalchemy import ColumnElement, or_

T = TypeVar("T")


def visible_to_user(user_id_col, is_public_col, consumer_user_id: int | None) -> ColumnElement[bool]:
    """Return a WHERE clause that filters rows visible to ``consumer_user_id``.

    A row is visible when ANY of:
      - it has no owner (``user_id IS NULL`` — system-collected, shared pool)
      - it belongs to the consumer (``user_id == consumer_user_id``)
      - it is explicitly public (``is_public == True``)

    Rows owned by another user with ``is_public = False`` are excluded.

    If ``consumer_user_id`` is None (no user context — e.g. legacy CLI),
    only the shared pool (``user_id IS NULL``) and explicitly public rows
    are visible. This is the safe default.
    """
    if consumer_user_id is None:
        return or_(user_id_col.is_(None), is_public_col.is_(True))
    return or_(
        user_id_col.is_(None),
        user_id_col == consumer_user_id,
        is_public_col.is_(True),
    )


# ── Gameplay visibility ──────────────────────────────────────────────────────


def gameplay_visible_to_user(
    user_id_col,
    is_public_col,
    consumer_user_id: int,
    *,
    allows_public: bool,
):
    """Return a WHERE clause filtering GameplaySource rows visible to a user.

    GameplaySources don't use the ``user_id IS NULL`` system-pool model —
    every source has an owner. Access to other users' gameplays is gated
    by the consumer's automation config (``fallback_policy=allow_public``
    or ``accept_public_gameplays=true``).

    When ``allows_public`` is True, the user can see:
      - their own sources (``user_id == consumer_user_id``)
      - public sources from anyone (``is_public == True``)

    When ``allows_public`` is False, only their own sources are visible.
    """
    if allows_public:
        return or_(
            user_id_col == consumer_user_id,
            is_public_col.is_(True),
        )
    return user_id_col == consumer_user_id


def user_allows_public_gameplays(config: dict | None) -> bool:
    """Check if the user's automation config allows public gameplay fallback.

    Returns True when ``fallback_policy == "allow_public"`` OR
    ``accept_public_gameplays is True``.
    """
    if not config:
        return False
    return (
        config.get("fallback_policy") == "allow_public"
        or config.get("accept_public_gameplays") is True
    )
