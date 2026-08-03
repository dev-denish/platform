"""Removing an ad-hoc layer (Wave 3: Added Layers).

Adding one is not a separate code path at all - it's IngestionService.ingest,
exactly as for any project-scoped layer, just flagged `is_adhoc=True` and
targeting an already-known project_id (see
app.api.v1.adhoc_layers.upload_adhoc_layer). Removal mirrors
ReferenceLayerService.remove: resolve layer_id -> dataset_id (+ project_id,
for the same project-scoped upload-role re-check adding one uses), then the
same soft-delete + audit-log shape every other removal in this app uses."""
from __future__ import annotations

from uuid import UUID

from app.core.db import Database
from app.core.errors import NotFoundError
from app.domain.authz import require_project_upload
from app.domain.dtos import CurrentUser
from app.domain.enums import AuditAction
from app.repositories.audit import AuditRepository
from app.repositories.datasets import DatasetRepository, LayerRepository


class AdhocLayerService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def remove(self, layer_id: UUID, actor: CurrentUser) -> None:
        with self.db.transaction() as cur:
            layer = LayerRepository(cur).get(layer_id)
            if layer is None:
                raise NotFoundError("No such layer.")
            # Project-scoped, not MANAGE_REFERENCE_LAYERS_ROLES-style global
            # Administrator-only: an ad-hoc layer is a normal project
            # resource, so whoever can upload one (GIS Associate on this
            # project, or Administrator) can also remove it.
            require_project_upload(cur, layer["project_id"], actor)
            removed = DatasetRepository(cur).soft_delete_adhoc(
                layer["dataset_id"], deleted_by=actor.user_id
            )
            if not removed:
                # Either never an ad-hoc layer, or already removed - same 404
                # either way, not a 403 that would confirm a non-ad-hoc
                # layer's existence at this endpoint.
                raise NotFoundError("No such ad-hoc layer.")
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.DELETE_DATASET, target=str(layer["dataset_id"]),
                detail=f"Removed ad-hoc layer {layer_id} (dataset {layer['dataset_id']}).",
            )
