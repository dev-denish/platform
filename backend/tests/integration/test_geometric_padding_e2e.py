"""DB-backed integration tests (Wave: geometric padding fix) - ingest REAL
rasters through the ACTUAL job function (workers.jobs.run_ingest_job), the
same one production dispatches, and confirm the reported area/class numbers
against an INDEPENDENT ground truth computed directly from each raster's own
geometry - not a unit test calling raster.compute_stats in isolation, and not
trusting anything this app's own code already computed.

Run locally with, e.g.:
    DMRV_TEST_DATABASE=1 DMRV_DB_HOST=localhost DMRV_DB_USER=dmrv \
    DMRV_DB_PASSWORD=... DMRV_DB_NAME=dmrv_test pytest -m integration
"""
from __future__ import annotations

import asyncio
import os
import uuid

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine, from_origin

pytestmark = pytest.mark.integration

if not os.getenv("DMRV_TEST_DATABASE"):
    pytest.skip("DMRV_TEST_DATABASE not set; skipping DB integration tests", allow_module_level=True)

from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402
from app.services.ingestion.storage import LocalStorage  # noqa: E402
from app.services.jobs_service import JobService  # noqa: E402
from app.workers.jobs import run_ingest_job  # noqa: E402


@pytest.fixture(scope="module")
def db() -> Database:
    d = Database(get_settings())
    d.connect()
    yield d
    d.close()


def _make_user(db: Database) -> uuid.UUID:
    with db.transaction() as cur:
        row = UserRepository(cur).upsert(f"padfix-{uuid.uuid4()}", "x", "Administrator")
    return row["user_id"]


def _run_real_ingest(db, storage, user_id, staged_path, *, legend, dataset_type="LULC", accuracy=90.0):
    settings = get_settings()
    job_id, _is_new = JobService(db, settings).submit(
        user_id=user_id, kind="ingest_dataset", idempotency_key=None, request_id=None
    )
    ctx = {"db": db, "storage": storage, "settings": settings, "job_try": 1}
    asyncio.run(
        run_ingest_job(
            ctx,
            job_id=str(job_id),
            staged_path=str(staged_path),
            meta={
                "project_name": f"PadFix E2E {uuid.uuid4()}", "region": "Karnataka",
                "dataset_type": dataset_type, "source": "test", "classification_method": "",
                "accuracy_score": accuracy if legend else None, "date_processed": "2026-01-01",
                "pixel_size_m": 10.0,
            },
            legend=legend,
            actor={"user_id": str(user_id), "username": "padfix", "role": "Administrator"},
            request_id=None,
        )
    )
    job = JobService(db, get_settings()).get_for_user(job_id, user_id)
    assert job.status == "succeeded", job.error
    return job


