"""DB-backed integration tests for delete-a-dataset (Administrator-only,
formal project-scoped uploads only - not reference/ad-hoc layers, which
keep their own endpoints). Same real-database + real-storage convention as
test_layer_rename.py: RBAC here is real HTTP route+role wiring
(DELETE_DATASET_ROLES), and "the file is actually gone" can only be proven
against a real LocalStorage pointed at a real tmp_path, not a fake.

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
    username = f"datasetdeletetest-{uuid.uuid4()}"
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


def _ingest_layer(db: Database, tmp_path, *, is_reference: bool = False) -> tuple[uuid.UUID, uuid.UUID]:
    """Ingests one raster layer through the real job function, against the
    SAME storage root the test's `client` fixture points at (so this test
    can assert on real files, not a second, disconnected LocalStorage
    instance). Returns (project_id, layer_id)."""
    settings = get_settings()
    storage = LocalStorage(str(tmp_path / "storage"))
    username = f"datasetdelete-ingest-{uuid.uuid4()}"
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
                "project_name": f"Dataset Delete Test Project {uuid.uuid4()}", "region": "Karnataka",
                "dataset_type": "Satellite / Raw Imagery", "source": "test",
                "classification_method": "", "accuracy_score": None,
                "date_processed": "2026-01-01", "pixel_size_m": 10.0,
                "is_reference": is_reference,
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


def _storage_keys(db: Database, layer_id: uuid.UUID) -> dict:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT file_key, cog_key, preview_key FROM spatial_layer WHERE layer_id = %s",
            (str(layer_id),),
        )
        return cur.fetchone()


def test_administrator_can_delete_a_dataset_and_its_files_are_actually_removed(db, client, tmp_path):
    project_id, layer_id = _ingest_layer(db, tmp_path)
    admin_token, _ = _make_token(db, Role.ADMINISTRATOR)

    keys = _storage_keys(db, layer_id)
    # Real ingest of a raster always leaves at least a cog_key (tile-ready)
    # and a preview_key - confirm the files genuinely exist BEFORE delete,
    # so "gone after" is a real assertion, not a tautology against files
    # that were never there.
    present_keys = [k for k in (keys["file_key"], keys["cog_key"], keys["preview_key"]) if k]
    assert present_keys, "expected at least one storage key from a real raster ingest"
    for key in present_keys:
        assert (tmp_path / "storage" / key).exists(), f"expected {key} to exist before delete"

    r = client.delete(f"/api/v1/datasets/{layer_id}", headers=_auth(admin_token))
    assert r.status_code == 204, r.text

    for key in present_keys:
        assert not (tmp_path / "storage" / key).exists(), f"expected {key} to be gone after delete"

    listing = client.get(f"/api/v1/projects/{project_id}/layers", headers=_auth(admin_token))
    assert listing.status_code == 200, listing.text
    assert all(l["layer_id"] != str(layer_id) for l in listing.json()["layers"])

    # Shows up readably in the activity feed - dataset_label, not a raw id.
    activity = client.get(f"/api/v1/projects/{project_id}/activity", headers=_auth(admin_token))
    assert activity.status_code == 200, activity.text
    item = next(i for i in activity.json()["items"] if i["action"] == "delete_dataset")
    assert "Removed dataset" in item["detail"]
    assert str(layer_id) not in item["detail"]


def test_non_administrator_gets_403_and_dataset_is_untouched(db, client, tmp_path):
    _project_id, layer_id = _ingest_layer(db, tmp_path)
    gis_token, _ = _make_token(db, Role.GIS_ASSOCIATE)
    keys = _storage_keys(db, layer_id)
    present_keys = [k for k in (keys["file_key"], keys["cog_key"], keys["preview_key"]) if k]

    r = client.delete(f"/api/v1/datasets/{layer_id}", headers=_auth(gis_token))
    assert r.status_code == 403, r.text

    for key in present_keys:
        assert (tmp_path / "storage" / key).exists(), f"{key} must still exist - the request was rejected"
    # Checked directly against the DB, not via GET .../layers - this token
    # belongs to an unrelated GIS Associate with no membership on this
    # project, so that endpoint's own (correct, separate) RBAC would 404 it
    # regardless of whether the delete above was rejected. This test is only
    # about DELETE_DATASET_ROLES, not project-membership view access.
    assert _storage_keys(db, layer_id)["cog_key"] == keys["cog_key"]
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT deleted_at FROM spatial_layer sl JOIN dataset d ON d.dataset_id = sl.dataset_id "
            "WHERE sl.layer_id = %s",
            (str(layer_id),),
        )
        assert cur.fetchone()["deleted_at"] is None


def test_delete_nonexistent_dataset_returns_404(db, client):
    admin_token, _ = _make_token(db, Role.ADMINISTRATOR)
    r = client.delete(f"/api/v1/datasets/{uuid.uuid4()}", headers=_auth(admin_token))
    assert r.status_code == 404, r.text


def test_reference_layer_is_out_of_scope_for_this_endpoint(db, client, tmp_path):
    """This endpoint must never be usable to remove a reference layer -
    that's ReferenceLayerService's job, with its own audit story. The
    is_reference=false guard in soft_delete_dataset is what actually
    enforces this; this test just proves it end-to-end over real HTTP."""
    _project_id, layer_id = _ingest_layer(db, tmp_path, is_reference=True)
    admin_token, _ = _make_token(db, Role.ADMINISTRATOR)

    r = client.delete(f"/api/v1/datasets/{layer_id}", headers=_auth(admin_token))
    assert r.status_code == 404, r.text

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT deleted_at FROM spatial_layer sl JOIN dataset d ON d.dataset_id = sl.dataset_id "
            "WHERE sl.layer_id = %s",
            (str(layer_id),),
        )
        assert cur.fetchone()["deleted_at"] is None

    # Cleanup, not an assertion: a reference layer is visible on EVERY
    # project (LayerRepository.list_for_project's `OR is_reference = true`),
    # so leaving this one live would leak into every other test's project-
    # layer-listing calls for the rest of the session, pointing at a COG file
    # under THIS test's tmp_path, which pytest tears down once this function
    # returns - direct SQL, not a second round-trip through the very removal
    # path this test exists to prove is unreachable from this endpoint.
    with db.transaction() as cur:
        cur.execute(
            "UPDATE dataset SET deleted_at = now() WHERE dataset_id = "
            "(SELECT dataset_id FROM spatial_layer WHERE layer_id = %s)",
            (str(layer_id),),
        )
