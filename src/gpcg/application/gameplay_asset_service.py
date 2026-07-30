"""Gameplay asset service — manage reusable clips of gameplay sources.

MVP: clips are defined manually (start/end). The system reuses them across
many videos. Future: semantic scene detection + VLM descriptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from gpcg.domain.models import GameplayAsset, GameplaySource
from gpcg.infrastructure.media import MediaError, probe


@dataclass
class AssetCreate:
    source_id: int
    start_sec: float
    end_sec: float
    label: Optional[str] = None


class GameplayAssetService:
    """CRUD + selection for gameplay assets."""

    @staticmethod
    def create(session: Session, data: AssetCreate) -> GameplayAsset:
        source = session.get(GameplaySource, data.source_id)
        if source is None:
            raise ValueError(f"source #{data.source_id} not found")
        if data.start_sec < 0 or data.end_sec <= data.start_sec:
            raise ValueError(f"invalid range: start={data.start_sec} end={data.end_sec}")
        if data.end_sec > source.duration:
            raise ValueError(
                f"end {data.end_sec:.2f}s exceeds source duration {source.duration:.2f}s"
            )
        asset = GameplayAsset(
            source_id=data.source_id,
            label=data.label,
            start_sec=data.start_sec,
            end_sec=data.end_sec,
            duration=data.end_sec - data.start_sec,
        )
        session.add(asset)
        session.flush()
        return asset

    @staticmethod
    def list_for_game(session: Session, game_id: int) -> list[GameplayAsset]:
        """List all assets belonging to sources of a given game."""
        stmt = (
            select(GameplayAsset)
            .join(GameplaySource, GameplayAsset.source_id == GameplaySource.id)
            .where(GameplaySource.game_id == game_id)
            .order_by(GameplayAsset.id)
        )
        return list(session.execute(stmt).scalars().all())

    @staticmethod
    def list_for_source(session: Session, source_id: int) -> list[GameplayAsset]:
        return list(
            session.execute(
                select(GameplayAsset)
                .where(GameplayAsset.source_id == source_id)
                .order_by(GameplayAsset.start_sec)
            ).scalars().all()
        )

    @staticmethod
    def delete(session: Session, asset_id: int) -> bool:
        asset = session.get(GameplayAsset, asset_id)
        if asset is None:
            return False
        session.delete(asset)
        session.flush()
        return True

    @staticmethod
    def get(session: Session, asset_id: int) -> Optional[GameplayAsset]:
        return session.get(GameplayAsset, asset_id)
