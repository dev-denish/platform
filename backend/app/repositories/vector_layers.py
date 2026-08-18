"""Vector feature persistence (Wave: multi-format layers, Part A).

Geometry construction happens IN PostGIS (ST_GeomFromGeoJSON), not in Python -
see app/services/ingestion/vector.py's module docstring for why no
shapely/GDAL vector-geometry library is needed here at all."""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb


class VectorFeatureRepository:
    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur

    def stage_bulk_features(self, stage_key: str, rows: Iterable[tuple[str, str]]) -> tuple[int, tuple[float, float, float, float] | None]:
        """Part 1 of the bulk-load path for a real-scale source layer
        (Admin Boundaries: Block is ~7K features nationwide, Village is
        several hundred thousand per state) - insert_many's executemany is
        fine for "one project's worth of plot boundaries" but was never
        meant to carry this. `rows` is (geom_geojson, properties_json)
        pairs, already-serialized strings (built by the ingestion script)
        so COPY can stream them in with no per-row Python object
        construction - geometry construction still happens in PostGIS
        (ST_GeomFromGeoJSON), same as insert_many, just deferred to
        commit_staged_features below.

        Split from committing into vector_feature because the bounds this
        returns are needed to create the spatial_layer row FIRST (its bbox
        columns are populated at insert time, mirroring insert_non_raster's
        existing raster/small-vector contract) - and vector_feature rows
        need that row's layer_id to exist before they can be written.

        The staging table is a real (not TEMP) table, named by the caller's
        own key and dropped by commit_staged_features: TEMP tables are
        bound to the session, and this runs inside a psycopg_pool
        connection that's returned to the pool (not closed) after the
        transaction - a stale TEMP table would silently persist onto
        whichever request borrows that connection next."""
        staging = f"admin_boundary_stage_{stage_key}"
        self.cur.execute(f'CREATE TABLE "{staging}" (geom_geojson TEXT, properties_json TEXT)')
        with self.cur.copy(f'COPY "{staging}" (geom_geojson, properties_json) FROM STDIN') as copy:
            count = 0
            for geom_geojson, properties_json in rows:
                copy.write_row((geom_geojson, properties_json))
                count += 1
        self.cur.execute(
            f"""
            SELECT ST_XMin(e) minx, ST_YMin(e) miny, ST_XMax(e) maxx, ST_YMax(e) maxy
            FROM (SELECT ST_Extent(ST_GeomFromGeoJSON(geom_geojson)) e FROM "{staging}") s
            """
        )
        row = self.cur.fetchone()
        bounds = (
            (row["minx"], row["miny"], row["maxx"], row["maxy"])
            if row is not None and row["minx"] is not None
            else None
        )
        return count, bounds

    def commit_staged_features(
        self, stage_key: str, layer_id: UUID, simplify_tolerance: float | None = None
    ) -> int:
        """Part 2: drains the staging table stage_bulk_features created into
        real vector_feature rows for the now-existing layer, then drops it -
        see stage_bulk_features' docstring for why the two are split.

        simplify_tolerance (Wave: Admin Boundaries, geometry simplification):
        source admin-boundary geometry (LGD_Blocks in particular - see the
        checkpoint that found this) is full survey precision, not simplified
        for display - Block averaged 1,485 vertices/polygon, some over
        40,000, 161MB of geometry for just 7,114 features, enough to OOM the
        backend building one whole-layer GeoJSON response. This is a
        reference/DISPLAY layer, not a source for area/adjacency
        calculations, so trading precision for size is the right call, the
        same one SOI's own product already makes by shipping its
        Administrative Boundary Database generalized to 1:1M scale.
        ST_SimplifyPreserveTopology (not plain ST_Simplify) avoids each
        polygon's own rings self-intersecting under simplification -  it does
        NOT guarantee no gaps/slivers between two adjacent features'
        independently-simplified shared borders, which is an acceptable
        trade for a display layer but would NOT be for anything computing
        areas/adjacency off this data. None (default) applies no
        simplification, unchanged behavior for every other caller."""
        staging = f"admin_boundary_stage_{stage_key}"
        if simplify_tolerance is not None:
            geom_expr = "ST_SimplifyPreserveTopology(ST_GeomFromGeoJSON(geom_geojson), %s)"
            params: tuple = (str(layer_id), simplify_tolerance)
        else:
            geom_expr = "ST_GeomFromGeoJSON(geom_geojson)"
            params = (str(layer_id),)
        try:
            self.cur.execute(
                f"""
                INSERT INTO vector_feature (layer_id, geom, properties)
                SELECT %s, ST_SetSRID({geom_expr}, 4326),
                       properties_json::jsonb
                FROM "{staging}"
                """,
                params,
            )
            return self.cur.rowcount
        finally:
            self.cur.execute(f'DROP TABLE IF EXISTS "{staging}"')

    def insert_many(
        self, layer_id: UUID, features: list[tuple[dict[str, Any], dict[str, Any]]]
    ) -> None:
        # executemany rather than a single multi-row INSERT: feature counts
        # here are "one project's worth of plot boundaries/points" (tens to
        # low thousands), not COG-scale - simplicity wins over a bulk-copy
        # optimization nothing here needs yet.
        self.cur.executemany(
            """
            INSERT INTO vector_feature (layer_id, geom, properties)
            VALUES (%s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), %s)
            """,
            [
                (str(layer_id), json.dumps(geom), Jsonb(props))
                for geom, props in features
            ],
        )

    def delete_for_layer(self, layer_id: UUID) -> None:
        """Delete-a-dataset (Administrator-only, see DatasetDeleteService): a
        vector layer's real data IS these rows - `spatial_layer`/`dataset`
        only ever get soft-deleted (deleted_at), never dropped, so the
        `ON DELETE CASCADE` on vector_feature.layer_id never fires on its
        own. Without this, a deleted vector dataset would still carry every
        one of its features forever - the DB-side equivalent of not
        actually freeing a raster's file from storage."""
        self.cur.execute("DELETE FROM vector_feature WHERE layer_id = %s", (str(layer_id),))

    def total_polygon_area_ha(self, layer_id: UUID) -> float:
        """Area only makes sense for polygon geometries - a point/line layer
        (e.g. CSV-as-points) correctly reports 0.0 ha here, same convention
        raster.py already uses for an unclassified scene having no
        class_stats: absence of a meaningful number, not an error."""
        self.cur.execute(
            """
            SELECT COALESCE(SUM(ST_Area(geography(geom))), 0) / 10000.0 AS ha
            FROM vector_feature
            WHERE layer_id = %s AND GeometryType(geom) IN ('POLYGON', 'MULTIPOLYGON')
            """,
            (str(layer_id),),
        )
        return float(self.cur.fetchone()["ha"])  # type: ignore[index]

    def as_geojson_feature_collection(
        self, layer_id: UUID | str, district_lgd_code: str | None = None
    ) -> dict[str, Any]:
        # district_lgd_code: Admin Boundaries' Village layer (see
        # VectorLayerService.get_geojson, which enforces this filter is
        # present whenever spatial_layer.requires_district_scope is true) -
        # every other caller passes None and gets the whole-layer behavior
        # unchanged. Matches idx_vector_feature_district_lgd
        # (0019_admin_boundaries) so this stays an index lookup, not a scan,
        # at village scale.
        if district_lgd_code is not None:
            self.cur.execute(
                "SELECT ST_AsGeoJSON(geom) AS geom, properties FROM vector_feature "
                "WHERE layer_id = %s AND properties->>'district_lgd_code' = %s",
                (str(layer_id), district_lgd_code),
            )
        else:
            self.cur.execute(
                "SELECT ST_AsGeoJSON(geom) AS geom, properties FROM vector_feature "
                "WHERE layer_id = %s",
                (str(layer_id),),
            )
        rows = self.cur.fetchall()
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": json.loads(r["geom"]),
                    "properties": r["properties"],
                }
                for r in rows
            ],
        }
