"""Analysis-result cache persistence + project-boundary lookup
(Wave: GEE analysis registry).

Geometry construction happens IN PostGIS (ST_Union/ST_AsGeoJSON), not in
Python - same convention as app/repositories/vector_layers.py."""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb


class AnalysisResultRepository:
    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur

    def get_project_boundary_geojson(self, project_id: UUID | str) -> dict[str, Any] | None:
        """The project's most-recently-uploaded `Boundary`-type vector layer,
        all its features unioned into one geometry. None if the project has
        no boundary layer yet - callers must treat that as "can't compute
        yet", not silently query all of GEE with no AOI."""
        self.cur.execute(
            """
            WITH boundary_layer AS (
                SELECT sl.layer_id
                FROM spatial_layer sl
                JOIN dataset d ON d.dataset_id = sl.dataset_id
                WHERE d.project_id = %s AND d.type = 'Boundary' AND d.deleted_at IS NULL
                ORDER BY d.loaded_at DESC
                LIMIT 1
            )
            SELECT ST_AsGeoJSON(ST_Union(vf.geom)) AS geom
            FROM vector_feature vf
            JOIN boundary_layer bl ON bl.layer_id = vf.layer_id
            """,
            (str(project_id),),
        )
        row = self.cur.fetchone()
        if row is None or row["geom"] is None:
            return None
        return json.loads(row["geom"])

    def get(self, project_id: UUID | str, analysis_id: str) -> dict[str, Any] | None:
        self.cur.execute(
            """
            SELECT project_id, analysis_id, computed_at, stats, legend, tile_url_template
            FROM analysis_result
            WHERE project_id = %s AND analysis_id = %s
            """,
            (str(project_id), analysis_id),
        )
        return self.cur.fetchone()

    def list_for_project(self, project_id: UUID | str) -> dict[str, Any]:
        """{analysis_id: computed_at} for every analysis this project has a
        cached result for - just enough for the catalog list to show "last
        computed" per row without fetching each full stats/legend blob."""
        self.cur.execute(
            "SELECT analysis_id, computed_at FROM analysis_result WHERE project_id = %s",
            (str(project_id),),
        )
        return {r["analysis_id"]: r["computed_at"] for r in self.cur.fetchall()}

    def upsert(
        self,
        *,
        project_id: UUID | str,
        analysis_id: str,
        computed_by: UUID | str,
        stats: dict[str, Any],
        legend: list[dict[str, Any]] | None,
        tile_url_template: str | None,
    ) -> dict[str, Any]:
        self.cur.execute(
            """
            INSERT INTO analysis_result
              (project_id, analysis_id, computed_at, computed_by, stats, legend, tile_url_template)
            VALUES (%s, %s, now(), %s, %s, %s, %s)
            ON CONFLICT (project_id, analysis_id) DO UPDATE SET
              computed_at = now(),
              computed_by = EXCLUDED.computed_by,
              stats = EXCLUDED.stats,
              legend = EXCLUDED.legend,
              tile_url_template = EXCLUDED.tile_url_template
            RETURNING project_id, analysis_id, computed_at, stats, legend, tile_url_template
            """,
            (
                str(project_id), analysis_id, str(computed_by),
                Jsonb(stats), Jsonb(legend) if legend is not None else None, tile_url_template,
            ),
        )
        row = self.cur.fetchone()
        assert row is not None
        return row
