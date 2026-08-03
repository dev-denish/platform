"""DB-backed integration tests (Wave: multi-format layers, Part A) for vector
file ingestion - GeoJSON/KML/CSV/Shapefile-as-zip - through the REAL job
function (`workers.jobs.run_ingest_job`), the same one the `/datasets/upload`
endpoint dispatches to (see test_tile_ingest_e2e.py for the identical
convention on the raster side). Proves the `spatial_layer`/`vector_feature`
rows PostGIS actually ends up with, not just that a Python function returned
without raising - see test_db_repositories.py for the same skip-guard
convention this file follows.

Run locally with, e.g.:
    DMRV_TEST_DATABASE=1 DMRV_DB_HOST=localhost DMRV_DB_USER=dmrv \
    DMRV_DB_PASSWORD=... DMRV_DB_NAME=dmrv_test pytest -m integration
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
import zipfile

import pytest
import shapefile

pytestmark = pytest.mark.integration

if not os.getenv("DMRV_TEST_DATABASE"):
    pytest.skip("DMRV_TEST_DATABASE not set; skipping DB integration tests", allow_module_level=True)

from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402
from app.services.ingestion import vector as V  # noqa: E402
from app.services.ingestion.storage import LocalStorage  # noqa: E402
from app.services.jobs_service import JobService  # noqa: E402
from app.workers.jobs import run_ingest_job  # noqa: E402


@pytest.fixture(scope="module")
def db() -> Database:
    d = Database(get_settings())
    d.connect()
    yield d
    d.close()


def _make_user(db: Database, role: str = "GIS Associate") -> uuid.UUID:
    with db.transaction() as cur:
        row = UserRepository(cur).upsert(f"vectortest-{uuid.uuid4()}", "x", role)
    return row["user_id"]


def _run_upload(
    db, tmp_path, *, staged_path: str, lat_column: str | None = None,
    lon_column: str | None = None, project_name: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Drives the SAME job function `/datasets/upload` dispatches to
    (workers.jobs.run_ingest_job) end-to-end against the real DB. Returns
    (job_id, user_id) so the caller can inspect the terminal job row."""
    settings = get_settings()
    storage = LocalStorage(str(tmp_path / "storage"))
    user_id = _make_user(db)
    job_id, _is_new = JobService(db, settings).submit(
        user_id=user_id, kind="ingest_dataset", idempotency_key=None, request_id=None
    )
    ctx = {"db": db, "storage": storage, "settings": settings, "job_try": 1}
    asyncio.run(
        run_ingest_job(
            ctx,
            job_id=str(job_id),
            staged_path=staged_path,
            meta={
                "project_name": project_name or f"Vector {uuid.uuid4()}", "region": "Karnataka",
                "dataset_type": "Boundary", "source": "test", "classification_method": "",
                "accuracy_score": None, "date_processed": "2026-01-01", "pixel_size_m": 10.0,
                "lat_column": lat_column, "lon_column": lon_column,
            },
            legend=None,
            actor={"user_id": str(user_id), "username": "vectortest", "role": "GIS Associate"},
            request_id=None,
        )
    )
    return job_id, user_id


def _layer_row(db, dataset_id) -> dict:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT layer_id, layer_kind, crs FROM spatial_layer WHERE dataset_id = %s",
            (str(dataset_id),),
        )
        return cur.fetchone()


def _feature_rows(db, layer_id) -> list[dict]:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ST_AsText(geom) AS wkt, ST_AsGeoJSON(geom) AS geojson, properties "
            "FROM vector_feature WHERE layer_id = %s ORDER BY feature_id",
            (str(layer_id),),
        )
        return cur.fetchall()


# --------------------------------------------------------------- GeoJSON happy path


def test_geojson_upload_creates_vector_layer_and_features(db, tmp_path):
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[76.0, 13.0], [76.0, 13.01], [76.01, 13.01], [76.01, 13.0], [76.0, 13.0]]],
                },
                "properties": {"plot_id": "P1", "area_class": "cropland"},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [76.005, 13.005]},
                "properties": {"plot_id": "P2"},
            },
        ],
    }
    staged = tmp_path / "upload.geojson"
    staged.write_text(json.dumps(geojson))

    job_id, user_id = _run_upload(db, tmp_path, staged_path=str(staged))

    job = JobService(db, get_settings()).get_for_user(job_id, user_id)
    assert job.status == "succeeded", job.error
    assert job.result["layer_kind"] == "vector"
    assert job.result["feature_count"] == 2

    layer = _layer_row(db, job.result["dataset_id"])
    assert layer["layer_kind"] == "vector"
    assert layer["crs"] == "EPSG:4326"

    rows = _feature_rows(db, layer["layer_id"])
    assert len(rows) == 2
    kinds = {json.loads(r["geojson"])["type"] for r in rows}
    assert kinds == {"Polygon", "Point"}
    props = [r["properties"] for r in rows]
    assert {"plot_id": "P1", "area_class": "cropland"} in props
    assert {"plot_id": "P2"} in props
    # 1 ha square-ish polygon at the equator-ish latitude - just confirm the
    # polygon area landed on the KPI as a real, non-zero number, not that the
    # points contributed anything (points correctly report 0 ha of their own).
    assert job.result["total_area_ha"] > 0


