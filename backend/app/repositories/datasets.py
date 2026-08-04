"""Dataset / spatial-layer / KPI persistence.

Notably fixes the MVP's KPI duplication: re-ingesting a dataset used to append
duplicate KPI rows, and /summary SUM()med them, silently double-counting portfolio
totals. Here KPI writes are UPSERTs keyed on (dataset_id, metric_name)."""
from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb


class DatasetRepository:
    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur

    def list_for_project(self, project_id: UUID | str) -> list[dict[str, Any]]:
        self.cur.execute(
            "SELECT dataset_id, type, source, accuracy_score, date_processed, loaded_at "
            "FROM dataset WHERE project_id = %s AND deleted_at IS NULL "
            "ORDER BY loaded_at DESC",
            (str(project_id),),
        )
        return list(self.cur.fetchall())

    def insert(
        self, *, project_id: UUID, dataset_type: str, source: str,
        accuracy_score: float | None, date_processed: str, batch_id: UUID,
        is_reference: bool = False, is_adhoc: bool = False,
    ) -> UUID:
        self.cur.execute(
            """
            INSERT INTO dataset
              (project_id, type, source, accuracy_score, date_processed, batch_id,
               is_reference, is_adhoc)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING dataset_id
            """,
            (
                str(project_id), dataset_type, source, accuracy_score, date_processed,
                str(batch_id), is_reference, is_adhoc,
            ),
        )
        return self.cur.fetchone()["dataset_id"]  # type: ignore[index]

    def mark_failed_promotion(self, dataset_id: UUID | str) -> None:
        """Compensating action for IngestionService.ingest: the row's own
        transaction already committed, but promoting its artifacts to
        storage then failed - see that call site."""
        self.cur.execute(
            "UPDATE dataset SET deleted_at = now() WHERE dataset_id = %s",
            (str(dataset_id),),
        )

    def soft_delete_reference(self, dataset_id: UUID | str, deleted_by: UUID) -> bool:
        """Wave: Reference Layer Library - removing a reference layer soft-
        deletes its `dataset` row through the SAME `deleted_at` column every
        layer listing already filters on (LayerRepository.list_for_project),
        so it disappears from every project at once, not just one. The
        `is_reference = true` guard is deliberate: this method must never be
        reachable to soft-delete an ordinary project-scoped dataset - see
        ReferenceLayerService, the only caller."""
        self.cur.execute(
            """
            UPDATE dataset SET deleted_at = now(), deleted_by = %s
            WHERE dataset_id = %s AND is_reference = true AND deleted_at IS NULL
            """,
            (str(deleted_by), str(dataset_id)),
        )
        return self.cur.rowcount > 0

    def soft_delete_adhoc(self, dataset_id: UUID | str, deleted_by: UUID) -> bool:
        """Wave 3 (Added Layers) - removing an ad-hoc layer soft-deletes its
        `dataset` row the same way soft_delete_reference does, just guarded
        by `is_adhoc = true` instead: this method must never be reachable to
        soft-delete a formal, project-scoped dataset - see
        AdhocLayerService, the only caller."""
        self.cur.execute(
            """
            UPDATE dataset SET deleted_at = now(), deleted_by = %s
            WHERE dataset_id = %s AND is_adhoc = true AND deleted_at IS NULL
            """,
            (str(deleted_by), str(dataset_id)),
        )
        return self.cur.rowcount > 0

    def rename(self, dataset_id: UUID | str, display_name: str) -> None:
        """Rename-a-layer (Administrator-only). `display_name` lives here on
        `dataset`, not on `spatial_layer` - see LayerRenameService, the only
        caller."""
        self.cur.execute(
            "UPDATE dataset SET display_name = %s WHERE dataset_id = %s",
            (display_name, str(dataset_id)),
        )


