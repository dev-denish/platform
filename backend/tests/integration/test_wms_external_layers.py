"""DB-backed integration tests (Wave: multi-format layers, Part B) for the
WMS/WFS domain allow-list and external-layer creation, driven over REAL HTTP
(FastAPI TestClient) against a REAL PostGIS database - unlike the service-only
convention in test_project_membership.py/test_vector_ingest.py, RBAC here is
specifically about which HTTP verb+role combination is wired to which
dependency (`UPLOAD_ROLES` for GET/create, `MANAGE_WMS_SOURCES_ROLES` for
POST/DELETE on the allow-list itself), so this exercises the actual route
wiring + a real minted JWT, not just the service method.

The app is NOT started through its DB-opening lifespan (same reason
conftest.py's `client` fixture avoids it): `app.state` is populated directly
with a real, already-connected `Database`, so no separate lifespan/Redis is
needed - only `TestClient(app)` used WITHOUT the `with` statement, exactly
like conftest.py, so lifespan startup/shutdown never fires.

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

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.domain.enums import Role  # noqa: E402
from app.main import create_app  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402
from app.services.ingestion.storage import LocalStorage  # noqa: E402


@pytest.fixture(scope="module")
def db() -> Database:
    d = Database(get_settings())
    d.connect()
    yield d
    d.close()


class _NullTaskRunner:
    """None of these tests submit an ingest job (the CSV-validation test fails
    before reaching job submission; everything else is wms-domains/
    external-layers routes, which never touch a TaskRunner at all) - a runner
    that errors if actually invoked catches a test accidentally exercising a
    path it didn't mean to."""

    async def run(self, fn, /, *args, **kwargs):
        raise AssertionError("no test in this module should reach job dispatch")

    def shutdown(self) -> None: ...


@pytest.fixture
def client(db, tmp_path) -> TestClient:
    # create_app() mounts a StaticFiles dir at settings.local_data_dir
    # unconditionally for storage_backend == "local" - the production default
    # (/var/lib/dmrv/data) isn't writable/mountable in a test process, so this
    # points both storage dirs at a throwaway tmp_path instead of touching it.
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


def _make_token(db: Database, role: Role) -> str:
    settings = get_settings()
    username = f"wmstest-{uuid.uuid4()}"
    with db.transaction() as cur:
        row = UserRepository(cur).upsert(username, "x", role.value)
    return create_access_token(
        settings, user_id=str(row["user_id"]), username=username, role=role.value
    )


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _cleanup_domain(db: Database, domain: str) -> None:
    with db.transaction() as cur:
        cur.execute("DELETE FROM allowed_wms_domain WHERE domain = %s", (domain,))


# --------------------------------------------------------------- allow-list RBAC


def test_administrator_can_add_a_wms_domain(db, client):
    admin_token = _make_token(db, Role.ADMINISTRATOR)
    domain = f"geo-{uuid.uuid4().hex[:8]}.example.com"
    try:
        r = client.post("/api/v1/wms-domains", json={"domain": domain}, headers=_auth(admin_token))
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["domain"] == domain
        assert body["domain_id"]
    finally:
        _cleanup_domain(db, domain)


def test_non_administrator_cannot_add_a_wms_domain(db, client):
    """GIS Associate passes UPLOAD_ROLES (can read the list, can create a
    layer FROM an approved domain) but must never be able to GROW the list -
    MANAGE_WMS_SOURCES_ROLES is Administrator-only."""
    gis_token = _make_token(db, Role.GIS_ASSOCIATE)
    domain = f"geo-{uuid.uuid4().hex[:8]}.example.com"

    r = client.post("/api/v1/wms-domains", json={"domain": domain}, headers=_auth(gis_token))

    assert r.status_code == 403
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM allowed_wms_domain WHERE domain = %s", (domain,))
        assert cur.fetchone() is None  # never written


def test_get_wms_domains_works_for_any_upload_role(db, client):
    admin_token = _make_token(db, Role.ADMINISTRATOR)
    gis_token = _make_token(db, Role.GIS_ASSOCIATE)
    domain = f"geo-{uuid.uuid4().hex[:8]}.example.com"
    client.post("/api/v1/wms-domains", json={"domain": domain}, headers=_auth(admin_token))
    try:
        r_admin = client.get("/api/v1/wms-domains", headers=_auth(admin_token))
        r_gis = client.get("/api/v1/wms-domains", headers=_auth(gis_token))
        assert r_admin.status_code == 200
        assert r_gis.status_code == 200
        assert domain in {d["domain"] for d in r_gis.json()}
    finally:
        _cleanup_domain(db, domain)


def test_get_wms_domains_rejects_a_role_outside_upload_roles(db, client):
    viewer_token = _make_token(db, Role.VIEWER)
    r = client.get("/api/v1/wms-domains", headers=_auth(viewer_token))
    assert r.status_code == 403


def test_get_wms_domains_requires_authentication(client):
    r = client.get("/api/v1/wms-domains")
    assert r.status_code == 401


