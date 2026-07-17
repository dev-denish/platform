"""DB-backed tests for project soft-delete against a REAL PostGIS database - the
optimistic-lock race, the FK-preserving no-cascade behaviour, the audit write, and
the portfolio-totals exclusion all need real transaction/constraint semantics, so
this is not faked (see test_db_repositories.py for the same skip-guard convention
this file follows).

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
from app.core.errors import NotFoundError  # noqa: E402
from app.domain.dtos import CurrentUser  # noqa: E402
from app.domain.enums import Role  # noqa: E402
from app.repositories.datasets import DatasetRepository, KpiRepository  # noqa: E402
from app.repositories.projects import ProjectRepository  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402


@pytest.fixture(scope="module")
def db() -> Database:
    d = Database(get_settings())
    d.connect()
    yield d
    d.close()


@pytest.fixture
def project_service(db) -> ProjectService:
    # delete_project/list_projects/get_kpis never touch storage; a bare object()
    # stands in, same as app.state.storage in the API-contract test client.
    return ProjectService(db, get_settings(), object())


def _make_admin(db: Database) -> CurrentUser:
    """`project.deleted_by` FKs to app_user - a real row keeps this test honest
    about the actual schema instead of a bare random UUID that would only work
    by accident of there being no FK."""
    username = f"admintest-{uuid.uuid4()}"
    with db.transaction() as cur:
        row = UserRepository(cur).upsert(username, "x", Role.ADMINISTRATOR.value)
    return CurrentUser(user_id=row["user_id"], username=username, role=Role.ADMINISTRATOR)


def _make_project(db: Database, name: str) -> uuid.UUID:
    with db.transaction() as cur:
        return ProjectRepository(cur).find_or_create_by_name(name, "Karnataka")


def test_get_version_is_none_for_missing_project(db):
    with db.connection() as conn, conn.cursor() as cur:
        assert ProjectRepository(cur).get_version(uuid.uuid4()) is None


def test_delete_project_soft_deletes_and_records_who(db, project_service):
    actor = _make_admin(db)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")

    project_service.delete_project(pid, actor)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT deleted_at, deleted_by, version FROM project WHERE project_id = %s",
            (str(pid),),
        )
        row = cur.fetchone()

    assert row["deleted_at"] is not None
    assert str(row["deleted_by"]) == str(actor.user_id)
    assert row["version"] == 1  # bumped from the 0 default


def test_deleted_project_disappears_from_get_and_listing(db, project_service):
    actor = _make_admin(db)
    name = f"Proj-{uuid.uuid4()}"
    pid = _make_project(db, name)

    project_service.delete_project(pid, actor)

    with pytest.raises(NotFoundError):
        project_service.get_project(pid)

    with db.connection() as conn, conn.cursor() as cur:
        assert ProjectRepository(cur).get(pid) is None
        rows, _total = ProjectRepository(cur).list_paginated(1000, 0)
    assert name not in {r["name"] for r in rows}


def test_second_delete_of_same_project_is_a_no_op_not_found(db, project_service):
    actor = _make_admin(db)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")

    project_service.delete_project(pid, actor)

    with pytest.raises(NotFoundError):
        project_service.delete_project(pid, actor)


def test_delete_project_writes_an_audit_entry(db, project_service):
    actor = _make_admin(db)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")

    project_service.delete_project(pid, actor)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT actor_id, actor_name, action, target FROM audit_log "
            "WHERE action = 'delete_project' AND target = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (str(pid),),
        )
        row = cur.fetchone()

    assert row is not None
    assert str(row["actor_id"]) == str(actor.user_id)
    assert row["actor_name"] == actor.username


def test_delete_project_does_not_cascade_dataset_fk_stays_valid(db, project_service):
    actor = _make_admin(db)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    with db.transaction() as cur:
        did = DatasetRepository(cur).insert(
            project_id=pid, dataset_type="LULC", source="S", accuracy_score=90.0,
            date_processed="2026-01-01", batch_id=uuid.uuid4(),
        )

    project_service.delete_project(pid, actor)

    # The dataset row is untouched: still present, FK still points at the (now
    # soft-deleted, still physically existing) project row - nothing cascaded.
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT dataset_id, project_id FROM dataset WHERE dataset_id = %s", (str(did),)
        )
        row = cur.fetchone()
    assert row is not None
    assert str(row["project_id"]) == str(pid)


def test_portfolio_totals_excludes_soft_deleted_project(db, project_service):
    actor = _make_admin(db)
    # A class_area_* metric (sorts before "total_area" alphabetically) is what
    # exposed the pre-existing project_count bug this test also guards against -
    # see the comment in KpiRepository.portfolio_totals.
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    with db.transaction() as cur:
        did = DatasetRepository(cur).insert(
            project_id=pid, dataset_type="LULC", source="S", accuracy_score=90.0,
            date_processed="2026-01-01", batch_id=uuid.uuid4(),
        )
        KpiRepository(cur).upsert(did, "total_area", 123.0, "ha")
        KpiRepository(cur).upsert(did, "class_area_forest", 50.0, "ha")

    with db.connection() as conn, conn.cursor() as cur:
        totals_before, count_before = KpiRepository(cur).portfolio_totals()
    assert totals_before.get("total_area", 0.0) >= 123.0
    assert count_before >= 1

    project_service.delete_project(pid, actor)

    with db.connection() as conn, conn.cursor() as cur:
        totals_after, count_after = KpiRepository(cur).portfolio_totals()
    # This project's dataset/kpi rows are untouched (no cascade) but the project
    # itself is gone, so its contribution must no longer show up in the total,
    # and the project_count must actually reflect that (not just whichever
    # metric_name happens to sort first).
    assert totals_after.get("total_area", 0.0) == pytest.approx(
        totals_before.get("total_area", 0.0) - 123.0
    )
    assert count_after == count_before - 1