def test_rotated_footprint_with_class_zero_matches_ground_truth_through_real_ingest(db, tmp_path):
    """Rebuilds the exact bug case that first caught this - a REAL rotated
    GDAL geotransform (not a numpy simulation) + a legend naming class 0 as
    real (Water) + an INDEPENDENT companion ground truth computed directly
    from the source's own geometry - and runs it through the REAL
    ingestion job, not raster.compute_stats called in isolation."""
    storage = LocalStorage(str(tmp_path / "storage"))
    user_id = _make_user(db)

    h = w = 300
    arr = np.zeros((h, w), dtype="uint16")
    arr[: h // 2, :] = 0  # Water - legend-defined real class 0
    arr[h // 2 :, :] = 1  # Forest
    angle = np.radians(22)
    transform = Affine(
        10 * np.cos(angle), -10 * np.sin(angle), 640000,
        10 * np.sin(angle), 10 * np.cos(angle), 1445000,
    )
    staged = tmp_path / "rotated_upload.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=1, dtype="uint16",
        crs="EPSG:32643", transform=transform, nodata=None,
    )
    with rasterio.open(staged, "w", **profile) as d:
        d.write(arr, 1)

    # Independent ground truth: the source's OWN geometry, computed here,
    # not by anything this app's pipeline does. Rotation changes neither
    # pixel count nor pixel area.
    pixel_ha = abs(transform.a * transform.e - transform.b * transform.d) / 10_000.0
    truth_water_ha = round((h // 2) * w * pixel_ha, 4)
    truth_forest_ha = round((h - h // 2) * w * pixel_ha, 4)
    truth_total_ha = truth_water_ha + truth_forest_ha

    legend = {"0": {"label": "Water"}, "1": {"label": "Forest"}}
    job = _run_real_ingest(db, storage, user_id, staged, legend=legend)

    measured_water = job.result["class_stats"]["Water"]
    measured_forest = job.result["class_stats"]["Forest"]
    measured_total = job.result["total_area_ha"]
    print(
        f"\nGROUND TRUTH  : Water={truth_water_ha} ha  Forest={truth_forest_ha} ha  "
        f"Total={truth_total_ha} ha"
    )
    print(
        f"MEASURED (real ingest): Water={measured_water} ha  Forest={measured_forest} ha  "
        f"Total={measured_total} ha"
    )

    assert "Unclassified" not in job.result["class_stats"], (
        "the rotated corner fill must be excluded entirely, not counted as a "
        "fake Unclassified class"
    )
    assert measured_water == pytest.approx(truth_water_ha, rel=0.01)
    assert measured_forest == pytest.approx(truth_forest_ha, rel=0.01)
    assert measured_total == pytest.approx(truth_total_ha, rel=0.01)

    # The promoted display/COG file itself carries a REAL internal mask -
    # not app-only metadata, not a separate shadow file.
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT file_key FROM spatial_layer sl JOIN dataset d "
            "ON d.dataset_id = sl.dataset_id WHERE d.project_id = %s",
            (job.result["project_id"],),
        )
        file_key = cur.fetchone()["file_key"]
    raster_path = storage.local_path_for_processing(file_key)
    with rasterio.open(raster_path) as d:
        from rasterio.enums import MaskFlags

        assert MaskFlags.per_dataset in d.mask_flag_enums[0]
        assert d.nodata is None, "no value-based nodata - the mask is the single source of truth"
        mask = d.read_masks(1)
        pct_masked = 100 * (mask == 0).mean()
        print(f"promoted raster: {pct_masked:.2f}% masked as padding (the rotated corners)")
        assert 0 < pct_masked < 50  # real corner fill exists, but isn't most of the raster


def test_non_rotated_real_nodata_classified_raster_still_works_through_real_ingest(db, tmp_path):
    """Regression: the original Wave 1.5 case (real nodata, no class-0
    ambiguity) through the REAL ingestion pipeline - nothing should change
    for the common case."""
    storage = LocalStorage(str(tmp_path / "storage"))
    user_id = _make_user(db)

    h = w = 300
    rng = np.random.default_rng(42)
    arr = rng.integers(1, 6, size=(h, w)).astype("uint8")
    arr[:30, :] = 0  # real nodata rows
    staged = tmp_path / "nodata_upload.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=1, dtype="uint8",
        crs="EPSG:32643", transform=from_origin(640000, 1445000, 10, 10), nodata=0,
    )
    with rasterio.open(staged, "w", **profile) as d:
        d.write(arr, 1)

    pixel_ha = (10 * 10) / 10_000.0
    truth_total_ha = round(int((arr != 0).sum()) * pixel_ha, 4)

    legend = {str(i): {"label": f"Class {i}"} for i in range(1, 6)}
    job = _run_real_ingest(db, storage, user_id, staged, legend=legend)

    print(f"\nGROUND TRUTH total: {truth_total_ha} ha; MEASURED: {job.result['total_area_ha']} ha")
    assert job.result["total_area_ha"] == pytest.approx(truth_total_ha, abs=1e-6)
    assert "Unclassified" not in job.result["class_stats"]


def test_raster_with_no_padding_at_all_reports_the_full_area_through_real_ingest(db, tmp_path):
    """Regression: the common case - a perfectly ordinary raster with no
    rotation and no nodata at all - must still report the FULL raster as
    real area. Nothing about this wave should exclude anything here."""
    storage = LocalStorage(str(tmp_path / "storage"))
    user_id = _make_user(db)

    h = w = 300
    arr = np.full((h, w), 3, dtype="uint8")
    staged = tmp_path / "clean_upload.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=1, dtype="uint8",
        crs="EPSG:32643", transform=from_origin(640000, 1445000, 10, 10), nodata=None,
    )
    with rasterio.open(staged, "w", **profile) as d:
        d.write(arr, 1)

    pixel_ha = (10 * 10) / 10_000.0
    truth_total_ha = round(h * w * pixel_ha, 4)

    legend = {"3": {"label": "Forest"}}
    job = _run_real_ingest(db, storage, user_id, staged, legend=legend)

    print(f"\nGROUND TRUTH (full raster): {truth_total_ha} ha; MEASURED: {job.result['total_area_ha']} ha")
    assert job.result["total_area_ha"] == pytest.approx(truth_total_ha, abs=1e-6)


def test_raw_unclassified_rotated_footprint_total_area_excludes_padding_through_real_ingest(
    db, tmp_path
):
    """Fix #5's mechanism, finally verified for real: a RAW/unclassified
    (no-legend) scene with a genuinely rotated footprint, through the REAL
    ingestion pipeline. Total Area (the band-stats path, not the
    class-area path) must exclude the rotated corner fill, matching an
    independent ground truth from the source's own geometry - not the old
    "every band exactly 0" heuristic, which this fixes by the same
    geometric mechanism as the classified case above."""
    storage = LocalStorage(str(tmp_path / "storage"))
    user_id = _make_user(db)

    h = w = 300
    rng = np.random.default_rng(3)
    bands = np.zeros((3, h, w), dtype="uint16")
    for b in range(3):
        bands[b] = (np.linspace(500, 3500, w) + rng.normal(0, 100, (h, w))).clip(1, 4000)
    angle = np.radians(18)
    transform = Affine(
        10 * np.cos(angle), -10 * np.sin(angle), 640000,
        10 * np.sin(angle), 10 * np.cos(angle), 1445000,
    )
    staged = tmp_path / "raw_rotated_upload.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=3, dtype="uint16",
        crs="EPSG:32643", transform=transform, nodata=None,
    )
    with rasterio.open(staged, "w", **profile) as d:
        d.write(bands)

    pixel_ha = abs(transform.a * transform.e - transform.b * transform.d) / 10_000.0
    truth_total_ha = round(h * w * pixel_ha, 4)  # 100% real data in the source, rotation aside

    job = _run_real_ingest(db, storage, user_id, staged, legend=None, dataset_type="Satellite / Raw Imagery")

    print(f"\nGROUND TRUTH (source's real footprint): {truth_total_ha} ha")
    print(f"MEASURED Total Area (real ingest, raw/unclassified path): {job.result['total_area_ha']} ha")
    assert job.result["total_area_ha"] == pytest.approx(truth_total_ha, rel=0.01)
    assert job.result["class_stats"] is None  # no legend -> band_stats path, not class-area
    assert job.result["band_stats"] is not None
