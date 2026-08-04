"""Removing a reference layer (Wave: Reference Layer Library).

Adding one is not a separate code path at all - it's IngestionService.ingest
or WmsService.create_external_layer, exactly as for any project-scoped
layer, just flagged `is_reference=True` (see those modules and
app.services.project_access.resolve_reference_library_project). Removal has
no existing home to reuse, since this app has never had a per-layer delete
before (only whole-project soft-delete) - this is the one genuinely new
piece, and it's deliberately small: resolve layer_id -> dataset_id, then the
same soft-delete + audit-log shape every other removal in this app uses
(see ProjectService.delete_project, UserService.deactivate_user)."""
from __future__ import annotations

from uuid import UUID

from app.core.db import Database
from app.core.errors import NotFoundError
from app.domain.dtos import CurrentUser
from app.domain.enums import AuditAction
from app.repositories.audit import AuditRepository
from app.repositories.datasets import DatasetRepository, LayerRepository


class ReferenceLayerService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def remove(self, layer_id: UUID, actor: CurrentUser) -> None:
        with self.db.transaction() as cur:
            layer = LayerRepository(cur).get(layer_id)
            if layer is None:
                raise NotFoundError("No such layer.")
            dataset_id = layer["dataset_id"]
            removed = DatasetRepository(cur).soft_delete_reference(
                dataset_id, deleted_by=actor.user_id
            )
            if not removed:
                # Either never a reference layer, or already removed by someone
                # else - same 404 either way, not a 403 that would confirm a
                # non-reference layer's existence at this endpoint.
                raise NotFoundError("No such reference layer.")
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.DELETE_DATASET, target=str(dataset_id),
                detail=f"Removed reference layer {layer_id} (dataset {dataset_id}).",
                project_id=layer["project_id"],
            )
