"""DB-backed tests for GEEAnalysisService (Wave: GEE analysis registry)
against a REAL PostGIS database - the cache/list/refresh flow and the
project-membership RBAC gate (require_project_view / require_project_upload)
need real transaction semantics, so this is not faked (see
test_forest_definition.py / test_adhoc_layers.py for the same skip-guard
convention this file follows).

`_compute` - the ONLY function that actually calls Google Earth Engine (see
app/services/gee_analysis_service.py's own docstring) - is monkeypatched to a
canned result for every test here, so this suite is fast/deterministic/CI-safe
and needs no GEE credentials. The 10 real query functions were verified
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
from app.domain.dtos import AnalysisPointValue, CurrentUser  # noqa: E402
from app.domain.enums import Role  # noqa: E402
from app.repositories.analysis_results import AnalysisResultRepository  # noqa: E402
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
    `_compute`. `calls` records (analysis_id, canopy_cover_pct, request_params)
    per invocation, so a test can assert GEE was - or was NOT - ever reached,
    and (Wave: raw-imagery browsing) what request_params a browse refresh
    actually passed through.

    `init_ee()` and `ee.Geometry(...)` are ALSO stubbed out here: refresh()
    calls both unconditionally before ever reaching `_compute` (see
    gee_analysis_service.py) - even *constructing* an ee.Geometry lazily
    initializes the EE API function registry on first use, so patching
    `_compute` alone still requires a real, authenticated GEE session on
    whatever machine runs this suite, the opposite of "CI-safe without
    credentials". Bug filed in the QA report; stubbed here so this suite
    doesn't depend on either one."""
    calls: list[tuple[str, float, dict | None]] = []

    def fake(analysis_id, boundary, canopy_cover_pct, request_params=None):
        calls.append((analysis_id, canopy_cover_pct, request_params))
        return _CANNED

    monkeypatch.setattr(svc_module, "_compute", fake)
    monkeypatch.setattr(svc_module, "init_ee", lambda: None)
    monkeypatch.setattr(svc_module.ee, "Geometry", lambda geojson: geojson)
    return calls


_CANNED_POINT = {"value": 4, "class_name": "crops", "class_color": "#E49635"}


@pytest.fixture
def fake_compute_point(monkeypatch):
    """get_point_value()'s analog of `fake_compute` above - stubs
    _compute_point (the only function get_point_value() calls that touches
    GEE) with a canned result, recording every (analysis_id, lon, lat) call
    so a test can assert the "not yet computed" guard rejected a request
    BEFORE ever reaching this stub."""
    calls: list[tuple[str, float, float]] = []

    def fake(analysis_id, boundary, canopy_cover_pct, lon, lat, request_params=None):
        calls.append((analysis_id, lon, lat))
        return _CANNED_POINT

    monkeypatch.setattr(svc_module, "_compute_point", fake)
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


def test_get_project_analyses_lists_all_nineteen_with_no_computed_at_before_any_refresh(
    db, analysis_service
):
    # 16 original + 3 Raw Imagery (Wave: raw-imagery browsing:
    # s2_browse/s1_browse/landsat_browse).
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")

    result = analysis_service.get_project_analyses(pid, admin)

    assert len(result.analyses) == 19
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

    # "sar" - still in-development (unlike "ndvi"/"evi"/"savi"/"mndwi"/"nbr",
    # which this test used before Wave: vegetation indices made them real).
    with pytest.raises(ValidationError):
        analysis_service.refresh(pid, "sar", admin)

    assert fake_compute == []  # rejected before ever reaching GEE


def test_refresh_without_a_boundary_layer_is_a_validation_error(db, analysis_service, fake_compute):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")  # no boundary layer inserted

    with pytest.raises(ValidationError):
        analysis_service.refresh(pid, "hansen_gfc", admin)

    assert fake_compute == []  # never called GEE with no AOI


