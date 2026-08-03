"""DB-backed integration tests (Wave: Reference Layer Library) against a REAL
PostGIS database - same convention as test_wms_external_layers.py /
test_adhoc_layers.py: RBAC here is about which HTTP route+role is wired to
which dependency (UPLOAD_ROLES to ADD via the existing upload/external-layer
endpoints, MANAGE_REFERENCE_LAYERS_ROLES to REMOVE via the one new endpoint),
and the cross-project visibility is a real SQL query-widening
(LayerRepository.list_for_project's `OR d.is_reference = true`) - both need a
real database, not a fake.

The one real-file ingestion test drives the ACTUAL job function
(workers.jobs.run_ingest_job), same pattern as test_tile_ingest_e2e.py,
rather than going through the async 202+poll HTTP contract - that proves the
real IngestionService/DatasetRepository path, not just routing.

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
from app.domain.dtos import CurrentUser  # noqa: E402
from app.domain.enums import Role  # noqa: E402
from app.main import create_app  # noqa: E402
from app.repositories.memberships import ProjectMembershipRepository  # noqa: E402
from app.repositories.projects import ProjectRepository  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402
from app.services.ingestion.storage import LocalStorage  # noqa: E402
from app.services.jobs_service import JobService  # noqa: E402
from app.services.project_access import (  # noqa: E402
    REFERENCE_LIBRARY_PROJECT_NAME,
    REFERENCE_LIBRARY_PROJECT_REGION,
)
from app.services.project_service import ProjectService  # noqa: E402
from app.workers.jobs import run_ingest_job  # noqa: E402


@pytest.fixture(scope="module")
def db() -> Database:
    d = Database(get_settings())
    d.connect()
    yield d
    d.close()


class _NullTaskRunner:
    """Every RBAC test in this module must be rejected by `require_role`
    BEFORE any job/creation logic runs - a runner that errors if actually
    invoked catches a test accidentally proving less than it claims (a 403
    that happened to occur AFTER dispatch, not because of it)."""

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
    username = f"reflayertest-{uuid.uuid4()}"
    with db.transaction() as cur:
        row = UserRepository(cur).upsert(username, "x", role.value)
    token = create_access_token(
        settings, user_id=str(row["user_id"]), username=username, role=role.value
    )
    return token, row["user_id"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_project(db: Database, name: str) -> uuid.UUID:
    with db.transaction() as cur:
        project_id, _created = ProjectRepository(cur).find_or_create_by_name(name, "Karnataka")
    return project_id


def _add_member(db: Database, project_id, user_id, role: Role, added_by) -> None:
    with db.transaction() as cur:
        ProjectMembershipRepository(cur).add(
            project_id=project_id, user_id=user_id, role=role, added_by=added_by
        )


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


def _ingest_reference_raster(db: Database, tmp_path, *, actor_role: Role) -> tuple[uuid.UUID, uuid.UUID]:
    """Runs the REAL ingest job (not the HTTP 202+poll contract - see module
    docstring) with is_reference=True. Returns (project_id, dataset's
    layer_id) off the job result."""
    settings = get_settings()
    storage = LocalStorage(str(tmp_path / "storage"))
    username = f"reflayer-ingest-{uuid.uuid4()}"
    with db.transaction() as cur:
        row = UserRepository(cur).upsert(username, "x", actor_role.value)
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
                "project_name": f"Ignored Project Name {uuid.uuid4()}", "region": "Karnataka",
                "dataset_type": "Satellite / Raw Imagery", "source": "test",
                "classification_method": "", "accuracy_score": None,
                "date_processed": "2026-01-01", "pixel_size_m": 10.0,
                "is_reference": True,
            },
            legend=None,
            actor={"user_id": str(user_id), "username": username, "role": actor_role.value},
            request_id=None,
        )
    )
    job = JobService(db, settings).get_for_user(job_id, user_id)
    assert job.status == "succeeded", job.error
    return job.result["project_id"], job.result["dataset_id"]


def _layer_id_for_dataset(db: Database, dataset_id) -> uuid.UUID:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT layer_id FROM spatial_layer WHERE dataset_id = %s", (str(dataset_id),))
        return cur.fetchone()["layer_id"]


def _hard_delete_dataset(db: Database, dataset_id) -> None:
    """Test cleanup only - a real reference layer's COG/preview lives under
    THIS test's own tmp_path storage root, which pytest deletes after the
    test finishes. Without this, the row would outlive its files, and the
    NEXT test run's `GET .../layers` (which also matches it via
    `is_reference = true`) would 500 trying to open a COG that no longer
    exists on disk - a real failure mode, just not the one under test here.
    `spatial_layer`/`kpi` cascade off `dataset_id` (see migrations)."""
    with db.transaction() as cur:
        cur.execute("DELETE FROM dataset WHERE dataset_id = %s", (str(dataset_id),))


# --------------------------------------------------------------- add: file upload (real ingest)


def test_gis_associate_can_upload_a_real_reference_layer_and_it_lands_in_the_library_project(
    db, tmp_path
):
    project_id, dataset_id = _ingest_reference_raster(db, tmp_path, actor_role=Role.GIS_ASSOCIATE)
    try:
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT is_reference, project_id FROM dataset WHERE dataset_id = %s", (str(dataset_id),)
            )
            row = cur.fetchone()
            assert row["is_reference"] is True
            cur.execute("SELECT name, region FROM project WHERE project_id = %s", (str(project_id),))
            proj = cur.fetchone()
            assert proj["name"] == REFERENCE_LIBRARY_PROJECT_NAME
            assert proj["region"] == REFERENCE_LIBRARY_PROJECT_REGION
    finally:
        _hard_delete_dataset(db, dataset_id)


# --------------------------------------------------------------- add: WMS/WFS (HTTP)


def _add_domain(db: Database, client: TestClient, admin_token: str) -> str:
    domain = f"geo-{uuid.uuid4().hex[:8]}.example.com"
    r = client.post("/api/v1/wms-domains", json={"domain": domain}, headers=_auth(admin_token))
    assert r.status_code == 201, r.text
    return domain


def test_gis_associate_can_create_a_wms_reference_layer(db, client):
    admin_token, _ = _make_token(db, Role.ADMINISTRATOR)
    gis_token, _ = _make_token(db, Role.GIS_ASSOCIATE)
    domain = _add_domain(db, client, admin_token)
    body = {
        "project_name": f"Ignored {uuid.uuid4()}", "region": "Karnataka",
        "domain": domain, "service_kind": "wms", "path": "/geoserver/wms",
        "layer_name": "test:layer", "is_reference": True,
    }

    r = client.post(
        "/api/v1/projects/00000000-0000-0000-0000-000000000000/external-layers",
        json=body, headers=_auth(gis_token),
    )

    assert r.status_code == 202, r.text
    layer_id = r.json()["layer_id"]
    try:
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT d.dataset_id, d.is_reference, p.name FROM spatial_layer sl "
                "JOIN dataset d ON d.dataset_id = sl.dataset_id "
                "JOIN project p ON p.project_id = d.project_id "
                "WHERE sl.layer_id = %s",
                (layer_id,),
            )
            row = cur.fetchone()
            assert row["is_reference"] is True
            assert row["name"] == REFERENCE_LIBRARY_PROJECT_NAME
    finally:
        _hard_delete_dataset(db, row["dataset_id"])


# --------------------------------------------------------------- add: RBAC rejection (HTTP 403)


def test_viewer_gets_403_uploading_a_reference_layer_via_datasets_upload(db, client, tmp_path):
    viewer_token, _ = _make_token(db, Role.VIEWER)
    staged = _tiny_geotiff(tmp_path)
    with open(staged, "rb") as f:
        r = client.post(
            "/api/v1/datasets/upload",
            headers=_auth(viewer_token),
            files={"file": ("scene.tif", f.read(), "image/tiff")},
            data={
                "project_name": f"Ignored {uuid.uuid4()}", "dataset_type": "Satellite / Raw Imagery",
                "source": "test", "date_processed": "2026-01-01", "is_reference": "true",
            },
        )
    assert r.status_code == 403, r.text


def test_viewer_gets_403_creating_a_wms_reference_layer(db, client):
    admin_token, _ = _make_token(db, Role.ADMINISTRATOR)
    viewer_token, _ = _make_token(db, Role.VIEWER)
    domain = _add_domain(db, client, admin_token)
    body = {
        "project_name": f"Ignored {uuid.uuid4()}", "region": "Karnataka",
        "domain": domain, "service_kind": "wms", "path": "/geoserver/wms",
        "layer_name": "test:layer", "is_reference": True,
    }

    r = client.post(
        "/api/v1/projects/00000000-0000-0000-0000-000000000000/external-layers",
        json=body, headers=_auth(viewer_token),
    )

    assert r.status_code == 403, r.text


# --------------------------------------------------------------- cross-project visibility


def test_reference_layer_appears_on_a_different_unrelated_projects_layer_listing(db, tmp_path):
    project_id, dataset_id = _ingest_reference_raster(db, tmp_path, actor_role=Role.GIS_ASSOCIATE)
    try:
        layer_id = _layer_id_for_dataset(db, dataset_id)

        other_project_id = _make_project(db, f"Unrelated Project {uuid.uuid4()}")
        assert other_project_id != project_id  # genuinely a different, unrelated project

        settings = get_settings()
        storage = LocalStorage(str(tmp_path / "storage"))
        svc = ProjectService(db, settings, storage)
        admin = CurrentUser(user_id=uuid.uuid4(), username="admin-viewer", role=Role.ADMINISTRATOR)

        layers = svc.get_layers(other_project_id, admin)

        assert any(str(lyr.layer_id) == str(layer_id) for lyr in layers.layers)
    finally:
        _hard_delete_dataset(db, dataset_id)


# --------------------------------------------------------------- remove (DELETE /reference-layers/{id})


def test_administrator_can_remove_a_reference_layer_and_it_disappears_everywhere(db, client, tmp_path):
    project_id, dataset_id = _ingest_reference_raster(db, tmp_path, actor_role=Role.GIS_ASSOCIATE)
    layer_id = _layer_id_for_dataset(db, dataset_id)
    other_project_id = _make_project(db, f"Unrelated Project {uuid.uuid4()}")
    admin_token, _ = _make_token(db, Role.ADMINISTRATOR)

    # Visible before deletion.
    before = client.get(f"/api/v1/projects/{other_project_id}/layers", headers=_auth(admin_token))
    assert before.status_code == 200, before.text
    assert any(lyr["layer_id"] == str(layer_id) for lyr in before.json()["layers"])

    r = client.delete(f"/api/v1/reference-layers/{layer_id}", headers=_auth(admin_token))
    assert r.status_code == 204, r.text

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT deleted_at, deleted_by FROM dataset WHERE dataset_id = %s", (str(dataset_id),))
        row = cur.fetchone()
        assert row["deleted_at"] is not None
        assert row["deleted_by"] is not None

    # Calling the OTHER project's listing again afterwards must not crash -
    # it should just silently omit the removed layer.
    after = client.get(f"/api/v1/projects/{other_project_id}/layers", headers=_auth(admin_token))
    assert after.status_code == 200, after.text
    assert not any(lyr["layer_id"] == str(layer_id) for lyr in after.json()["layers"])


def test_gis_associate_cannot_remove_a_reference_layer(db, client, tmp_path):
    """GIS Associate is in UPLOAD_ROLES (can ADD) but not
    MANAGE_REFERENCE_LAYERS_ROLES (cannot REMOVE) - the asymmetry the enums
    module documents."""
    _project_id, dataset_id = _ingest_reference_raster(db, tmp_path, actor_role=Role.GIS_ASSOCIATE)
    try:
        layer_id = _layer_id_for_dataset(db, dataset_id)
        gis_token, _ = _make_token(db, Role.GIS_ASSOCIATE)

        r = client.delete(f"/api/v1/reference-layers/{layer_id}", headers=_auth(gis_token))

        assert r.status_code == 403, r.text
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT deleted_at FROM dataset WHERE dataset_id = %s", (str(dataset_id),))
            assert cur.fetchone()["deleted_at"] is None
    finally:
        _hard_delete_dataset(db, dataset_id)


def test_delete_nonexistent_reference_layer_returns_404(db, client):
    admin_token, _ = _make_token(db, Role.ADMINISTRATOR)
    r = client.delete(
        f"/api/v1/reference-layers/{uuid.uuid4()}", headers=_auth(admin_token)
    )
    assert r.status_code == 404


def test_delete_a_non_reference_project_layer_returns_404_not_403(db, client, tmp_path):
    """A layer_id that exists but was never a reference layer must 404, the
    same as one that doesn't exist at all - never a 403, which would confirm
    an ordinary layer's existence at this endpoint (see
    ReferenceLayerService.remove's own docstring)."""
    settings = get_settings()
    storage = LocalStorage(str(tmp_path / "storage"))
    username = f"reflayer-ordinary-{uuid.uuid4()}"
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
            ctx, job_id=str(job_id), staged_path=staged,
            meta={
                "project_name": f"Ordinary Project {uuid.uuid4()}", "region": "Karnataka",
                "dataset_type": "Satellite / Raw Imagery", "source": "test",
                "classification_method": "", "accuracy_score": None,
                "date_processed": "2026-01-01", "pixel_size_m": 10.0,
                "is_reference": False,
            },
            legend=None,
            actor={"user_id": str(user_id), "username": username, "role": Role.GIS_ASSOCIATE.value},
            request_id=None,
        )
    )
    job = JobService(db, settings).get_for_user(job_id, user_id)
    assert job.status == "succeeded", job.error
    try:
        layer_id = _layer_id_for_dataset(db, job.result["dataset_id"])

        admin_token, _ = _make_token(db, Role.ADMINISTRATOR)
        r = client.delete(f"/api/v1/reference-layers/{layer_id}", headers=_auth(admin_token))

        assert r.status_code == 404, r.text
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT deleted_at FROM dataset WHERE dataset_id = %s", (str(job.result["dataset_id"]),))
            assert cur.fetchone()["deleted_at"] is None  # untouched
    finally:
        _hard_delete_dataset(db, job.result["dataset_id"])
