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
