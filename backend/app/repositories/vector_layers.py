"""Vector feature persistence (Wave: multi-format layers, Part A).

Geometry construction happens IN PostGIS (ST_GeomFromGeoJSON), not in Python -
see app/services/ingestion/vector.py's module docstring for why no
shapely/GDAL vector-geometry library is needed here at all."""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb


class VectorFeatureRepository:
    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur

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

    def as_geojson_feature_collection(self, layer_id: UUID | str) -> dict[str, Any]:
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
