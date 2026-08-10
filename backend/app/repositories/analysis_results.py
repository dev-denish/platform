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

    def boundary_contains_point(self, project_id: UUID | str, lon: float, lat: float) -> bool:
        """Wave: AOI clip. True only if the project's unioned Boundary-layer
        geometry actually contains this point - callers must gate a GEE
        point-query with this BEFORE evaluating GEE at the raw coordinate,
        since a clipped map tile now legitimately renders nothing outside
        the boundary and a point-query that ignored that would silently
        return a real value for a click on visually-empty area. A second,
        separate query from get_project_boundary_geojson (not folded into
        it) - callers that don't have a lon/lat (refresh()'s own boundary
        lookup) keep using that one unchanged. Cheap: a single GIST-indexed
        ST_Contains, no GEE round trip."""
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
            SELECT ST_Contains(ST_Union(vf.geom), ST_SetSRID(ST_MakePoint(%s, %s), 4326)) AS contains
            FROM vector_feature vf
            JOIN boundary_layer bl ON bl.layer_id = vf.layer_id
            """,
            (str(project_id), lon, lat),
        )
        row = self.cur.fetchone()
        return bool(row and row["contains"])

    def get(
        self, project_id: UUID | str, analysis_id: str, params_key: str | None = None
    ) -> dict[str, Any] | None:
        """`params_key=None` (the default) means "most recently computed, in
        whatever configuration" - this is what every pre-existing caller
        (report generation, a plain GET with no config query params) wants:
        they never knew about variants before this wave and shouldn't have
        to. Pass an explicit `params_key` (from
        `analysis_config.params_key(...)`) only when the caller has a
        specific configuration in hand and wants to know if THAT exact
        variant is already computed - e.g. the Analysis panel checking
        whether the currently-selected picker state has a cached result."""
        if params_key is None:
            self.cur.execute(
                """
                SELECT project_id, analysis_id, params_key, computed_at, stats, legend,
                       tile_url_template
                FROM analysis_result
                WHERE project_id = %s AND analysis_id = %s
                ORDER BY computed_at DESC
                LIMIT 1
                """,
                (str(project_id), analysis_id),
            )
        else:
            self.cur.execute(
                """
                SELECT project_id, analysis_id, params_key, computed_at, stats, legend,
                       tile_url_template
                FROM analysis_result
                WHERE project_id = %s AND analysis_id = %s AND params_key = %s
                """,
                (str(project_id), analysis_id, params_key),
            )
        return self.cur.fetchone()

    def list_for_project(self, project_id: UUID | str) -> dict[str, Any]:
        """{analysis_id: computed_at} for every analysis this project has a
        cached result for - just enough for the catalog list to show "last
        computed" per row without fetching each full stats/legend blob.

        An analysis_id can now have MULTIPLE rows (one per configured
        variant, see this wave's own migration) - `DISTINCT ON` picks the
        most-recently-computed one per id. This means "computed, most
        recently, in SOME configuration" - not "the currently-selected
        configuration is ready." That's the correct, minimal answer for
        this badge: it was always just a staleness indicator, never a
        promise that one particular variant is cached."""
        self.cur.execute(
            """
            SELECT DISTINCT ON (analysis_id) analysis_id, computed_at
            FROM analysis_result
            WHERE project_id = %s
            ORDER BY analysis_id, computed_at DESC
            """,
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
        params_key: str = "default",
    ) -> dict[str, Any]:
        self.cur.execute(
            """
            INSERT INTO analysis_result
              (project_id, analysis_id, params_key, computed_at, computed_by, stats, legend,
               tile_url_template)
            VALUES (%s, %s, %s, now(), %s, %s, %s, %s)
            ON CONFLICT (project_id, analysis_id, params_key) DO UPDATE SET
              computed_at = now(),
              computed_by = EXCLUDED.computed_by,
              stats = EXCLUDED.stats,
              legend = EXCLUDED.legend,
              tile_url_template = EXCLUDED.tile_url_template
            RETURNING project_id, analysis_id, params_key, computed_at, stats, legend,
                      tile_url_template
            """,
            (
                str(project_id), analysis_id, params_key, str(computed_by),
                Jsonb(stats), Jsonb(legend) if legend is not None else None, tile_url_template,
            ),
        )
        row = self.cur.fetchone()
        assert row is not None
        return row