class LayerRepository:
    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur

    def insert(
        self, *, dataset_id: UUID, file_key: str, preview_key: str, crs: str,
        bounds: tuple[float, float, float, float], pixel_size_m: float,
        band_count: int, class_legend: dict[str, Any] | None,
    ) -> UUID:
        minx, miny, maxx, maxy = bounds
        self.cur.execute(
            """
            INSERT INTO spatial_layer
              (dataset_id, file_key, preview_key, crs,
               bbox_minx, bbox_miny, bbox_maxx, bbox_maxy, pixel_size_m, extent,
               band_count, class_legend)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                    ST_MakeEnvelope(%s, %s, %s, %s, 4326), %s, %s)
            RETURNING layer_id
            """,
            (str(dataset_id), file_key, preview_key, crs,
             minx, miny, maxx, maxy, pixel_size_m, minx, miny, maxx, maxy,
             band_count, Jsonb(class_legend) if class_legend is not None else None),
        )
        return self.cur.fetchone()["layer_id"]  # type: ignore[index]

    def insert_non_raster(
        self, *, dataset_id: UUID, layer_kind: str, crs: str,
        bounds: tuple[float, float, float, float],
    ) -> UUID:
        """Wave: multi-format layers. A vector or external_wms/external_wfs
        row has no file/preview/pixel_size/band_count/class_legend at all -
        those stay NULL, exactly like a raster row before its COG exists,
        just permanently rather than transiently."""
        minx, miny, maxx, maxy = bounds
        self.cur.execute(
            """
            INSERT INTO spatial_layer (dataset_id, crs, bbox_minx, bbox_miny,
                                        bbox_maxx, bbox_maxy, extent, layer_kind)
            VALUES (%s, %s, %s, %s, %s, %s, ST_MakeEnvelope(%s, %s, %s, %s, 4326), %s)
            RETURNING layer_id
            """,
            (str(dataset_id), crs, minx, miny, maxx, maxy, minx, miny, maxx, maxy, layer_kind),
        )
        return self.cur.fetchone()["layer_id"]  # type: ignore[index]

    def list_for_project(self, project_id: UUID | str) -> list[dict[str, Any]]:
        # Wave: Reference Layer Library - `OR d.is_reference = true` is what
        # makes a reference layer show up on EVERY project's Layers panel,
        # not just the shared library project it's physically attached to
        # (see app.services.project_access.resolve_reference_library_project).
        # A project's OWN reference layer (if this IS the library project)
        # matches both halves of the OR on the same row, never double-counted.
        self.cur.execute(
            """
            SELECT sl.layer_id, sl.layer_kind, sl.crs, sl.bbox_minx, sl.bbox_miny,
                   sl.bbox_maxx, sl.bbox_maxy, sl.pixel_size_m, sl.preview_key,
                   sl.cog_key, sl.band_count, sl.class_legend, d.type, d.date_processed,
                   d.source, d.display_name, d.accuracy_score, d.is_reference, d.is_adhoc
            FROM spatial_layer sl
            JOIN dataset d ON d.dataset_id = sl.dataset_id
            WHERE d.deleted_at IS NULL AND (d.project_id = %s OR d.is_reference = true)
            ORDER BY d.loaded_at DESC
            """,
            (str(project_id),),
        )
        return list(self.cur.fetchall())

    def get_for_dataset(self, dataset_id: UUID | str) -> dict[str, Any] | None:
        """Phase 3: the ingest job needs the layer it just created (for its
        file_key, to convert to a COG) after `IngestionService.ingest()` has
        already returned - it only hands back an `IngestResult` (project/dataset
        ids + stats), not the layer row, so this is the lookup back to it."""
        self.cur.execute(
            "SELECT layer_id, file_key, cog_key FROM spatial_layer "
            "WHERE dataset_id = %s",
            (str(dataset_id),),
        )
        return self.cur.fetchone()

    def get(self, layer_id: UUID | str) -> dict[str, Any] | None:
        # Joins dataset/project and excludes both deleted_at columns: without
        # this, soft-deleting a project (which never cascades - see
        # ProjectService.delete_project) left its COG readable forever via
        # any endpoint that resolves a layer directly by layer_id (tile
        # rendering, pixel inspect), even though the project itself 404s
        # everywhere else. Both callers of this method (get_cog_key,
        # get_render_context) go through this one query, so this single fix
        # closes the gap for both.
        self.cur.execute(
            """
            SELECT sl.layer_id, sl.dataset_id, sl.layer_kind, sl.cog_key, sl.file_key,
                   sl.class_legend, p.project_id
            FROM spatial_layer sl
            JOIN dataset d ON d.dataset_id = sl.dataset_id
            JOIN project p ON p.project_id = d.project_id
            WHERE sl.layer_id = %s AND d.deleted_at IS NULL AND p.deleted_at IS NULL
            """,
            (str(layer_id),),
        )
        return self.cur.fetchone()

    def set_cog_key(self, layer_id: UUID | str, cog_key: str) -> None:
        self.cur.execute(
            "UPDATE spatial_layer SET cog_key = %s WHERE layer_id = %s",
            (cog_key, str(layer_id)),
        )

    def update_legend(self, layer_id: UUID | str, class_legend: dict[str, Any]) -> None:
        """Wave: editable class legend. Overwrites the persisted legend for an
        already-ingested layer - tile rendering (TileService.get_render_context)
        and the pixel-inspect popup both read this same column live on their
        next request, so the change is immediately visible with no
        re-ingestion. See ClassLegendService.update_legend, the only caller -
        it also recomputes and re-persists this layer's KPIs in the same
        transaction, so the legend and its area numbers never go out of sync."""
        self.cur.execute(
            "UPDATE spatial_layer SET class_legend = %s WHERE layer_id = %s",
            (Jsonb(class_legend), str(layer_id)),
        )


