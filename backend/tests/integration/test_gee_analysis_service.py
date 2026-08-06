"""DB-backed tests for GEEAnalysisService (Wave: GEE analysis registry)
against a REAL PostGIS database - the cache/list/refresh flow and the
project-membership RBAC gate (require_project_view / require_project_upload)
need real transaction semantics, so this is not faked (see
test_forest_definition.py / test_adhoc_layers.py for the same skip-guard
convention this file follows).

`_compute` - the ONLY function that actually calls Google Earth Engine (see
app/services/gee_analysis_service.py's own docstring) - is monkeypatched to a
canned result for every test here, so this suite is fast/deterministic/CI-safe
and needs no GEE credentials. The 5 real query functions were verified
separately, end-to-end, against the live ee-kdenish4 project.

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
from app.core.errors import ForbiddenError, NotFoundError, ValidationError  # noqa: E402
from app.domain.dtos import CurrentUser  # noqa: E402
from app.domain.enums import Role  # noqa: E402
from app.repositories.datasets import DatasetRepository, LayerRepository  # noqa: E402
from app.repositories.memberships import ProjectMembershipRepository  # noqa: E402
from app.repositories.projects import ProjectRepository  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402
from app.repositories.vector_layers import VectorFeatureRepository  # noqa: E402
from app.services import gee_analysis_service as svc_module  # noqa: E402
from app.services.gee_analysis_service import GEEAnalysisService  # noqa: E402

_CANNED = (
    {"class_area_ha": {"trees": 12.5}},
    [{"code": 1, "name": "trees", "color": "#000"}],
    "https://tiles.example/{z}/{x}/{y}",
)


@pytest.fixture(scope="module")
def db() -> Database:
    d = Database(get_settings())
    d.connect()
    yield d
    d.close()


@pytest.fixture
def analysis_service(db) -> GEEAnalysisService:
    return GEEAnalysisService(db)


@pytest.fixture(autouse=True)
def fake_compute(monkeypatch):
    """Every test in this module gets a deterministic, network-free
    `_compute`. `calls` records (analysis_id, canopy_cover_pct) per
    invocation, so a test can assert GEE was - or was NOT - ever reached.

    `init_ee()` and `ee.Geometry(...)` are ALSO stubbed out here: refresh()
    calls both unconditionally before ever reaching `_compute` (see
    gee_analysis_service.py) - even *constructing* an ee.Geometry lazily
    initializes the EE API function registry on first use, so patching
    `_compute` alone still requires a real, authenticated GEE session on
    whatever machine runs this suite, the opposite of "CI-safe without
    credentials". Bug filed in the QA report; stubbed here so this suite
    doesn't depend on either one."""
    calls: list[tuple[str, float]] = []

    def fake(analysis_id, boundary, canopy_cover_pct):
        calls.append((analysis_id, canopy_cover_pct))
        return _CANNED

    monkeypatch.setattr(svc_module, "_compute", fake)
    monkeypatch.setattr(svc_module, "init_ee", lambda: None)
    monkeypatch.setattr(svc_module.ee, "Geometry", lambda geojson: geojson)
    return calls


def _make_user(db: Database, role: Role) -> CurrentUser:
    username = f"geetest-{role.name.lower()}-{uuid.uuid4()}"
    with db.transaction() as cur:
        row = UserRepository(cur).upsert(username, "x", role.value)
    return CurrentUser(user_id=row["user_id"], username=username, role=role)


def _make_project(db: Database, name: str) -> uuid.UUID:
    with db.transaction() as cur:
        project_id, _created = ProjectRepository(cur).find_or_create_by_name(name, "Karnataka")
    return project_id


def _add_member(db: Database, project_id, user: CurrentUser, role: Role, added_by: CurrentUser) -> None:
    with db.transaction() as cur:
        ProjectMembershipRepository(cur).add(
            project_id=project_id, user_id=user.user_id, role=role, added_by=added_by.user_id
        )


def _add_boundary(db: Database, project_id) -> None:
    with db.transaction() as cur:
        dataset_id = DatasetRepository(cur).insert(
            project_id=project_id, dataset_type="Boundary", source="test",
            accuracy_score=None, date_processed="2026-01-01", batch_id=uuid.uuid4(),
        )
        layer_id = LayerRepository(cur).insert_non_raster(
            dataset_id=dataset_id, layer_kind="vector", crs="EPSG:4326",
            bounds=(0.0, 0.0, 1.0, 1.0),
        )
        VectorFeatureRepository(cur).insert_many(
            layer_id,
            [(
                {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                },
                {},
            )],
        )


# --------------------------------------------------------------- catalog listing


