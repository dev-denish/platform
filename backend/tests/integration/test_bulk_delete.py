"""DB-backed tests for bulk select+delete (Projects, Users) against a REAL
PostGIS database - the same reason test_project_soft_delete.py/
test_user_management.py aren't faked: the optimistic-lock race, the real FK
schema introspection, and the audit write all need real transaction/
constraint semantics.

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
from app.core.security import hash_password  # noqa: E402
from app.domain.dtos import CurrentUser  # noqa: E402
from app.domain.enums import Role  # noqa: E402
from app.repositories.projects import ProjectRepository  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402
from app.services.user_service import UserService  # noqa: E402


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
def user_service(db) -> UserService:
    return UserService(db)


def _make_admin(db: Database) -> CurrentUser:
    username = f"bulkdel-admin-{uuid.uuid4()}"
    with db.transaction() as cur:
        row = UserRepository(cur).upsert(username, hash_password("irrelevant123"), Role.ADMINISTRATOR.value)
    return CurrentUser(user_id=row["user_id"], username=username, role=Role.ADMINISTRATOR)


def _make_project(db: Database, name: str) -> uuid.UUID:
    with db.transaction() as cur:
        project_id, _created = ProjectRepository(cur).find_or_create_by_name(name, "Karnataka")
    return project_id


def _make_user(db: Database, role: Role = Role.VIEWER) -> CurrentUser:
    username = f"bulkdel-user-{uuid.uuid4()}"
    with db.transaction() as cur:
        row = UserRepository(cur).create(username, hash_password("irrelevant123"), role.value)
    return CurrentUser(user_id=row["user_id"], username=username, role=role)


# --------------------------------------------------------------- projects


def test_bulk_delete_projects_partial_success(db, project_service):
    actor = _make_admin(db)
    p1 = _make_project(db, f"Bulk-{uuid.uuid4()}")
    p2 = _make_project(db, f"Bulk-{uuid.uuid4()}")
    missing_id = uuid.uuid4()  # never existed

    results = project_service.bulk_delete_projects([p1, p2, missing_id], actor)

    by_id = {r.id: r for r in results}
    assert by_id[p1].success is True
    assert by_id[p2].success is True
    assert by_id[missing_id].success is False
    assert by_id[missing_id].error == "Project not found."

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT deleted_at, deleted_by FROM project WHERE project_id IN (%s, %s)",
            (str(p1), str(p2)),
        )
        rows = cur.fetchall()
    assert len(rows) == 2
    for row in rows:
        assert row["deleted_at"] is not None
        assert str(row["deleted_by"]) == str(actor.user_id)


def test_bulk_delete_projects_writes_one_audit_entry_per_success(db, project_service):
    actor = _make_admin(db)
    p1 = _make_project(db, f"Bulk-{uuid.uuid4()}")
    p2 = _make_project(db, f"Bulk-{uuid.uuid4()}")

    project_service.bulk_delete_projects([p1, p2], actor)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT target, actor_id, actor_name FROM audit_log "
            "WHERE action = 'delete_project' AND target IN (%s, %s)",
            (str(p1), str(p2)),
        )
        rows = cur.fetchall()
    assert {r["target"] for r in rows} == {str(p1), str(p2)}
    for row in rows:
        assert str(row["actor_id"]) == str(actor.user_id)
        assert row["actor_name"] == actor.username


def test_bulk_delete_projects_does_not_abort_batch_on_a_bad_id(db, project_service):
    """A failing id in the MIDDLE of the batch must not stop later ids in the
    same batch from being processed - proves this is one transaction with
    per-id outcomes, not one that rolls back or short-circuits on the first
    failure."""
    actor = _make_admin(db)
    p1 = _make_project(db, f"Bulk-{uuid.uuid4()}")
    already_deleted = _make_project(db, f"Bulk-{uuid.uuid4()}")
    project_service.delete_project(already_deleted, actor)
    p3 = _make_project(db, f"Bulk-{uuid.uuid4()}")

    results = project_service.bulk_delete_projects([p1, already_deleted, p3], actor)

    by_id = {r.id: r for r in results}
    assert by_id[p1].success is True
    assert by_id[already_deleted].success is False
    assert by_id[p3].success is True  # came AFTER the failing id in the list


# --------------------------------------------------------------- users


def test_bulk_permanent_delete_partial_success(db, user_service):
    actor = _make_admin(db)
    u1 = _make_user(db)
    u2 = _make_user(db)
    missing_id = uuid.uuid4()

    results = user_service.bulk_permanent_delete_users([u1.user_id, u2.user_id, missing_id], actor)

    by_id = {r.id: r for r in results}
    assert by_id[u1.user_id].success is True
    assert by_id[u2.user_id].success is True
    assert by_id[missing_id].success is False
    assert by_id[missing_id].error == "User not found."

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT user_id FROM app_user WHERE user_id IN (%s, %s)",
            (str(u1.user_id), str(u2.user_id)),
        )
        assert cur.fetchall() == []


def test_bulk_permanent_delete_blocks_self_delete_without_blocking_others(db, user_service):
    """The realistic per-item conflict a real admin can hit in this schema:
    every OTHER FK to app_user is SET NULL or a reviewed CASCADE (see
    UserRepository.referencing_foreign_keys/_REVIEWED_CASCADE_FKS), so
    self-delete-block is the one safety check that genuinely varies id-by-id
    with real data - it's what a bulk-select-all-then-delete would trigger if
    the acting admin's own row is in the selection."""
    actor = _make_admin(db)
    u1 = _make_user(db)
    u2 = _make_user(db)

    results = user_service.bulk_permanent_delete_users([u1.user_id, actor.user_id, u2.user_id], actor)

    by_id = {r.id: r for r in results}
    assert by_id[u1.user_id].success is True
    assert by_id[u2.user_id].success is True
    assert by_id[actor.user_id].success is False
    assert by_id[actor.user_id].error == "You cannot permanently delete your own account."

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT user_id FROM app_user WHERE user_id = %s", (str(actor.user_id),))
        assert cur.fetchone() is not None  # the admin's own row survives


