"""DB-backed tests for the WMS project-name footgun fix - same bug shape as
memory: wave_upload_project_name_footgun, applied to
POST /projects/{id}/external-layers instead of /datasets/upload.

Bug (confirmed live against the ephemeral dmrv-qa stack before this fix, via
WmsService.create_external_layer directly): a project_name that didn't
exactly match an existing project silently created a second, empty-looking
duplicate project instead of erroring - here that mismatch can only come from
stale client state (AddExternalLayerDialog always sends the name of the
project already open on screen), not a typo a user typed, but the fix and its
regression risk are identical to the upload case.

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
from app.repositories.projects import ProjectRepository  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402
from app.repositories.wms_domains import WmsDomainRepository  # noqa: E402
from app.services.ingestion.storage import LocalStorage  # noqa: E402


@pytest.fixture(scope="module")
def db() -> Database:
    d = Database(get_settings())
    d.connect()
    yield d
    d.close()


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
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


def _make_token(db: Database, role: Role) -> str:
    settings = get_settings()
    username = f"wmsfootguntest-{uuid.uuid4()}"
    with db.transaction() as cur:
        row = UserRepository(cur).upsert(username, "x", role.value)
    return create_access_token(
        settings, user_id=str(row["user_id"]), username=username, role=role.value
    )


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_domain(db: Database, actor_user_id) -> str:
    domain = f"geo-{uuid.uuid4().hex[:8]}.example.com"
    with db.transaction() as cur:
        WmsDomainRepository(cur).add(domain, actor_user_id)
    return domain


def _layer_body(project_name: str, domain: str) -> dict:
    return {
        "project_name": project_name, "region": "Karnataka", "domain": domain,
        "service_kind": "wms", "path": "/geoserver/wms", "layer_name": "test:layer",
    }


def test_mismatched_project_name_is_rejected_instead_of_forking_a_duplicate(db, client):
    """The exact reported bug, reproduced over real HTTP: a project already
    exists ("RA GHG"), the request names a mismatched variant ("RA_GHG") -
    this must error, and no second project may exist afterward."""
    gis_token = _make_token(db, Role.GIS_ASSOCIATE)
    with db.transaction() as cur:
        admin_row = UserRepository(cur).upsert(f"wmsadmin-{uuid.uuid4()}", "x", Role.ADMINISTRATOR.value)
    domain = _make_domain(db, admin_row["user_id"])
    real_name = f"RA GHG {uuid.uuid4()}"
    with db.transaction() as cur:
        real_id, _ = ProjectRepository(cur).find_or_create_by_name(real_name, "Karnataka")
    typo_name = real_name.replace(" ", "_")

    r = client.post(
        "/api/v1/projects/00000000-0000-0000-0000-000000000000/external-layers",
        json=_layer_body(typo_name, domain), headers=_auth(gis_token),
    )

    assert r.status_code == 404, r.text
    with db.connection() as conn, conn.cursor() as cur:
        dup = ProjectRepository(cur).get_by_name(typo_name)
        assert dup is None  # no duplicate project created
        real = ProjectRepository(cur).get_by_name(real_name)
        assert real["project_id"] == real_id  # the real project is untouched


def test_name_that_matches_no_project_at_all_is_rejected(db, client):
    """A wholly novel name (not just a typo of an existing project) must also
    error rather than silently create a project."""
    with db.transaction() as cur:
        admin_row = UserRepository(cur).upsert(f"wmsadmin-{uuid.uuid4()}", "x", Role.ADMINISTRATOR.value)
    domain = _make_domain(db, admin_row["user_id"])
    gis_token = _make_token(db, Role.GIS_ASSOCIATE)
    name = f"Never Existed {uuid.uuid4()}"

    r = client.post(
        "/api/v1/projects/00000000-0000-0000-0000-000000000000/external-layers",
        json=_layer_body(name, domain), headers=_auth(gis_token),
    )

    assert r.status_code == 404, r.text
    with db.connection() as conn, conn.cursor() as cur:
        assert ProjectRepository(cur).get_by_name(name) is None


def test_matching_project_name_still_succeeds(db, client):
    """The regression risk this fix must not introduce: adding a WMS layer to
    the SAME correctly-named, already-open project keeps working exactly as
    before - reusing the existing project, not erroring."""
    with db.transaction() as cur:
        admin_row = UserRepository(cur).upsert(f"wmsadmin-{uuid.uuid4()}", "x", Role.ADMINISTRATOR.value)
    domain = _make_domain(db, admin_row["user_id"])
    admin_token = create_access_token(
        get_settings(), user_id=str(admin_row["user_id"]), username=admin_row["username"],
        role=Role.ADMINISTRATOR.value,
    )
    name = f"RA GHG {uuid.uuid4()}"
    with db.transaction() as cur:
        existing_id, _ = ProjectRepository(cur).find_or_create_by_name(name, "Karnataka")

    r = client.post(
        "/api/v1/projects/00000000-0000-0000-0000-000000000000/external-layers",
        json=_layer_body(name, domain), headers=_auth(admin_token),
    )

    assert r.status_code == 202, r.text
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT project_id FROM dataset WHERE dataset_id = ("
            "  SELECT dataset_id FROM spatial_layer WHERE layer_id = %s"
            ")",
            (r.json()["layer_id"],),
        )
        assert cur.fetchone()["project_id"] == existing_id


def test_matching_is_case_insensitive_like_the_rest_of_the_app(db, client):
    """Matches ProjectRepository.find_or_create_by_name's own unique index
    (`lower(name)`) - the same rule test_upload_project_name_footgun.py
    already proves for the upload path, not re-derived here."""
    with db.transaction() as cur:
        admin_row = UserRepository(cur).upsert(f"wmsadmin-{uuid.uuid4()}", "x", Role.ADMINISTRATOR.value)
    domain = _make_domain(db, admin_row["user_id"])
    admin_token = create_access_token(
        get_settings(), user_id=str(admin_row["user_id"]), username=admin_row["username"],
        role=Role.ADMINISTRATOR.value,
    )
    name = f"Ra Ghg {uuid.uuid4()}"
    with db.transaction() as cur:
        ProjectRepository(cur).find_or_create_by_name(name, "Karnataka")

    r = client.post(
        "/api/v1/projects/00000000-0000-0000-0000-000000000000/external-layers",
        json=_layer_body(name.upper(), domain), headers=_auth(admin_token),
    )

    assert r.status_code == 202, r.text