def test_get_project_analyses_lists_all_thirteen_with_no_computed_at_before_any_refresh(
    db, analysis_service
):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")

    result = analysis_service.get_project_analyses(pid, admin)

    assert len(result.analyses) == 13
    assert all(a.computed_at is None for a in result.analyses)


# --------------------------------------------------------------- refresh - happy path


def test_refresh_upserts_and_returns_the_row_and_audit_logs(db, analysis_service, fake_compute):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    _add_boundary(db, pid)

    result = analysis_service.refresh(pid, "hansen_gfc", admin)

    assert result.project_id == pid
    assert result.analysis_id == "hansen_gfc"
    assert result.stats == _CANNED[0]
    assert result.legend == _CANNED[1]
    assert result.tile_url_template == _CANNED[2]
    assert len(fake_compute) == 1
    assert fake_compute[0][0] == "hansen_gfc"
    assert isinstance(fake_compute[0][1], float)  # the live canopy-cover threshold

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT actor_id, action, project_id, detail FROM audit_log "
            "WHERE action = 'refresh_analysis' AND project_id = %s ORDER BY created_at DESC LIMIT 1",
            (str(pid),),
        )
        row = cur.fetchone()
    assert row is not None
    assert str(row["actor_id"]) == str(admin.user_id)
    assert str(row["project_id"]) == str(pid)


def test_get_project_analyses_shows_computed_at_only_for_the_refreshed_id(db, analysis_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    _add_boundary(db, pid)
    analysis_service.refresh(pid, "hansen_gfc", admin)

    result = analysis_service.get_project_analyses(pid, admin)

    by_id = {a.id: a.computed_at for a in result.analyses}
    assert by_id["hansen_gfc"] is not None
    assert by_id["dynamic_world"] is None


def test_get_result_404s_before_refresh_and_returns_cached_row_after(db, analysis_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    _add_boundary(db, pid)

    with pytest.raises(NotFoundError):
        analysis_service.get_result(pid, "hansen_gfc", admin)

    analysis_service.refresh(pid, "hansen_gfc", admin)

    result = analysis_service.get_result(pid, "hansen_gfc", admin)
    assert result.stats == _CANNED[0]


# --------------------------------------------------------------- refresh - rejection paths


def test_refresh_unknown_analysis_id_is_not_found(db, analysis_service, fake_compute):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")

    with pytest.raises(NotFoundError):
        analysis_service.refresh(pid, "not-a-real-analysis", admin)

    assert fake_compute == []  # rejected before ever reaching GEE


def test_refresh_in_development_analysis_id_is_a_validation_error(db, analysis_service, fake_compute):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    _add_boundary(db, pid)

    with pytest.raises(ValidationError):
        analysis_service.refresh(pid, "ndvi", admin)

    assert fake_compute == []  # rejected before ever reaching GEE


def test_refresh_without_a_boundary_layer_is_a_validation_error(db, analysis_service, fake_compute):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")  # no boundary layer inserted

    with pytest.raises(ValidationError):
        analysis_service.refresh(pid, "hansen_gfc", admin)

    assert fake_compute == []  # never called GEE with no AOI


# --------------------------------------------------------------- permission gating


def test_viewer_can_read_but_refresh_is_forbidden(db, analysis_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    _add_boundary(db, pid)
    viewer = _make_user(db, Role.VIEWER)
    _add_member(db, pid, viewer, Role.VIEWER, added_by=admin)

    analysis_service.get_project_analyses(pid, viewer)  # GET-tier: fine for a Viewer

    with pytest.raises(ForbiddenError):
        analysis_service.refresh(pid, "hansen_gfc", viewer)


def test_gis_associate_project_role_can_refresh(db, analysis_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    _add_boundary(db, pid)
    analyst = _make_user(db, Role.ANALYST)  # global role, irrelevant here
    _add_member(db, pid, analyst, Role.GIS_ASSOCIATE, added_by=admin)  # project role IS

    result = analysis_service.refresh(pid, "hansen_gfc", analyst)
    assert result.analysis_id == "hansen_gfc"


def test_administrator_can_refresh_without_any_membership_row(db, analysis_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    _add_boundary(db, pid)  # admin was never added as a member of this project

    result = analysis_service.refresh(pid, "hansen_gfc", admin)
    assert result.analysis_id == "hansen_gfc"


def test_user_with_no_membership_gets_not_found_not_a_leaky_forbidden(db, analysis_service):
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")  # exists, actor never added
    outsider = _make_user(db, Role.GIS_ASSOCIATE)  # global role would pass UPLOAD_ROLES...

    with pytest.raises(NotFoundError):
        analysis_service.get_project_analyses(pid, outsider)

    with pytest.raises(NotFoundError):  # ...but no membership -> 404, never a 403
        analysis_service.refresh(pid, "hansen_gfc", outsider)