def test_administrator_can_delete_a_wms_domain(db, client):
    admin_token = _make_token(db, Role.ADMINISTRATOR)
    domain = f"geo-{uuid.uuid4().hex[:8]}.example.com"
    created = client.post(
        "/api/v1/wms-domains", json={"domain": domain}, headers=_auth(admin_token)
    ).json()

    r = client.delete(f"/api/v1/wms-domains/{created['domain_id']}", headers=_auth(admin_token))

    assert r.status_code == 204
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM allowed_wms_domain WHERE domain = %s", (domain,))
        assert cur.fetchone() is None


def test_non_administrator_cannot_delete_a_wms_domain(db, client):
    admin_token = _make_token(db, Role.ADMINISTRATOR)
    gis_token = _make_token(db, Role.GIS_ASSOCIATE)
    domain = f"geo-{uuid.uuid4().hex[:8]}.example.com"
    created = client.post(
        "/api/v1/wms-domains", json={"domain": domain}, headers=_auth(admin_token)
    ).json()
    try:
        r = client.delete(f"/api/v1/wms-domains/{created['domain_id']}", headers=_auth(gis_token))
        assert r.status_code == 403
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM allowed_wms_domain WHERE domain = %s", (domain,))
            assert cur.fetchone() is not None  # still there
    finally:
        _cleanup_domain(db, domain)


# --------------------------------------------------------------- external-layer creation


def test_creating_external_layer_on_a_non_allowlisted_domain_is_rejected(db, client):
    """The endpoint itself is reachable by a GIS Associate (UPLOAD_ROLES) -
    the rejection is specifically about the domain not being on the
    Administrator-managed allow-list, proven by getting past RBAC (not a
    403-for-role) but still failing."""
    gis_token = _make_token(db, Role.GIS_ASSOCIATE)
    body = {
        "project_name": f"WMS Project {uuid.uuid4()}", "region": "Karnataka",
        "domain": "not-an-approved-domain.example.com", "service_kind": "wms",
        "path": "/geoserver/wms", "layer_name": "test:layer",
    }

    r = client.post(
        "/api/v1/projects/00000000-0000-0000-0000-000000000000/external-layers",
        json=body, headers=_auth(gis_token),
    )

    assert r.status_code == 403, r.text
    assert "allow-list" in r.json()["error"]["message"]


def test_creating_external_layer_on_an_allowlisted_domain_succeeds(db, client):
    admin_token = _make_token(db, Role.ADMINISTRATOR)
    gis_token = _make_token(db, Role.GIS_ASSOCIATE)
    domain = f"geo-{uuid.uuid4().hex[:8]}.example.com"
    client.post("/api/v1/wms-domains", json={"domain": domain}, headers=_auth(admin_token))
    body = {
        "project_name": f"WMS Project {uuid.uuid4()}", "region": "Karnataka",
        "domain": domain, "service_kind": "wms", "path": "/geoserver/wms",
        "layer_name": "test:layer",
    }
    try:
        r = client.post(
            "/api/v1/projects/00000000-0000-0000-0000-000000000000/external-layers",
            json=body, headers=_auth(gis_token),
        )
        assert r.status_code == 202, r.text
        layer_id = r.json()["layer_id"]

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT layer_kind FROM spatial_layer WHERE layer_id = %s", (layer_id,)
            )
            assert cur.fetchone()["layer_kind"] == "external_wms"
    finally:
        _cleanup_domain(db, domain)


def test_creating_external_layer_requires_upload_role(db, client):
    viewer_token = _make_token(db, Role.VIEWER)
    body = {
        "project_name": f"WMS Project {uuid.uuid4()}", "region": "Karnataka",
        "domain": "whatever.example.com", "service_kind": "wms",
        "path": "/geoserver/wms", "layer_name": "test:layer",
    }
    r = client.post(
        "/api/v1/projects/00000000-0000-0000-0000-000000000000/external-layers",
        json=body, headers=_auth(viewer_token),
    )
    assert r.status_code == 403


# --------------------------------------------------------------- /datasets/upload CSV validation


def test_upload_csv_with_nonexistent_lat_column_is_rejected_by_the_endpoint_itself(db, client):
    """The endpoint's own pre-job check (api/v1/datasets.py, validated against
    the REAL uploaded file's header via csv_header()) - distinct from
    IngestionService/vector.parse_csv_points, which only run once a job
    actually executes. This must 4xx before a job row even exists."""
    gis_token = _make_token(db, Role.GIS_ASSOCIATE)
    csv_bytes = b"site,lat,lon\nA,13.0,76.0\n"

    r = client.post(
        "/api/v1/datasets/upload",
        headers=_auth(gis_token),
        files={"file": ("plots.csv", csv_bytes, "text/csv")},
        data={
            "project_name": f"CSV Endpoint {uuid.uuid4()}", "dataset_type": "Boundary",
            "source": "test", "date_processed": "2026-01-01",
            "lat_column": "not_a_real_column", "lon_column": "lon",
        },
    )

    assert r.status_code == 422, r.text
    assert "not_a_real_column" in r.json()["error"]["message"]
