"""Dataset / spatial-layer / KPI persistence.

Notably fixes the MVP's KPI duplication: re-ingesting a dataset used to append
duplicate KPI rows, and /summary SUM()med them, silently double-counting portfolio
totals. Here KPI writes are UPSERTs keyed on (dataset_id, metric_name)."""
from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg


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
        accuracy_score: float, date_processed: str, batch_id: UUID,
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


class LayerRepository:
    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur

    def insert(
        self, *, dataset_id: UUID, file_key: str, preview_key: str, crs: str,
        bounds: tuple[float, float, float, float], pixel_size_m: float,
    ) -> None:
        minx, miny, maxx, maxy = bounds
        self.cur.execute(
            """
            INSERT INTO spatial_layer
              (dataset_id, file_key, preview_key, crs,
               bbox_minx, bbox_miny, bbox_maxx, bbox_maxy, pixel_size_m, extent)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                    ST_MakeEnvelope(%s, %s, %s, %s, 4326))
            """,
            (str(dataset_id), file_key, preview_key, crs,
             minx, miny, maxx, maxy, pixel_size_m, minx, miny, maxx, maxy),
        )

    def list_for_project(self, project_id: UUID | str) -> list[dict[str, Any]]:
        self.cur.execute(
            """
            SELECT sl.layer_id, sl.crs, sl.bbox_minx, sl.bbox_miny,
                   sl.bbox_maxx, sl.bbox_maxy, sl.pixel_size_m, sl.preview_key,
                   d.type, d.date_processed
            FROM spatial_layer sl
            JOIN dataset d ON d.dataset_id = sl.dataset_id
            WHERE d.project_id = %s AND d.deleted_at IS NULL
            ORDER BY d.loaded_at DESC
            """,
            (str(project_id),),
        )
        return list(self.cur.fetchall())


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
        self.cur.execute(
            """
            SELECT k.metric_name, k.value, k.unit
            FROM kpi k
            JOIN dataset d ON d.dataset_id = k.dataset_id
            WHERE d.project_id = %s AND d.deleted_at IS NULL
            ORDER BY k.metric_name
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