class KpiRepository:
    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur

    def upsert(self, dataset_id: UUID, metric_name: str, value: float, unit: str) -> None:
        self.cur.execute(
            """
            INSERT INTO kpi (dataset_id, metric_name, value, unit)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (dataset_id, metric_name)
              DO UPDATE SET value = EXCLUDED.value, unit = EXCLUDED.unit, computed_at = now()
            """,
            (str(dataset_id), metric_name, value, unit),
        )

    def for_dataset(self, dataset_id: UUID | str) -> dict[str, float]:
        """Every current KPI value for one dataset, keyed by metric_name -
        the "before" snapshot ClassLegendService.update_legend needs to audit
        a legend edit: the layer's own already-persisted numbers (what every
        dashboard was showing a moment ago), not a re-derivation of them."""
        self.cur.execute(
            "SELECT metric_name, value FROM kpi WHERE dataset_id = %s", (str(dataset_id),)
        )
        return {r["metric_name"]: float(r["value"]) for r in self.cur.fetchall()}

    def delete_class_metrics(self, dataset_id: UUID | str) -> None:
        """Wipes every per-class KPI row for this dataset (metric_name LIKE
        'class_area_%') before a legend edit re-inserts fresh ones from the
        new legend's own recomputed stats (see ClassLegendService.
        update_legend, the only caller) - a class just removed from the
        legend must not leave its old, now-stale area behind as an orphaned
        row that would otherwise keep inflating portfolio_totals() forever."""
        self.cur.execute(
            "DELETE FROM kpi WHERE dataset_id = %s AND metric_name LIKE 'class_area_%%'",
            (str(dataset_id),),
        )

    def for_project(self, project_id: UUID | str) -> list[dict[str, Any]]:
        """Every KPI row for this project, each tagged with the real
        `layer_id` it belongs to (Phase 3 Wave G) - not just flattened by
        metric_name. A dataset always has exactly one spatial_layer row in
        this app's ingest model (both written in the same transaction - see
        IngestionService.ingest), so this join is exact, not a guess; keying
        by layer_id (rather than the kpi table's own dataset_id FK) lets a
        caller join directly against GET /projects/{id}/layers's layer_id
        with no extra correlation field needed.

        Bugfix: the old query selected only metric_name/value/unit with no
        per-dataset attribution at all - ProjectService.get_kpis then merged
        every dataset's rows into ONE flat dict keyed by metric_name, so a
        project with 2+ layers sharing a metric name (e.g. every classified
        layer has its own "total_area") silently lost all but one layer's
        numbers to dict-key collision.

        Wave 3 (Added Layers): `AND NOT d.is_adhoc` - an ad-hoc layer's KPIs
        (it still gets a plain total_area row like any ingest) must never
        surface in a project's official Key Metrics or feed Landscape
        Evolution's per-class numbers, so they're excluded at the source
        here rather than trusted to every caller to filter themselves.
        """
        self.cur.execute(
            """
            SELECT l.layer_id, k.metric_name, k.value, k.unit
            FROM kpi k
            JOIN dataset d ON d.dataset_id = k.dataset_id
            JOIN spatial_layer l ON l.dataset_id = d.dataset_id
            WHERE d.project_id = %s AND d.deleted_at IS NULL AND NOT d.is_adhoc
            ORDER BY d.date_processed, k.metric_name
            """,
            (str(project_id),),
        )
        return list(self.cur.fetchall())

    def portfolio_totals(
        self, *, member_id: UUID | str | None = None
    ) -> tuple[dict[str, float], int]:
        # Joins project too (not just dataset) and excludes p.deleted_at: a
        # soft-deleted project's dataset/kpi rows are untouched by design (no
        # cascade), so without this join their KPIs would keep inflating the
        # portfolio total after the project itself has vanished from every list.
        #
        # `member_id` is None for an Administrator (every project, no filter -
        # see ProjectService.portfolio_summary) or a user_id to restrict the
        # totals to projects that user has a live membership row on (Wave:
        # project-level RBAC) - same member_join shape ProjectRepository.
        # list_paginated already uses for the same "scope this listing to
        # what the caller can see" need.
        #
        # project_count is a SEPARATE query, not `rows[0]["project_count"]` off the
        # per-metric GROUP BY below (a pre-existing bug found while verifying the
        # above join: that took whichever metric_name happens to sort first
        # alphabetically as "the" count, which is only the distinct-project count
        # for THAT one metric - e.g. a project with no "class_area_class_0" pixels
        # was silently never counted at all, regardless of soft-delete).
        member_join = ""
        params: list[Any] = []
        if member_id is not None:
            member_join = (
                "JOIN project_membership pm ON pm.project_id = p.project_id "
                "AND pm.user_id = %s AND pm.removed_at IS NULL"
            )
            params.append(str(member_id))

        # member_join is always one of the two fixed literals above - never
        # derived from `member_id`'s value (which travels only as a bound %s
        # parameter) - so this f-string splices in a constant clause, not
        # attacker-reachable SQL (same note as ProjectRepository.list_paginated).
        self.cur.execute(
            f"""
            SELECT k.metric_name, SUM(k.value) AS total
            FROM kpi k
            JOIN dataset d ON d.dataset_id = k.dataset_id
            JOIN project p ON p.project_id = d.project_id
            {member_join}
            WHERE d.deleted_at IS NULL AND p.deleted_at IS NULL
              AND (k.metric_name LIKE 'class_area%%' OR k.metric_name = 'total_area')
            GROUP BY k.metric_name
            ORDER BY k.metric_name
            """,  # noqa: S608
            tuple(params),
        )
        totals = {r["metric_name"]: float(r["total"]) for r in self.cur.fetchall()}

        self.cur.execute(
            f"""
            SELECT COUNT(DISTINCT d.project_id) AS project_count
            FROM kpi k
            JOIN dataset d ON d.dataset_id = k.dataset_id
            JOIN project p ON p.project_id = d.project_id
            {member_join}
            WHERE d.deleted_at IS NULL AND p.deleted_at IS NULL
              AND (k.metric_name LIKE 'class_area%%' OR k.metric_name = 'total_area')
            """,  # noqa: S608
            tuple(params),
        )
        count = int(self.cur.fetchone()["project_count"])
        return totals, count
