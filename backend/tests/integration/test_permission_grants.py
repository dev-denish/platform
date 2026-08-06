"""DB-backed tests for per-user permission grants (Wave: permission grants)
against a REAL PostGIS database - the (user_id, permission_name) primary key
and its ON CONFLICT DO UPDATE re-grant semantics need real constraint
behavior, so this is not faked (see test_user_management.py for the same
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
from app.core.errors import NotFoundError, ValidationError  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.domain.dtos import CurrentUser  # noqa: E402
from app.domain.enums import Role  # noqa: E402
from app.domain.permissions import has_permission  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402
from app.services.permission_service import PermissionService  # noqa: E402

_PERMISSION = "edit_forest_definition"


@pytest.fixture(scope="module")
def db() -> Database:
    d = Database(get_settings())
    d.connect()
    yield d
    d.close()


@pytest.fixture
def permission_service(db) -> PermissionService:
    return PermissionService(db)


def _make_user(db: Database, role: Role) -> CurrentUser:
    username = f"permgrant-{role.name.lower()}-{uuid.uuid4()}"
    with db.transaction() as cur:
        row = UserRepository(cur).create(username, hash_password("irrelevant123"), role.value)
    return CurrentUser(user_id=row["user_id"], username=username, role=role)


def _has_permission(db: Database, user: CurrentUser) -> bool:
    with db.connection() as conn, conn.cursor() as cur:
        return has_permission(cur, user, _PERMISSION)


# --------------------------------------------------------------- has_permission


def test_administrator_has_every_permission_with_no_grant_row(db):
    admin = _make_user(db, Role.ADMINISTRATOR)
    assert _has_permission(db, admin) is True


def test_viewer_with_no_grant_lacks_the_permission(db):
    viewer = _make_user(db, Role.VIEWER)
    assert _has_permission(db, viewer) is False


def test_grant_then_has_permission_is_true(db, permission_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    viewer = _make_user(db, Role.VIEWER)

    permission_service.grant(viewer.user_id, _PERMISSION, admin)

    assert _has_permission(db, viewer) is True


def test_revoke_then_has_permission_is_false_again(db, permission_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    viewer = _make_user(db, Role.VIEWER)
    permission_service.grant(viewer.user_id, _PERMISSION, admin)
    assert _has_permission(db, viewer) is True

    permission_service.revoke(viewer.user_id, _PERMISSION, admin)

    assert _has_permission(db, viewer) is False


# --------------------------------------------------------------- validation


def test_grant_unknown_permission_name_is_rejected(db, permission_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    viewer = _make_user(db, Role.VIEWER)

    with pytest.raises(ValidationError):
        permission_service.grant(viewer.user_id, "not_a_real_permission", admin)


def test_grant_to_administrator_is_rejected_as_meaningless(db, permission_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    other_admin = _make_user(db, Role.ADMINISTRATOR)

    with pytest.raises(ValidationError):
        permission_service.grant(other_admin.user_id, _PERMISSION, admin)


def test_grant_to_nonexistent_user_404s(db, permission_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    with pytest.raises(NotFoundError):
        permission_service.grant(uuid.uuid4(), _PERMISSION, admin)


def test_revoke_never_granted_404s(db, permission_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    viewer = _make_user(db, Role.VIEWER)
    with pytest.raises(NotFoundError):
        permission_service.revoke(viewer.user_id, _PERMISSION, admin)


# --------------------------------------------------------------- listing


def test_list_grants_shows_who_granted_it(db, permission_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    viewer = _make_user(db, Role.VIEWER)
    permission_service.grant(viewer.user_id, _PERMISSION, admin)

    result = permission_service.list_grants(viewer.user_id, admin)

    assert len(result.grants) == 1
    assert result.grants[0].permission_name == _PERMISSION
    assert result.grants[0].granted_by == admin.user_id
    assert result.grants[0].granted_by_username == admin.username


def test_regrant_refreshes_granted_by_without_erroring(db, permission_service):
    first_admin = _make_user(db, Role.ADMINISTRATOR)
    second_admin = _make_user(db, Role.ADMINISTRATOR)
    viewer = _make_user(db, Role.VIEWER)
    permission_service.grant(viewer.user_id, _PERMISSION, first_admin)

    permission_service.grant(viewer.user_id, _PERMISSION, second_admin)

    result = permission_service.list_grants(viewer.user_id, first_admin)
    assert len(result.grants) == 1
    assert result.grants[0].granted_by == second_admin.user_id


# --------------------------------------------------------------- audit trail


def test_grant_is_audit_logged(db, permission_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    viewer = _make_user(db, Role.VIEWER)

    permission_service.grant(viewer.user_id, _PERMISSION, admin)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT actor_id, actor_name, detail FROM audit_log "
            "WHERE action = 'grant_permission' AND target = %s ORDER BY created_at DESC LIMIT 1",
            (f"{viewer.user_id}:{_PERMISSION}",),
        )
        row = cur.fetchone()
    assert row is not None
    assert row["actor_id"] == admin.user_id
    assert row["actor_name"] == admin.username
    assert viewer.username in row["detail"]
    assert _PERMISSION in row["detail"]


def test_revoke_is_audit_logged(db, permission_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    viewer = _make_user(db, Role.VIEWER)
    permission_service.grant(viewer.user_id, _PERMISSION, admin)

    permission_service.revoke(viewer.user_id, _PERMISSION, admin)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT actor_id, actor_name, detail FROM audit_log "
            "WHERE action = 'revoke_permission' AND target = %s ORDER BY created_at DESC LIMIT 1",
            (f"{viewer.user_id}:{_PERMISSION}",),
        )
        row = cur.fetchone()
    assert row is not None
    assert row["actor_id"] == admin.user_id
    assert viewer.username in row["detail"]