# --------------------------------------------------------------- KML happy path


def test_kml_upload_creates_vector_layer_and_features(db, tmp_path):
    kml = """<?xml version="1.0"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark>
  <name>Boundary A</name>
  <ExtendedData><Data name="plot_id"><value>KML-1</value></Data></ExtendedData>
  <Polygon><outerBoundaryIs><LinearRing>
    <coordinates>76.0,13.0,0 76.0,13.01,0 76.01,13.01,0 76.01,13.0,0 76.0,13.0,0</coordinates>
  </LinearRing></outerBoundaryIs></Polygon>
</Placemark>
</Document></kml>"""
    staged = tmp_path / "upload.kml"
    staged.write_text(kml)

    job_id, user_id = _run_upload(db, tmp_path, staged_path=str(staged))

    job = JobService(db, get_settings()).get_for_user(job_id, user_id)
    assert job.status == "succeeded", job.error
    layer = _layer_row(db, job.result["dataset_id"])
    rows = _feature_rows(db, layer["layer_id"])
    assert len(rows) == 1
    assert json.loads(rows[0]["geojson"])["type"] == "Polygon"
    assert rows[0]["properties"]["name"] == "Boundary A"
    assert rows[0]["properties"]["plot_id"] == "KML-1"


# --------------------------------------------------------------- CSV happy path


def test_csv_upload_with_lat_lon_columns_creates_point_features(db, tmp_path):
    csv_bytes = (
        "site,latitude,longitude\n"
        "Well-1,13.01,76.02\n"
        "Well-2,13.02,76.03\n"
    )
    staged = tmp_path / "upload.csv"
    staged.write_text(csv_bytes)

    job_id, user_id = _run_upload(
        db, tmp_path, staged_path=str(staged), lat_column="latitude", lon_column="longitude"
    )

    job = JobService(db, get_settings()).get_for_user(job_id, user_id)
    assert job.status == "succeeded", job.error
    layer = _layer_row(db, job.result["dataset_id"])
    rows = _feature_rows(db, layer["layer_id"])
    assert len(rows) == 2
    wkts = {r["wkt"] for r in rows}
    assert "POINT(76.02 13.01)" in wkts
    assert "POINT(76.03 13.02)" in wkts
    sites = {r["properties"]["site"] for r in rows}
    assert sites == {"Well-1", "Well-2"}
    # Points contribute no polygon area - the vector-layer convention
    # (VectorFeatureRepository.total_polygon_area_ha's own docstring).
    assert job.result["total_area_ha"] == 0.0


# --------------------------------------------------------------- Shapefile happy path


def _build_shapefile_zip(tmp_path, name: str = "plots", *, wgs84: bool = True) -> str:
    base = str(tmp_path / name)
    w = shapefile.Writer(base, shapeType=shapefile.POLYGON)
    w.field("plot_id", "C")
    w.poly([[[76.0, 13.0], [76.0, 13.01], [76.01, 13.01], [76.01, 13.0], [76.0, 13.0]]])
    w.record("SHP-1")
    w.close()
    if wgs84:
        prj = (
            'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
            'SPHEROID["WGS_1984",6378137,298.257223563]],'
            'PRIMEM["Greenwich",0],UNIT["Degree",0.017453292519943295]]'
        )
    else:
        # A real geographic CRS with no WGS84 marker substring anywhere in it
        # (see _WGS84_MARKERS - a naive substring check, so this string must
        # not even coincidentally contain "WGS_1984"/"WGS84"/etc.).
        prj = (
            'GEOGCS["GCS_North_American_1983",'
            'DATUM["D_North_American_1983",SPHEROID["GRS_1980",6378137,298.257222101]],'
            'PRIMEM["Greenwich",0],UNIT["Degree",0.017453292519943295]]'
        )
    with open(base + ".prj", "w") as f:
        f.write(prj)
    zpath = str(tmp_path / f"{name}.zip")
    with zipfile.ZipFile(zpath, "w") as zf:
        for ext in (".shp", ".shx", ".dbf", ".prj"):
            zf.write(base + ext, arcname=name + ext)
    return zpath


def test_shapefile_zip_upload_creates_vector_layer_and_features(db, tmp_path):
    zpath = _build_shapefile_zip(tmp_path)

    job_id, user_id = _run_upload(db, tmp_path, staged_path=zpath)

    job = JobService(db, get_settings()).get_for_user(job_id, user_id)
    assert job.status == "succeeded", job.error
    layer = _layer_row(db, job.result["dataset_id"])
    rows = _feature_rows(db, layer["layer_id"])
    assert len(rows) == 1
    assert json.loads(rows[0]["geojson"])["type"] == "Polygon"
    assert rows[0]["properties"]["plot_id"] == "SHP-1"
    assert job.result["total_area_ha"] > 0


