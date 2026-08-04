"""DB-backed integration tests for the per-project recent-activity feed
(GET /projects/{id}/activity) - real audit_log rows, real project_id
scoping (migration 0014_audit_log_project_id), so this needs the real
database, not a faked repository.

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
from app.domain.enums import AuditAction, Role  # noqa: E402
from app.main import create_app  # noqa: E402
from app.repositories.audit import AuditRepository  # noqa: E402
from app.repositories.projects import ProjectRepository  # noqa: E402
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
        raise AssertionError("no request in this test dispatches a background job")

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
    username = f"activitytest-{uuid.uuid4()}"
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
    """Ingests one ordinary raster layer through the real job function (the
    real INGEST_DATASET audit write, project_id and all). Returns
    (project_id, layer_id)."""
    settings = get_settings()
    storage = LocalStorage(str(tmp_path / "storage"))
    username = f"activity-ingest-{uuid.uuid4()}"
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
                "project_name": f"Activity Test Project {uuid.uuid4()}", "region": "Karnataka",
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
    return job.result["project_id"], user_id


def test_activity_feed_shows_own_project_action_and_excludes_other_project(db, client, tmp_path):
    # Real write path: ingesting a layer records INGEST_DATASET with THIS
    # project's id (see IngestionService._ingest_raster's threaded
    # project_id=project_id).
    project_a_id, _actor_id = _ingest_layer(db, tmp_path)

    # A second project's activity - must never leak into project A's feed.
    with db.transaction() as cur:
        project_b_id, _created = ProjectRepository(cur).find_or_create_by_name(
            f"Activity Test Project B {uuid.uuid4()}", "Karnataka"
        )
        AuditRepository(cur).record(
            actor_id=None, actor_name="someone-else",
            action=AuditAction.RENAME_LAYER, target="unrelated-layer",
            detail="renamed in project B", project_id=project_b_id,
        )

    admin_token, _ = _make_token(db, Role.ADMINISTRATOR)
    r = client.get(f"/api/v1/projects/{project_a_id}/activity", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    items = r.json()["items"]

    # Two of project A's OWN actions land here: the ingest itself, plus the
    # auto-add-as-first-member ADD_PROJECT_MEMBER a brand-new project's
    # first upload always writes (see project_access.resolve_project_for_upload)
    # - project B's decoy RENAME_LAYER row must never appear among them.
    actions = {i["action"] for i in items}
    assert actions == {"ingest_dataset", "add_project_member"}
    assert all(i["action"] != "rename_layer" for i in items)
    ingest_item = next(i for i in items if i["action"] == "ingest_dataset")
    assert ingest_item["target"] is not None
    assert "created_at" in ingest_item


def test_activity_feed_requires_project_membership(db, client, tmp_path):
    project_a_id, _actor_id = _ingest_layer(db, tmp_path)
    outsider_token, _ = _make_token(db, Role.GIS_ASSOCIATE)

    r = client.get(f"/api/v1/projects/{project_a_id}/activity", headers=_auth(outsider_token))
    assert r.status_code == 404, r.text
