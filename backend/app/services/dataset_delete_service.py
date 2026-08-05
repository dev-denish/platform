"""Delete-a-dataset (Administrator-only) - permanent removal of a formal,
project-scoped dataset/layer: the thing that came through the real upload/
ingestion pipeline, as opposed to a reference layer (ReferenceLayerService)
or an ad-hoc quick-add (AdhocLayerService), which already have their own
removal paths and are explicitly out of scope here (soft_delete_dataset's
`is_reference = false AND is_adhoc = false` guard is what actually enforces
that, not a check in this file).

Two things happen, in this order and for a reason: the DB row is soft-
deleted (deleted_at, same convention as every other removal in this app -
keeps the audit trail load-bearing and the row available for
dataset_label's read-time resolution) and committed FIRST; only then is the
underlying storage file(s) actually unlinked. Storage deletion is real and
irreversible but not transactional the way a DB row is - if it fails (a
transient S3 error, say), the user-facing delete must still have succeeded
(the row is gone from every listing, deleted_at is set), so this logs a
warning rather than raising and leaving the row un-deleted over a storage
hiccup. Ops can always find and clean up an orphaned key later from the
warning log; a user cannot recover a dataset the app told them was deleted
but silently wasn't."""
from __future__ import annotations

from uuid import UUID

from app.core.db import Database
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.domain.dtos import CurrentUser
from app.domain.enums import AuditAction
from app.repositories.audit import AuditRepository
from app.repositories.datasets import DatasetRepository, LayerRepository, dataset_label
from app.repositories.vector_layers import VectorFeatureRepository
from app.services.ingestion.storage import Storage

log = get_logger("dmrv.dataset_delete")


class DatasetDeleteService:
    def __init__(self, db: Database, storage: Storage) -> None:
        self.db = db
        self.storage = storage

    def remove(self, layer_id: UUID, actor: CurrentUser) -> None:
        with self.db.transaction() as cur:
            layer = LayerRepository(cur).get(layer_id)
            if layer is None:
                raise NotFoundError("No such layer.")
            removed = DatasetRepository(cur).soft_delete_dataset(
                layer["dataset_id"], deleted_by=actor.user_id
            )
            if not removed:
                # Either a reference/ad-hoc layer (out of scope for this
                # endpoint - see module docstring), already removed, or
                # never existed - same 404 either way, not a 403 that would
                # confirm which kind it actually is.
                raise NotFoundError("No such dataset.")
            if layer["layer_kind"] == "vector":
                VectorFeatureRepository(cur).delete_for_layer(layer_id)
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.DELETE_DATASET, target=str(layer["dataset_id"]),
                detail=f"Removed dataset '{dataset_label(layer)}'.",
                project_id=layer["project_id"],
            )

        for key in (layer["file_key"], layer["cog_key"], layer["preview_key"]):
            if not key:
                continue
            try:
                self.storage.delete(key)
            except Exception:
                log.warning("dataset_delete.storage_cleanup_failed", key=key, layer_id=str(layer_id))