@pytest.mark.parametrize("analysis_id", ["ndvi", "evi", "savi", "mndwi", "nbr"])
def test_refresh_rejects_an_async_execution_analysis(db, analysis_service, fake_compute, analysis_id):
    """refresh() is the "sync"-execution path only (see its own docstring's
    routing-invariant assert) - the route (app/api/v1/analyses.py) sends every
    vegetation index through enqueue_refresh() instead, since all five share
    NDVI's "async" execution mode. This proves that invariant holds for the
    boundary/permission-validated request path, not just NDVI, so a future
    catalog edit that flips one back to "sync" without updating its query
    function fails loudly here rather than silently in production."""
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    _add_boundary(db, pid)

    with pytest.raises(AssertionError):
        analysis_service.refresh(pid, analysis_id, admin)

    assert fake_compute == []  # never reached _compute - failed on the routing assert first


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


# --------------------------------------------------------------- get_point_value (Identify)


def test_get_point_value_sync_analysis_returns_a_real_value_immediately(
    db, analysis_service, fake_compute_point
):
    # "sync" (dynamic_world here) analyses need no prior refresh() at all -
    # unlike the async ones below, Identify can query them cold.
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    _add_boundary(db, pid)

    # (0.5, 0.5) - inside _add_boundary's [0,0]-[1,1] unit-square boundary,
    # not just anywhere - Wave: AOI clip's boundary_contains_point gate now
    # rejects an outside point before ever reaching fake_compute_point.
    result = analysis_service.get_point_value(pid, "dynamic_world", lon=0.5, lat=0.5, actor=admin)

    assert isinstance(result, AnalysisPointValue)
    assert result.analysis_id == "dynamic_world"
    assert result.lon == 0.5
    assert result.lat == 0.5
    assert result.class_name == "crops"
    assert result.class_color == "#E49635"
    assert result.outside_boundary is False
    assert fake_compute_point == [("dynamic_world", 0.5, 0.5)]


def test_get_point_value_async_analysis_before_any_refresh_is_a_clear_validation_error(
    db, analysis_service, fake_compute, fake_compute_point
):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    _add_boundary(db, pid)

    with pytest.raises(ValidationError) as exc_info:
        # (0.5, 0.5) - inside the boundary, so this raises for the intended
        # reason (not yet computed), not because of the boundary_contains_point
        # gate that now runs first.
        analysis_service.get_point_value(pid, "ndvi", lon=0.5, lat=0.5, actor=admin)

    # Exact text (not a substring match) - this is what actually reaches
    # Identify's popup verbatim (ProjectMap.jsx's renderGeePixelRow renders
    # `error` as-is), so a wording regression here is a real user-facing bug,
    # not just an internal message drifting.
    assert exc_info.value.message == (
        "Run 'NDVI' first - it hasn't been computed for this project yet."
    )
    assert exc_info.value.status_code == 422
    assert fake_compute_point == []  # never reached the point-query path...
    assert fake_compute == []  # ...and never reached the expensive refresh/compute path either


def test_get_point_value_async_analysis_after_refresh_returns_a_real_value_without_recomputing(
    db, analysis_service, fake_compute, fake_compute_point
):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    _add_boundary(db, pid)
    # Simulate the worker's own upsert (app/workers/gee_analysis_jobs.py) directly,
    # rather than the full job-queue/runner plumbing enqueue_refresh() drives -
    # this test is about get_point_value()'s cache check, not job dispatch.
    with db.transaction() as cur:
        AnalysisResultRepository(cur).upsert(
            project_id=pid, analysis_id="ndvi", computed_by=admin.user_id,
            stats=_CANNED[0], legend=_CANNED[1], tile_url_template=_CANNED[2],
        )

    # (0.5, 0.5) - inside the boundary; see the sibling test above for why.
    result = analysis_service.get_point_value(pid, "ndvi", lon=0.5, lat=0.5, actor=admin)

    assert result.class_name == "crops"  # from the canned _compute_point stub
    assert fake_compute_point == [("ndvi", 0.5, 0.5)]
    # The whole point of the cache check above: a cached result lets
    # Identify read a real value WITHOUT re-running _compute() - the
    # expensive multi-year _annual_index_series() path _compute() drives for
    # NDVI/EVI/SAVI/MNDWI/NBR (5-59s measured, see gee_analysis_service.py's
    # own module docstring) is never touched by a pixel click.
    assert fake_compute == []


