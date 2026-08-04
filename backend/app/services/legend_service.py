"""Editable class legend, post-upload (Wave: editable class legend).

The upload-time Class Legend Builder already lets someone define a legend
before ingest; this is the same capability for a layer that's already been
ingested - add a value, remove one, rename/recolor an existing one - without
re-uploading the source file.

Mirrors IngestionService's own two-phase shape: the heavy raster read
(`compute_stats`, re-reading real pixel data from the layer's actual stored
COG/raster) runs UNGUARDED, then one transaction writes the new legend, the
recomputed KPI rows, and the audit entry together. Tile rendering
(TileService) and every KPI-driven read (ProjectService.get_kpis/
get_evolution, portfolio_totals) already query `spatial_layer.class_legend`/
`kpi` live on every request - there is nothing else to invalidate or
propagate; the moment this transaction commits, every one of those reflects
the new numbers.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import NotFoundError, UnprocessableError, ValidationError
from app.domain.authz import require_project_upload
from app.domain.dtos import ClassLegendUpdateResult, CurrentUser
from app.domain.enums import AuditAction
from app.repositories.audit import AuditRepository
from app.repositories.datasets import KpiRepository, LayerRepository
from app.services.ingestion import raster as R
from app.services.ingestion.storage import Storage


def _entry_label(entry: dict[str, str] | str | None) -> str | None:
    if isinstance(entry, dict):
        return entry.get("label")
    if isinstance(entry, str):
        return entry
    return None


def _summarize_legend_diff(old_legend: dict[str, Any], new_legend: dict[str, Any]) -> str:
    """Human-readable "what changed" for the audit `detail` column: which
    pixel values were added, removed, or relabeled/recolored - not just a
    dump of the two full legends."""
    old_keys, new_keys = set(old_legend), set(new_legend)
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    changed = sorted(
        k for k in old_keys & new_keys
        if old_legend[k] != new_legend[k]
    )
    parts = []
    if added:
        parts.append(f"added value(s) {added}")
    if removed:
        parts.append(f"removed value(s) {removed}")
    if changed:
        parts.append(f"relabeled/recolored value(s) {changed}")
    return "; ".join(parts) if parts else "no legend keys changed"


class ClassLegendService:
    def __init__(self, db: Database, settings: Settings, storage: Storage) -> None:
        self.db = db
        self.settings = settings
        self.storage = storage

    def update_legend(
        self, layer_id: UUID, new_legend: dict[str, Any], actor: CurrentUser
    ) -> ClassLegendUpdateResult:
        with self.db.connection() as conn, conn.cursor() as cur:
            layer = LayerRepository(cur).get(layer_id)
            if layer is None:
                raise NotFoundError("No such layer.")
            if layer["layer_kind"] != "raster":
                raise ValidationError("Only a raster layer's class legend can be edited.")
            # Same project-scoped re-check IngestionService/AdhocLayerService
            # apply to any other layer-modifying action: Administrator, or
            # GIS Associate on THIS project.
            require_project_upload(cur, layer["project_id"], actor)
            raster_key = layer["cog_key"] or layer["file_key"]
            if not raster_key:
                raise UnprocessableError(
                    "This layer has no stored raster to recompute stats from yet."
                )

        # Heavy, bounded raster read - re-reads the layer's actual stored
        # raster/COG (never a cosmetic legend-only update), off the DB
        # transaction below, same "raster work first, one transaction for
        # every row after" shape IngestionService.ingest uses.
        raster_path = self.storage.local_path_for_processing(raster_key)
        try:
            stats = R.compute_stats(
                raster_path, new_legend or None, block=self.settings.raster_window_size
            )
        except Exception as e:  # rasterio/GDAL failure -> client-safe 422
            raise UnprocessableError(f"Raster could not be processed: {e}") from e

        with self.db.transaction() as cur:
            layer = LayerRepository(cur).get(layer_id)
            if layer is None:
                raise NotFoundError("No such layer.")
            dataset_id = layer["dataset_id"]
            old_legend = layer["class_legend"] or {}

            kpis = KpiRepository(cur)
            before = kpis.for_dataset(dataset_id)

            LayerRepository(cur).update_legend(layer_id, new_legend)
            kpis.delete_class_metrics(dataset_id)
            kpis.upsert(dataset_id, "total_area", stats.total_area_ha, "ha")
            if stats.class_area_ha is not None:
                for label, area in stats.class_area_ha.items():
                    kpis.upsert(dataset_id, R.metric_key(label), area, "ha")

            after = kpis.for_dataset(dataset_id)
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.UPDATE_CLASS_LEGEND, target=str(layer_id),
                detail=(
                    f"{_summarize_legend_diff(old_legend, new_legend)}; "
                    f"Total Area {before.get('total_area', 0.0)} ha -> "
                    f"{after.get('total_area', 0.0)} ha; "
                    f"per-class before {before}; per-class after {after}"
                ),
                project_id=layer["project_id"],
            )

        return ClassLegendUpdateResult(
            layer_id=layer_id, class_legend=new_legend,
            total_area_ha=stats.total_area_ha, class_area_ha=stats.class_area_ha,
        )
