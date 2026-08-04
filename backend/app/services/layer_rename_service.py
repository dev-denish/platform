"""Rename-a-layer (Administrator-only display_name override).

Deliberately minimal, same shape as ReferenceLayerService.remove: resolve
layer_id -> dataset_id, one UPDATE, one audit entry. No project-tier
re-check (RENAME_LAYER_ROLES is a global-only gate, see enums.py) and no
heavy work off-transaction (unlike ClassLegendService, there is no raster to
re-read - a display label never affects computed stats)."""
from __future__ import annotations

from uuid import UUID

from app.core.db import Database
from app.core.errors import NotFoundError
from app.domain.dtos import CurrentUser, LayerRenameResult
from app.domain.enums import AuditAction
from app.repositories.audit import AuditRepository
from app.repositories.datasets import DatasetRepository, LayerRepository


class LayerRenameService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def rename(
        self, layer_id: UUID, display_name: str, actor: CurrentUser
    ) -> LayerRenameResult:
        with self.db.transaction() as cur:
            layer = LayerRepository(cur).get(layer_id)
            if layer is None:
                raise NotFoundError("No such layer.")
            DatasetRepository(cur).rename(layer["dataset_id"], display_name)
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.RENAME_LAYER, target=str(layer_id),
                detail=f"Renamed layer {layer_id} to '{display_name}'.",
            )
        return LayerRenameResult(layer_id=layer_id, display_name=display_name)
