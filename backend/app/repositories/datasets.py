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
    ) -> UUID:
        self.cur.execute(
            """
            INSERT INTO dataset
              (project_id, type, source, accuracy_score, date_processed, batch_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING dataset_id
            """,
            (str(project_id), dataset_type, source, accuracy_score, date_processed, str(batch_id)),
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


class LayerRepository:
    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur

    def insert(
        self, *, dataset_id: UUID, file_key: str, preview_key: str, crs: str,
        bounds: tuple[float, float, float, float], pixel_size_m: float,
        band_count: int, class_legend: dict[str, Any] | None,
    ) -> None:
        minx, miny, maxx, maxy = bounds
        self.cur.execute(
            """
            INSERT INTO spatial_layer
              (dataset_id, file_key, preview_key, crs,
               bbox_minx, bbox_miny, bbox_maxx, bbox_maxy, pixel_size_m, extent,
               band_count, class_legend)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                    ST_MakeEnvelope(%s, %s, %s, %s, 4326), %s, %s)
            """,
            (str(dataset_id), file_key, preview_key, crs,
             minx, miny, maxx, maxy, pixel_size_m, minx, miny, maxx, maxy,
             band_count, Jsonb(class_legend) if class_legend is not None else None),
        )

    def list_for_project(self, project_id: UUID | str) -> list[dict[str, Any]]:
        self.cur.execute(
            """
            SELECT sl.layer_id, sl.crs, sl.bbox_minx, sl.bbox_miny,
                   sl.bbox_maxx, sl.bbox_maxy, sl.pixel_size_m, sl.preview_key,
                   sl.cog_key, sl.band_count, sl.class_legend, d.type, d.date_processed
            FROM spatial_layer sl
            JOIN dataset d ON d.dataset_id = sl.dataset_id
            WHERE d.project_id = %s AND d.deleted_at IS NULL
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
            SELECT sl.layer_id, sl.dataset_id, sl.cog_key, sl.class_legend
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
        """
        self.cur.execute(
            """
            SELECT l.layer_id, k.metric_name, k.value, k.unit
            FROM kpi k
            JOIN dataset d ON d.dataset_id = k.dataset_id
            JOIN spatial_layer l ON l.dataset_id = d.dataset_id
            WHERE d.project_id = %s AND d.deleted_at IS NULL
            ORDER BY d.date_processed, k.metric_name
            """,
            (str(project_id),),
        )
        return list(self.cur.fetchall())

    def portfolio_totals(self) -> tuple[dict[str, float], int]:
        # Joins project too (not just dataset) and excludes p.deleted_at: a
        # soft-deleted project's dataset/kpi rows are untouched by design (no
        # cascade), so without this join their KPIs would keep inflating the
        # portfolio total after the project itself has vanished from every list.
        #
        # project_count is a SEPARATE query, not `rows[0]["project_count"]` off the
        # per-metric GROUP BY below (a pre-existing bug found while verifying the
        # above join: that took whichever metric_name happens to sort first
        # alphabetically as "the" count, which is only the distinct-project count
        # for THAT one metric - e.g. a project with no "class_area_class_0" pixels
        # was silently never counted at all, regardless of soft-delete).
        self.cur.execute(
            """
            SELECT k.metric_name, SUM(k.value) AS total
            FROM kpi k
            JOIN dataset d ON d.dataset_id = k.dataset_id
            JOIN project p ON p.project_id = d.project_id
            WHERE d.deleted_at IS NULL AND p.deleted_at IS NULL
              AND (k.metric_name LIKE 'class_area%%' OR k.metric_name = 'total_area')
            GROUP BY k.metric_name
            ORDER BY k.metric_name
            """
        )
        totals = {r["metric_name"]: float(r["total"]) for r in self.cur.fetchall()}

        self.cur.execute(
            """
            SELECT COUNT(DISTINCT d.project_id) AS project_count
            FROM kpi k
            JOIN dataset d ON d.dataset_id = k.dataset_id
            JOIN project p ON p.project_id = d.project_id
            WHERE d.deleted_at IS NULL AND p.deleted_at IS NULL
              AND (k.metric_name LIKE 'class_area%%' OR k.metric_name = 'total_area')
            """
        )
        count = int(self.cur.fetchone()["project_count"])
        return totals, count
