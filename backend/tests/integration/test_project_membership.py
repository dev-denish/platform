"""DB-backed tests for project-level membership RBAC against a REAL PostGIS
database - the partial-unique-index race, the Administrator bypass, and the
"project role, not global role" rule all need real transaction/constraint
semantics, so this is not faked (see test_db_repositories.py for the same
skip-guard convention this file follows).

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
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError  # noqa: E402
from app.domain.dtos import CurrentUser, IngestMetadata  # noqa: E402
from app.domain.enums import Role  # noqa: E402
from app.repositories.datasets import DatasetRepository, KpiRepository  # noqa: E402
from app.repositories.memberships import ProjectMembershipRepository  # noqa: E402
from app.repositories.projects import ProjectRepository  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402
from app.services.ingestion.service import IngestionService  # noqa: E402
from app.services.membership_service import MembershipService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402


@pytest.fixture(scope="module")
def db() -> Database:
    d = Database(get_settings())
    d.connect()
    yield d
    d.close()


@pytest.fixture
def project_service(db) -> ProjectService:
    return ProjectService(db, get_settings(), object())


@pytest.fixture
def membership_service(db) -> MembershipService:
    return MembershipService(db)


@pytest.fixture
def ingestion_service(db) -> IngestionService:
    return IngestionService(db, get_settings(), object())


def _make_user(db: Database, role: Role) -> CurrentUser:
    username = f"membertest-{uuid.uuid4()}"
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


# --------------------------------------------------------------- no backfill


def test_freshly_created_project_has_zero_membership_rows(db):
    """The wave's own rollout note: NO backfill. A project that exists right
    now (this one, created seconds ago by this very test) still has zero
    membership rows - that is the intended state for every pre-existing
    project the moment this ships, not a defect."""
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM project_membership WHERE project_id = %s", (str(pid),))
        n = cur.fetchone()["n"]

    assert n == 0


# --------------------------------------------------------------- view access


def test_non_admin_without_membership_gets_404_on_every_read(db, project_service):
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    outsider = _make_user(db, Role.ANALYST)

    with pytest.raises(NotFoundError):
        project_service.get_project(pid, outsider)
    with pytest.raises(NotFoundError):
        project_service.get_kpis(pid, outsider)
    with pytest.raises(NotFoundError):
        project_service.get_layers(pid, outsider)
    with pytest.raises(NotFoundError):
        project_service.get_evolution(pid, outsider)


def test_membership_grants_access_consistently_across_every_read(db, project_service):
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    admin = _make_user(db, Role.ADMINISTRATOR)
    member = _make_user(db, Role.ANALYST)  # global role irrelevant to the check below
    _add_member(db, pid, member, Role.VIEWER, added_by=admin)

    # Every one of these must now succeed for the same reason: a live
    # membership row exists - not just get_project.
    project_service.get_project(pid, member)
    project_service.get_kpis(pid, member)
    project_service.get_layers(pid, member)
    project_service.get_evolution(pid, member)


def test_administrator_bypasses_membership_with_zero_rows(db, project_service):
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    admin = _make_user(db, Role.ADMINISTRATOR)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM project_membership WHERE project_id = %s", (str(pid),))
        assert cur.fetchone()["n"] == 0

    # No row for this Administrator (or anyone) on this project at all, yet
    # every read succeeds.
    project_service.get_project(pid, admin)
    project_service.get_kpis(pid, admin)
    project_service.get_layers(pid, admin)
    project_service.get_evolution(pid, admin)


def test_list_projects_is_scoped_to_membership_for_non_admin(db, project_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    member = _make_user(db, Role.VIEWER)
    visible = _make_project(db, f"Visible-{uuid.uuid4()}")
    hidden = _make_project(db, f"Hidden-{uuid.uuid4()}")
    _add_member(db, visible, member, Role.VIEWER, added_by=admin)

    page = project_service.list_projects(member, limit=1000, offset=0)
    names = {p.project_id for p in page.items}
    assert visible in names
    assert hidden not in names

    admin_page = project_service.list_projects(admin, limit=1000, offset=0)
    admin_names = {p.project_id for p in admin_page.items}
    assert visible in admin_names
    assert hidden in admin_names


def test_list_projects_search_is_combined_with_membership_scope(db, project_service):
    """Name search must never bypass the membership filter - a non-admin
    searching for a project they can't see still gets nothing back, even
    though the name matches and an Administrator's search for the same term
    does find it."""
    admin = _make_user(db, Role.ADMINISTRATOR)
    member = _make_user(db, Role.VIEWER)
    token = uuid.uuid4().hex[:8]
    visible = _make_project(db, f"SearchVisible-{token}")
    hidden = _make_project(db, f"SearchHidden-{token}")
    _add_member(db, visible, member, Role.VIEWER, added_by=admin)

    # Case-insensitive partial match.
    member_page = project_service.list_projects(member, limit=1000, offset=0, search=token.upper())
    member_names = {p.project_id for p in member_page.items}
    assert visible in member_names
    assert hidden not in member_names  # membership scope still applies

    admin_page = project_service.list_projects(admin, limit=1000, offset=0, search=token)
    admin_names = {p.project_id for p in admin_page.items}
    assert visible in admin_names
    assert hidden in admin_names

    assert project_service.list_projects(member, limit=1000, offset=0, search="no-such-project").items == []


# --------------------------------------------------------------- portfolio summary


def _add_dataset_with_area(db: Database, project_id, total_area_ha: float) -> None:
    """Enough of a real dataset+kpi row for KpiRepository.portfolio_totals to
    have something real to sum - same shape as test_project_soft_delete.py's
    test_portfolio_totals_excludes_soft_deleted_project."""
    with db.transaction() as cur:
        did = DatasetRepository(cur).insert(
            project_id=project_id, dataset_type="LULC", source="S", accuracy_score=90.0,
            date_processed="2026-01-01", batch_id=uuid.uuid4(),
        )
        KpiRepository(cur).upsert(did, "total_area", total_area_ha, "ha")


def test_portfolio_summary_excludes_projects_the_caller_is_not_a_member_of(db, project_service):
    """The follow-up fix: GET /summary must not leak totals from a project
    the caller can't otherwise open (get_project/kpis/layers/evolution
    already 404 it) - `member_id=None` (Administrator) stays unfiltered."""
    admin = _make_user(db, Role.ADMINISTRATOR)
    member = _make_user(db, Role.VIEWER)
    visible = _make_project(db, f"SummaryVisible-{uuid.uuid4()}")
    hidden = _make_project(db, f"SummaryHidden-{uuid.uuid4()}")
    _add_dataset_with_area(db, visible, 100.0)
    _add_dataset_with_area(db, hidden, 999.0)
    _add_member(db, visible, member, Role.VIEWER, added_by=admin)

    member_summary = project_service.portfolio_summary(member)
    assert member_summary["portfolio"].get("total_area", 0.0) == pytest.approx(100.0)

    admin_summary = project_service.portfolio_summary(admin)
    assert admin_summary["portfolio"]["total_area"] >= 100.0 + 999.0


def test_portfolio_summary_updates_when_a_second_project_is_added(db, project_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    member = _make_user(db, Role.VIEWER)
    first = _make_project(db, f"SummaryFirst-{uuid.uuid4()}")
    second = _make_project(db, f"SummarySecond-{uuid.uuid4()}")
    _add_dataset_with_area(db, first, 50.0)
    _add_dataset_with_area(db, second, 75.0)
    _add_member(db, first, member, Role.VIEWER, added_by=admin)

    before = project_service.portfolio_summary(member)
    assert before["portfolio"].get("total_area", 0.0) == pytest.approx(50.0)
    assert before["project_count"] == 1

    _add_member(db, second, member, Role.VIEWER, added_by=admin)

    after = project_service.portfolio_summary(member)
    assert after["portfolio"]["total_area"] == pytest.approx(125.0)
    assert after["project_count"] == 2


# --------------------------------------------------------------- manage access


def test_project_level_gis_associate_can_add_a_member_to_that_project(db, membership_service):
    """Proves the check uses the PROJECT role, not the global one: this
    actor's global role is Analyst, but they hold GIS Associate on THIS
    specific project."""
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    actor = _make_user(db, Role.ANALYST)  # global role - deliberately NOT GIS Associate
    _add_member(db, pid, actor, Role.GIS_ASSOCIATE, added_by=admin)  # project-level role IS

    target = _make_user(db, Role.VIEWER)
    result = membership_service.add_member(pid, target.username, Role.VERIFIER, actor)

    assert result.user_id == target.user_id
    assert result.role == Role.VERIFIER


def test_project_level_gis_associate_cannot_add_to_a_different_project(db, membership_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    home_project = _make_project(db, f"Home-{uuid.uuid4()}")
    other_project = _make_project(db, f"Other-{uuid.uuid4()}")
    actor = _make_user(db, Role.ANALYST)
    _add_member(db, home_project, actor, Role.GIS_ASSOCIATE, added_by=admin)
    # deliberately NOT added to other_project at all

    target = _make_user(db, Role.VIEWER)
    with pytest.raises(NotFoundError):  # can't even see it - not a 403
        membership_service.add_member(other_project, target.username, Role.VIEWER, actor)


def test_member_with_non_managing_role_cannot_add_members(db, membership_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    actor = _make_user(db, Role.VIEWER)
    _add_member(db, pid, actor, Role.VIEWER, added_by=admin)  # can see, can't manage

    target = _make_user(db, Role.VIEWER)
    with pytest.raises(ForbiddenError):
        membership_service.add_member(pid, target.username, Role.VIEWER, actor)


def test_administrator_can_manage_membership_on_a_project_with_zero_rows(db, membership_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    target = _make_user(db, Role.VIEWER)

    result = membership_service.add_member(pid, target.username, Role.ANALYST, admin)

    assert result.role == Role.ANALYST


def test_administrator_role_is_rejected_as_a_project_level_role(db, membership_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    target = _make_user(db, Role.VIEWER)

    with pytest.raises(ValidationError):
        membership_service.add_member(pid, target.username, Role.ADMINISTRATOR, admin)


def test_add_member_prefills_role_from_target_global_role(db, membership_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    target = _make_user(db, Role.VERIFIER)  # global role, no role passed explicitly

    result = membership_service.add_member(pid, target.username, None, admin)

    assert result.role == Role.VERIFIER


def test_add_member_twice_is_a_conflict_not_a_silent_duplicate(db, membership_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    target = _make_user(db, Role.VIEWER)

    membership_service.add_member(pid, target.username, Role.VIEWER, admin)
    with pytest.raises(ConflictError):
        membership_service.add_member(pid, target.username, Role.ANALYST, admin)


def test_remove_then_readd_gets_a_fresh_row_not_a_collision(db, membership_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    target = _make_user(db, Role.VIEWER)

    membership_service.add_member(pid, target.username, Role.VIEWER, admin)
    membership_service.remove_member(pid, target.user_id, admin)
    # removed - no longer visible
    with pytest.raises(NotFoundError):
        ProjectService(db, get_settings(), object()).get_project(pid, target)
    # re-add succeeds (no unique-index collision with the removed row)
    result = membership_service.add_member(pid, target.username, Role.ANALYST, admin)
    assert result.role == Role.ANALYST


def test_update_role_changes_the_project_level_role_only(db, membership_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    target = _make_user(db, Role.VIEWER)
    membership_service.add_member(pid, target.username, Role.VIEWER, admin)

    updated = membership_service.update_role(pid, target.user_id, Role.VERIFIER, admin)

    assert updated.role == Role.VERIFIER
    with db.connection() as conn, conn.cursor() as cur:
        assert UserRepository(cur).get_by_id(target.user_id)["role"] == Role.VIEWER.value


# --------------------------------------------------------------- upload path


def test_upload_to_a_brand_new_project_auto_adds_creator_as_first_member(db, ingestion_service):
    actor = _make_user(db, Role.GIS_ASSOCIATE)
    meta = IngestMetadata(
        project_name=f"AutoMember-{uuid.uuid4()}", region="Karnataka", dataset_type="LULC",
        source="test", classification_method="", accuracy_score=None,
        date_processed="2026-01-01", pixel_size_m=10.0,
    )

    with db.transaction() as cur:
        project_id = ingestion_service._resolve_project(cur, meta, actor)  # noqa: SLF001

    with db.connection() as conn, conn.cursor() as cur:
        role = ProjectMembershipRepository(cur).get_role(project_id, actor.user_id)
    assert role == Role.GIS_ASSOCIATE


def test_upload_to_an_existing_project_without_membership_is_denied(db, ingestion_service):
    name = f"Existing-{uuid.uuid4()}"
    _make_project(db, name)  # exists, but this actor is never added to it
    actor = _make_user(db, Role.GIS_ASSOCIATE)
    meta = IngestMetadata(
        project_name=name, region="Karnataka", dataset_type="LULC", source="test",
        classification_method="", accuracy_score=None, date_processed="2026-01-01",
        pixel_size_m=10.0,
    )

    with pytest.raises(NotFoundError), db.transaction() as cur:
        ingestion_service._resolve_project(cur, meta, actor)  # noqa: SLF001


def test_upload_to_existing_project_with_non_gis_associate_role_is_forbidden(db, ingestion_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    name = f"Existing-{uuid.uuid4()}"
    pid = _make_project(db, name)
    actor = _make_user(db, Role.GIS_ASSOCIATE)  # global role passes UPLOAD_ROLES...
    _add_member(db, pid, actor, Role.VIEWER, added_by=admin)  # ...but project role doesn't
    meta = IngestMetadata(
        project_name=name, region="Karnataka", dataset_type="LULC", source="test",
        classification_method="", accuracy_score=None, date_processed="2026-01-01",
        pixel_size_m=10.0,
    )

    with pytest.raises(ForbiddenError), db.transaction() as cur:
        ingestion_service._resolve_project(cur, meta, actor)  # noqa: SLF001


def test_administrator_upload_never_creates_a_membership_row(db, ingestion_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    meta = IngestMetadata(
        project_name=f"AdminUpload-{uuid.uuid4()}", region="Karnataka", dataset_type="LULC",
        source="test", classification_method="", accuracy_score=None,
        date_processed="2026-01-01", pixel_size_m=10.0,
    )

    with db.transaction() as cur:
        project_id = ingestion_service._resolve_project(cur, meta, admin)  # noqa: SLF001

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM project_membership WHERE project_id = %s", (str(project_id),)
        )
        assert cur.fetchone()["n"] == 0