def test_bulk_permanent_delete_writes_one_audit_entry_per_success(db, user_service):
    actor = _make_admin(db)
    u1 = _make_user(db)
    u2 = _make_user(db)

    user_service.bulk_permanent_delete_users([u1.user_id, u2.user_id], actor)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT target, actor_id FROM audit_log "
            "WHERE action = 'permanently_delete_user' AND target IN (%s, %s)",
            (str(u1.user_id), str(u2.user_id)),
        )
        rows = cur.fetchall()
    assert {r["target"] for r in rows} == {str(u1.user_id), str(u2.user_id)}
    for row in rows:
        assert str(row["actor_id"]) == str(actor.user_id)


def test_bulk_permanent_delete_blocks_every_id_when_schema_has_an_unreviewed_fk(db, user_service):
    """The FK-safety check is schema-wide (see UserRepository.
    referencing_foreign_keys's docstring), not a per-row existence check -
    every real FK to app_user today is SET NULL or a reviewed CASCADE, so
    this guard never actually fires against real production data. This test
    proves it still WOULD fire, and would block every id in the batch
    uniformly (not just the "conflicting" one), if the schema ever grew an
    unreviewed FK - by adding one as a throwaway table for the duration of
    this test only."""
    actor = _make_admin(db)
    u1 = _make_user(db)
    u2 = _make_user(db)

    with db.transaction() as cur:
        cur.execute(
            "CREATE TABLE bulkdel_test_unreviewed_fk ("
            "  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),"
            "  user_id UUID NOT NULL REFERENCES app_user(user_id)"
            ")"
        )
    try:
        results = user_service.bulk_permanent_delete_users([u1.user_id, u2.user_id], actor)
        by_id = {r.id: r for r in results}
        assert by_id[u1.user_id].success is False
        assert by_id[u2.user_id].success is False
        assert "bulkdel_test_unreviewed_fk" in by_id[u1.user_id].error
        assert "bulkdel_test_unreviewed_fk" in by_id[u2.user_id].error

        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM app_user WHERE user_id IN (%s, %s)",
                (str(u1.user_id), str(u2.user_id)),
            )
            assert len(cur.fetchall()) == 2  # neither was actually deleted
    finally:
        with db.transaction() as cur:
            cur.execute("DROP TABLE bulkdel_test_unreviewed_fk")