# --------------------------------------------------------------- CSV bad column rejection


def test_csv_upload_with_nonexistent_lat_column_fails_cleanly_not_500(db, tmp_path):
    staged = tmp_path / "upload.csv"
    staged.write_text("site,lat,lon\nA,13.0,76.0\n")

    job_id, user_id = _run_upload(
        db, tmp_path, staged_path=str(staged), lat_column="does_not_exist", lon_column="lon"
    )

    job = JobService(db, get_settings()).get_for_user(job_id, user_id)
    assert job.status == "failed", "a bad column name must be a terminal, clean failure"
    assert job.error["code"] == "validation_error"


def test_parse_csv_points_rejects_nonexistent_column_directly(tmp_path):
    """Direct unit-level check of the parser itself (no DB, no job), same
    invariant as the job-level test above but pinpointing exactly which
    function raises and with what exception type."""
    from app.core.errors import ValidationError

    staged = tmp_path / "upload.csv"
    staged.write_text("site,lat,lon\nA,13.0,76.0\n")

    with pytest.raises(ValidationError):
        V.parse_csv_points(str(staged), "nope", "lon")


# --------------------------------------------------------------- malformed geometry rejection


def test_geojson_with_structurally_malformed_geometry_fails_cleanly_not_500(db, tmp_path):
    """A MultiPolygon whose coordinates are actually Polygon-shaped (missing
    one nesting level) parses fine as JSON and passes the Python-side type
    check (_validate_geometry only checks the `type` field) - PostGIS's
    ST_GeomFromGeoJSON is what actually rejects it, inside
    VectorFeatureRepository.insert_many. Regression test for the bug fixed in
    IngestionService._ingest_vector: this used to be an unclassified bare
    exception, retried job_max_retries times as if transient before
    dead-lettering, instead of failing immediately as the permanent,
    client-caused error it actually is."""
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [[76.0, 13.0], [76.0, 13.01], [76.01, 13.0]],
                },
                "properties": {},
            }
        ],
    }
    staged = tmp_path / "bad.geojson"
    staged.write_text(json.dumps(geojson))

    job_id, user_id = _run_upload(db, tmp_path, staged_path=str(staged))

    job = JobService(db, get_settings()).get_for_user(job_id, user_id)
    assert job.status == "failed", (
        "malformed geometry must fail the job immediately, not dead_letter after retries"
    )
    assert job.error["code"] == "unprocessable"


def test_geojson_with_non_numeric_coordinate_fails_cleanly_not_500(db, tmp_path):
    """Regression test for the RecursionError fixed in
    vector.py's `_iter_coord_pairs` - a string where a number belongs used to
    recurse into itself (the string is truthy and re-indexes to itself)
    until Python's recursion limit crashed the job with an unclassified
    exception, instead of a clean, immediate 'unprocessable' failure."""
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": ["oops", 13.0]}, "properties": {}}
        ],
    }
    staged = tmp_path / "bad2.geojson"
    staged.write_text(json.dumps(geojson))

    job_id, user_id = _run_upload(db, tmp_path, staged_path=str(staged))

    job = JobService(db, get_settings()).get_for_user(job_id, user_id)
    assert job.status == "failed"
    assert job.error["code"] == "unprocessable"


def test_kml_with_invalid_coordinate_text_fails_cleanly_not_500(db, tmp_path):
    kml = """<?xml version="1.0"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><Point><coordinates>not-a-number,13.0,0</coordinates></Point></Placemark>
</Document></kml>"""
    staged = tmp_path / "bad.kml"
    staged.write_text(kml)

    job_id, user_id = _run_upload(db, tmp_path, staged_path=str(staged))

    job = JobService(db, get_settings()).get_for_user(job_id, user_id)
    assert job.status == "failed"
    assert job.error["code"] == "unprocessable"


def test_shapefile_zip_with_non_wgs84_prj_is_rejected_not_reprojected(db, tmp_path):
    zpath = _build_shapefile_zip(tmp_path, name="badcrs", wgs84=False)

    job_id, user_id = _run_upload(db, tmp_path, staged_path=zpath)

    job = JobService(db, get_settings()).get_for_user(job_id, user_id)
    assert job.status == "failed"
    assert job.error["code"] == "unprocessable"


def test_shapefile_zip_that_is_not_actually_a_zip_fails_cleanly_not_500(db, tmp_path):
    staged = tmp_path / "not_really.zip"
    staged.write_bytes(b"this is not a zip file at all")

    job_id, user_id = _run_upload(db, tmp_path, staged_path=str(staged))

    job = JobService(db, get_settings()).get_for_user(job_id, user_id)
    assert job.status == "failed"
    assert job.error["code"] == "unprocessable"
