"""DB-backed integration tests for rename-a-layer (Administrator-only
`display_name` override) - same real-database convention as
test_reference_layers.py, since RBAC here is a real HTTP route+role wiring
(RENAME_LAYER_ROLES) and the round-trip through GET /projects/{id}/layers
needs the real LayerRepository.list_for_project join, not a fake.

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
from rasterio.transform import from_origin

pytestmark = pytest.mark.integration

if not os.getenv("DMRV_TEST_DATABASE"):
    pytest.skip("DMRV_TEST_DATABASE not set; skipping DB integration tests", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.domain.enums import Role  # noqa: E402
from app.main import create_app  # noqa: E402
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


class _NullTaskRunner:
    async def run(self, fn, /, *args, **kwargs):
        raise AssertionError("no RBAC-rejected request should reach job dispatch")

    def shutdown(self) -> None: ...


@pytest.fixture
def client(db, tmp_path) -> TestClient:
    settings = get_settings().model_copy(
        update={
            "local_data_dir": str(tmp_path / "data"),
            "upload_staging_dir": str(tmp_path / "staging"),
        }
    )
    app = create_app(settings)
    app.state.settings = settings
    app.state.db = db
    app.state.storage = LocalStorage(str(tmp_path / "storage"))
    app.state.task_runner = _NullTaskRunner()
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


def _make_token(db: Database, role: Role) -> tuple[str, uuid.UUID]:
    settings = get_settings()
    username = f"layerrenametest-{uuid.uuid4()}"
    with db.transaction() as cur:
        row = UserRepository(cur).upsert(username, "x", role.value)
    token = create_access_token(
        settings, user_id=str(row["user_id"]), username=username, role=role.value
    )
    return token, row["user_id"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _tiny_geotiff(tmp_path) -> str:
    h = w = 64
    arr = np.ones((h, w), dtype="uint8")
    path = tmp_path / f"src-{uuid.uuid4().hex[:8]}.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=1, dtype="uint8",
        crs="EPSG:32643", transform=from_origin(640000, 1445000, 10, 10), nodata=0,
    )
    with rasterio.open(path, "w", **profile) as d:
        d.write(arr, 1)
    return str(path)


def _ingest_layer(db: Database, tmp_path) -> tuple[uuid.UUID, uuid.UUID]:
    """Ingests one ordinary (non-reference, non-adhoc) raster layer through
    the real job function. Returns (project_id, layer_id)."""
    settings = get_settings()
    storage = LocalStorage(str(tmp_path / "storage"))
    username = f"layerrename-ingest-{uuid.uuid4()}"
    with db.transaction() as cur:
        row = UserRepository(cur).upsert(username, "x", Role.GIS_ASSOCIATE.value)
    user_id = row["user_id"]

    staged = _tiny_geotiff(tmp_path)
    job_id, _is_new = JobService(db, settings).submit(
        user_id=user_id, kind="ingest_dataset", idempotency_key=None, request_id=None
    )
    ctx = {"db": db, "storage": storage, "settings": settings, "job_try": 1}
    asyncio.run(
        run_ingest_job(
            ctx,
            job_id=str(job_id),
            staged_path=staged,
            meta={
                "project_name": f"Layer Rename Test Project {uuid.uuid4()}", "region": "Karnataka",
                "dataset_type": "Satellite / Raw Imagery", "source": "test",
                "classification_method": "", "accuracy_score": None,
                "date_processed": "2026-01-01", "pixel_size_m": 10.0,
            },
            legend=None,
            actor={"user_id": str(user_id), "username": username, "role": Role.GIS_ASSOCIATE.value},
            request_id=None,
        )
    )
    job = JobService(db, settings).get_for_user(job_id, user_id)
    assert job.status == "succeeded", job.error
    project_id = job.result["project_id"]
    dataset_id = job.result["dataset_id"]
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT layer_id FROM spatial_layer WHERE dataset_id = %s", (str(dataset_id),))
        layer_id = cur.fetchone()["layer_id"]
    return project_id, layer_id


def test_administrator_can_rename_a_layer_and_the_layers_listing_reflects_it(db, client, tmp_path):
    project_id, layer_id = _ingest_layer(db, tmp_path)
    admin_token, _ = _make_token(db, Role.ADMINISTRATOR)

    r = client.patch(
        f"/api/v1/layers/{layer_id}/display-name",
        json={"display_name": "Q1 2026 Canopy Cover"},
        headers=_auth(admin_token),
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"layer_id": str(layer_id), "display_name": "Q1 2026 Canopy Cover"}

    listing = client.get(f"/api/v1/projects/{project_id}/layers", headers=_auth(admin_token))
    assert listing.status_code == 200, listing.text
    layer = next(l for l in listing.json()["layers"] if l["layer_id"] == str(layer_id))
    assert layer["display_name"] == "Q1 2026 Canopy Cover"


def test_non_administrator_gets_403_renaming_a_layer(db, client, tmp_path):
    _project_id, layer_id = _ingest_layer(db, tmp_path)
    gis_token, _ = _make_token(db, Role.GIS_ASSOCIATE)

    r = client.patch(
        f"/api/v1/layers/{layer_id}/display-name",
        json={"display_name": "Should Not Land"},
        headers=_auth(gis_token),
    )

    assert r.status_code == 403, r.text
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT d.display_name FROM spatial_layer sl JOIN dataset d "
            "ON d.dataset_id = sl.dataset_id WHERE sl.layer_id = %s",
            (str(layer_id),),
        )
        assert cur.fetchone()["display_name"] is None


def test_rename_nonexistent_layer_returns_404(db, client):
    admin_token, _ = _make_token(db, Role.ADMINISTRATOR)
    r = client.patch(
        f"/api/v1/layers/{uuid.uuid4()}/display-name",
        json={"display_name": "Anything"},
        headers=_auth(admin_token),
    )
    assert r.status_code == 404, r.text