def test_get_point_value_unknown_analysis_id_is_not_found(db, analysis_service, fake_compute_point):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    _add_boundary(db, pid)

    with pytest.raises(NotFoundError):
        analysis_service.get_point_value(pid, "not-a-real-analysis", lon=0, lat=0, actor=admin)

    assert fake_compute_point == []


def test_get_point_value_in_development_analysis_id_is_a_validation_error(
    db, analysis_service, fake_compute_point
):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    _add_boundary(db, pid)

    with pytest.raises(ValidationError):
        analysis_service.get_point_value(pid, "sar", lon=0, lat=0, actor=admin)

    assert fake_compute_point == []


def test_get_point_value_without_a_boundary_layer_is_a_validation_error(
    db, analysis_service, fake_compute_point
):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")  # no boundary layer inserted

    with pytest.raises(ValidationError):
        analysis_service.get_point_value(pid, "dynamic_world", lon=0, lat=0, actor=admin)

    assert fake_compute_point == []


def test_get_point_value_is_readable_by_a_viewer(db, analysis_service, fake_compute_point):
    # Identify is a read, not a write - Viewer is enough (require_project_view,
    # same tier get_result()/get_project_analyses() already use), unlike
    # refresh() which needs an upload-tier role.
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    _add_boundary(db, pid)
    viewer = _make_user(db, Role.VIEWER)
    _add_member(db, pid, viewer, Role.VIEWER, added_by=admin)

    # (0.5, 0.5), not (0,0) - (0,0) is a VERTEX of _add_boundary's polygon,
    # and ST_Contains excludes the boundary itself, so it would trip the
    # outside_boundary gate before reaching fake_compute_point at all.
    result = analysis_service.get_point_value(pid, "dynamic_world", lon=0.5, lat=0.5, actor=viewer)
    assert result.analysis_id == "dynamic_world"
    assert result.outside_boundary is False
    assert result.class_name == "crops"


# --------------------------------------------------------------- boundary_contains_point gate


def test_get_point_value_outside_the_boundary_never_reaches_gee(
    db, analysis_service, fake_compute, fake_compute_point
):
    """The actual regression this gate exists for: a click outside the AOI
    (which the map tile now legitimately renders as empty, since Wave: AOI
    clip) must come back as a normal, typed outside_boundary=True response -
    not a real GEE-derived value, and not an error either."""
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    _add_boundary(db, pid)  # unit square [0,0]-[1,1]

    result = analysis_service.get_point_value(pid, "dynamic_world", lon=5.0, lat=5.0, actor=admin)

    assert result.analysis_id == "dynamic_world"
    assert result.lon == 5.0
    assert result.lat == 5.0
    assert result.outside_boundary is True
    assert result.value is None
    assert result.class_name is None
    assert fake_compute_point == []  # never reached the expensive point-query path
    assert fake_compute == []  # ...and never touched GEE at all


def test_get_point_value_outside_the_boundary_is_checked_before_the_async_cache_gate(
    db, analysis_service, fake_compute, fake_compute_point
):
    """Order matters: an outside-boundary click on an async (never-refreshed)
    analysis must report outside_boundary=True, not the "run this first"
    validation error - the boundary check is the cheaper, more fundamental
    gate and runs first (see get_point_value's own comment)."""
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    _add_boundary(db, pid)

    result = analysis_service.get_point_value(pid, "ndvi", lon=5.0, lat=5.0, actor=admin)

    assert result.outside_boundary is True
    assert fake_compute_point == []
    assert fake_compute == []


def test_get_point_value_user_with_no_membership_gets_not_found(
    db, analysis_service, fake_compute_point
):
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    outsider = _make_user(db, Role.GIS_ASSOCIATE)

    with pytest.raises(NotFoundError):
        analysis_service.get_point_value(pid, "dynamic_world", lon=0, lat=0, actor=outsider)
