"""DB-backed integration test (Phase 3 Wave A): ingest a REAL raster through the
ACTUAL job function (workers.jobs.run_ingest_job) - the same one arq/ThreadPool
dispatch in production, including the COG-conversion step added in this wave -
then fetch a real tile through TileService. No mocked tile data anywhere in this
path: the raster, the COG, and the rendered PNG are all genuine.

Run locally with, e.g.:
    DMRV_TEST_DATABASE=1 DMRV_DB_HOST=localhost DMRV_DB_USER=dmrv \
    DMRV_DB_PASSWORD=... DMRV_DB_NAME=dmrv_test pytest -m integration
"""
from __future__ import annotations

import asyncio
import os
import uuid

import morecantile
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

pytestmark = pytest.mark.integration

if not os.getenv("DMRV_TEST_DATABASE"):
    pytest.skip("DMRV_TEST_DATABASE not set; skipping DB integration tests", allow_module_level=True)

from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402
from app.services.ingestion.storage import LocalStorage  # noqa: E402
from app.services.jobs_service import JobService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402
from app.services.tile_service import TileService  # noqa: E402
from app.workers.jobs import run_ingest_job  # noqa: E402

_TMS = morecantile.tms.get("WebMercatorQuad")


@pytest.fixture(scope="module")
def db() -> Database:
    d = Database(get_settings())
    d.connect()
    yield d
    d.close()


def _make_user(db: Database) -> uuid.UUID:
    with db.transaction() as cur:
        row = UserRepository(cur).upsert(f"tiletest-{uuid.uuid4()}", "x", "Administrator")
    return row["user_id"]


def test_ingest_then_fetch_a_real_tile_end_to_end(db, tmp_path):
    settings = get_settings()
    storage = LocalStorage(str(tmp_path / "storage"))
    user_id = _make_user(db)

    # A real classified raster in projected UTM metres - exactly the shape an
    # actual upload arrives in (see raster.py's own test fixtures).
    h = w = 1024
    arr = np.zeros((h, w), dtype="uint8")
    arr[: h // 2, :] = 1
    arr[h // 2 :, :] = 2
    staged = tmp_path / "upload.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=1, dtype="uint8",
        crs="EPSG:32643", transform=from_origin(640000, 1445000, 10, 10), nodata=0,
    )
    with rasterio.open(staged, "w", **profile) as d:
        d.write(arr, 1)

    job_id, _is_new = JobService(db, settings).submit(
        user_id=user_id, kind="ingest_dataset", idempotency_key=None, request_id=None
    )

    ctx = {"db": db, "storage": storage, "settings": settings, "job_try": 1}
    asyncio.run(
        run_ingest_job(
            ctx,
            job_id=str(job_id),
            staged_path=str(staged),
            meta={
                "project_name": f"Tile E2E {uuid.uuid4()}", "region": "Karnataka",
                "dataset_type": "LULC", "source": "test", "classification_method": "",
                "accuracy_score": 90.0, "date_processed": "2026-01-01", "pixel_size_m": 10.0,
            },
            legend=None,
            actor={"user_id": str(user_id), "username": "tiletest", "role": "Administrator"},
            request_id=None,
        )
    )

    job = JobService(db, settings).get_for_user(job_id, user_id)
    assert job.status == "succeeded", job.error
    assert job.result["cog_key"] is not None, job.result.get("cog_error")

    project_id = job.result["project_id"]
    proj_svc = ProjectService(db, settings, storage)
    layers = proj_svc.get_layers(project_id)
    assert len(layers.layers) == 1
    layer = layers.layers[0]
    assert layer.tile_url_template is not None
    assert "{z}/{x}/{y}.png?token=" in layer.tile_url_template

    tile_svc = TileService(db, settings, storage)
    cog_key = tile_svc.get_cog_key(layer.layer_id)
    cog_path = storage.local_path_for_processing(cog_key)
    with rasterio.open(cog_path) as cog:
        bounds = cog.bounds
    cx, cy = (bounds.left + bounds.right) / 2, (bounds.bottom + bounds.top) / 2
    t = _TMS.tile(cx, cy, 14)

    png = tile_svc.render(cog_key, t.z, t.x, t.y)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
