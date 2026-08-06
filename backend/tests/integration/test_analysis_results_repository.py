"""DB-backed tests for AnalysisResultRepository (Wave: GEE analysis registry)
against a REAL PostGIS database - the boundary-layer lookup unions real
`vector_feature` geometry via ST_Union/ST_AsGeoJSON, and upsert relies on the
real (project_id, analysis_id) primary key / ON CONFLICT semantics, so this
is not faked (see test_forest_definition.py / test_adhoc_layers.py for the
same skip-guard convention this file follows).

Run locally with, e.g.:
    DMRV_TEST_DATABASE=1 DMRV_DB_HOST=localhost DMRV_DB_USER=dmrv \
    DMRV_DB_PASSWORD=... DMRV_DB_NAME=dmrv_test pytest -m integration
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.integration

if not os.getenv("DMRV_TEST_DATABASE"):
    pytest.skip("DMRV_TEST_DATABASE not set; skipping DB integration tests", allow_module_level=True)

from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.repositories.analysis_results import AnalysisResultRepository  # noqa: E402
from app.repositories.datasets import DatasetRepository, LayerRepository  # noqa: E402
from app.repositories.projects import ProjectRepository  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402
from app.repositories.vector_layers import VectorFeatureRepository  # noqa: E402


@pytest.fixture(scope="module")
def db() -> Database:
    d = Database(get_settings())
    d.connect()
    yield d
    d.close()


def _make_project(db: Database, name: str) -> uuid.UUID:
    with db.transaction() as cur:
        project_id, _created = ProjectRepository(cur).find_or_create_by_name(name, "Karnataka")
    return project_id


def _make_user_id(db: Database) -> uuid.UUID:
    with db.transaction() as cur:
        row = UserRepository(cur).upsert(f"analysistest-{uuid.uuid4()}", "x", "Administrator")
    return row["user_id"]


def _insert_boundary_layer(
    db: Database, project_id, features: list[dict], *, loaded_at: str | None = None
) -> uuid.UUID:
    """A dataset(type='Boundary') + spatial_layer(layer_kind='vector') +
    vector_feature rows, shaped like a real GeoJSON boundary upload - same
    tables test_vector_ingest.py's real ingest-job path produces, just
    inserted directly (this repository doesn't care how they got there)."""
    with db.transaction() as cur:
        dataset_id = DatasetRepository(cur).insert(
            project_id=project_id, dataset_type="Boundary", source="test",
            accuracy_score=None, date_processed="2026-01-01", batch_id=uuid.uuid4(),
        )
        layer_id = LayerRepository(cur).insert_non_raster(
            dataset_id=dataset_id, layer_kind="vector", crs="EPSG:4326",
            bounds=(0.0, 0.0, 2.0, 1.0),
        )
        VectorFeatureRepository(cur).insert_many(layer_id, [(geom, {}) for geom in features])
        if loaded_at is not None:
            cur.execute(
                "UPDATE dataset SET loaded_at = %s WHERE dataset_id = %s",
                (loaded_at, str(dataset_id)),
            )
    return layer_id


def _square(minx: float, miny: float, maxx: float, maxy: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]],
    }


def _bbox(geojson: dict) -> tuple[float, float, float, float]:
    """Plain-Python bbox over any GeoJSON geometry's coordinates (no shapely
    dependency in this repo) - enough to prove a union covers the extent of
    every input feature without needing real geometry algebra."""
    xs: list[float] = []
    ys: list[float] = []

    def walk(coords):
        if isinstance(coords[0], int | float):
            xs.append(coords[0])
            ys.append(coords[1])
        else:
            for c in coords:
                walk(c)

    walk(geojson["coordinates"])
    return min(xs), min(ys), max(xs), max(ys)


# --------------------------------------------------------------- boundary lookup


def test_get_project_boundary_geojson_returns_none_without_a_boundary_layer(db):
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")

    with db.connection() as conn, conn.cursor() as cur:
        result = AnalysisResultRepository(cur).get_project_boundary_geojson(pid)

    assert result is None


def test_get_project_boundary_geojson_ignores_non_boundary_dataset_types(db):
    """A non-Boundary vector layer (e.g. an LULC vector upload) must never be
    mistaken for the project's AOI - only d.type = 'Boundary' counts."""
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    with db.transaction() as cur:
        dataset_id = DatasetRepository(cur).insert(
            project_id=pid, dataset_type="LULC", source="test",
            accuracy_score=None, date_processed="2026-01-01", batch_id=uuid.uuid4(),
        )
        layer_id = LayerRepository(cur).insert_non_raster(
            dataset_id=dataset_id, layer_kind="vector", crs="EPSG:4326",
            bounds=(0.0, 0.0, 1.0, 1.0),
        )
        VectorFeatureRepository(cur).insert_many(
            layer_id, [(_square(0, 0, 1, 1), {})]
        )

    with db.connection() as conn, conn.cursor() as cur:
        result = AnalysisResultRepository(cur).get_project_boundary_geojson(pid)

    assert result is None


def test_get_project_boundary_geojson_unions_all_features_of_the_boundary_layer(db):
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    _insert_boundary_layer(db, pid, [_square(0, 0, 1, 1), _square(1, 0, 2, 1)])

    with db.connection() as conn, conn.cursor() as cur:
        result = AnalysisResultRepository(cur).get_project_boundary_geojson(pid)

    assert result is not None
    assert result["type"] in ("Polygon", "MultiPolygon")
    assert _bbox(result) == (0.0, 0.0, 2.0, 1.0)


def test_get_project_boundary_geojson_uses_only_the_most_recently_loaded_boundary_layer(db):
    """Two Boundary datasets on the same project (e.g. a re-upload) - only the
    latest one's features are the current AOI; an older boundary's features
    must not leak into the union."""
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    _insert_boundary_layer(
        db, pid, [_square(10, 10, 11, 11)], loaded_at="2020-01-01T00:00:00Z"
    )
    _insert_boundary_layer(
        db, pid, [_square(0, 0, 1, 1)], loaded_at="2026-01-01T00:00:00Z"
    )

    with db.connection() as conn, conn.cursor() as cur:
        result = AnalysisResultRepository(cur).get_project_boundary_geojson(pid)

    assert _bbox(result) == (0.0, 0.0, 1.0, 1.0)


# --------------------------------------------------------------- get / list / upsert


def test_get_returns_none_when_no_result_computed_yet(db):
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")

    with db.connection() as conn, conn.cursor() as cur:
        assert AnalysisResultRepository(cur).get(pid, "hansen_gfc") is None


def test_list_for_project_returns_only_computed_analysis_ids(db):
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    computed_by = _make_user_id(db)
    with db.transaction() as cur:
        AnalysisResultRepository(cur).upsert(
            project_id=pid, analysis_id="hansen_gfc", computed_by=computed_by,
            stats={"a": 1}, legend=None, tile_url_template=None,
        )

    with db.connection() as conn, conn.cursor() as cur:
        computed = AnalysisResultRepository(cur).list_for_project(pid)

    assert set(computed) == {"hansen_gfc"}


def test_upsert_is_a_true_upsert_no_duplicate_row_and_updates_computed_at_and_stats(db):
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    computed_by = _make_user_id(db)

    with db.transaction() as cur:
        first = AnalysisResultRepository(cur).upsert(
            project_id=pid, analysis_id="hansen_gfc", computed_by=computed_by,
            stats={"baseline_forest_area_ha": 10.0}, legend=None, tile_url_template="https://a",
        )
    with db.transaction() as cur:
        second = AnalysisResultRepository(cur).upsert(
            project_id=pid, analysis_id="hansen_gfc", computed_by=computed_by,
            stats={"baseline_forest_area_ha": 20.0}, legend=None, tile_url_template="https://b",
        )

    assert second["stats"]["baseline_forest_area_ha"] == 20.0
    assert second["tile_url_template"] == "https://b"
    assert second["computed_at"] >= first["computed_at"]

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS c FROM analysis_result WHERE project_id = %s AND analysis_id = %s",
            (str(pid), "hansen_gfc"),
        )
        assert cur.fetchone()["c"] == 1
