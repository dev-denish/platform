"""DB-backed tests for the forest-definition threshold (Wave: permission
grants, Part 2) against a REAL PostGIS database - the seeded singleton row
(migration 0016) and the has_permission()-gated update both need real
schema/transaction semantics, so this is not faked.

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
from app.core.errors import ForbiddenError  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.domain.dtos import CurrentUser, UpdateForestDefinitionRequest  # noqa: E402
from app.domain.enums import Role  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402
from app.services.forest_definition_service import ForestDefinitionService  # noqa: E402
from app.services.permission_service import PermissionService  # noqa: E402

_PERMISSION = "edit_forest_definition"
# The seeded India DNA defaults (migration 0016) - restored after every test
# that mutates the singleton row, since it's shared, real, global state.
_DEFAULTS = {"canopy_cover_pct": 15, "min_height_m": 2, "min_area_ha": 0.05}


@pytest.fixture(scope="module")
def db() -> Database:
    d = Database(get_settings())
    d.connect()
    yield d
    d.close()


@pytest.fixture
def forest_service(db) -> ForestDefinitionService:
    return ForestDefinitionService(db)


@pytest.fixture
def permission_service(db) -> PermissionService:
    return PermissionService(db)


@pytest.fixture(autouse=True)
def _restore_defaults(db):
    """The forest_definition_setting row is a real singleton, not per-test
    data - every test that writes to it must leave it exactly as it found it
    (the seeded defaults), or a later test (or a human looking at the
    ephemeral stack) sees whatever the last test wrote."""
    yield
    with db.transaction() as cur:
        cur.execute(
            "UPDATE forest_definition_setting SET canopy_cover_pct = %s, "
            "min_height_m = %s, min_area_ha = %s, updated_by = NULL",
            (_DEFAULTS["canopy_cover_pct"], _DEFAULTS["min_height_m"], _DEFAULTS["min_area_ha"]),
        )


def _make_user(db: Database, role: Role) -> CurrentUser:
    username = f"forestdef-{role.name.lower()}-{uuid.uuid4()}"
    with db.transaction() as cur:
        row = UserRepository(cur).create(username, hash_password("irrelevant123"), role.value)
    return CurrentUser(user_id=row["user_id"], username=username, role=role)


# --------------------------------------------------------------- read


def test_any_authenticated_user_can_read_the_seeded_defaults(db, forest_service):
    viewer = _make_user(db, Role.VIEWER)

    result = forest_service.get(viewer)

    assert result.canopy_cover_pct == _DEFAULTS["canopy_cover_pct"]
    assert result.min_height_m == _DEFAULTS["min_height_m"]
    assert result.min_area_ha == _DEFAULTS["min_area_ha"]


def test_viewer_without_grant_cannot_edit(db, forest_service):
    viewer = _make_user(db, Role.VIEWER)
    assert forest_service.get(viewer).can_edit is False


def test_administrator_can_edit(db, forest_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    assert forest_service.get(admin).can_edit is True


# --------------------------------------------------------------- write gating


def test_update_without_permission_is_forbidden(db, forest_service):
    viewer = _make_user(db, Role.VIEWER)
    body = UpdateForestDefinitionRequest(canopy_cover_pct=20, min_height_m=3, min_area_ha=0.1)

    with pytest.raises(ForbiddenError):
        forest_service.update(body, viewer)


def test_update_as_administrator_succeeds(db, forest_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    body = UpdateForestDefinitionRequest(canopy_cover_pct=20, min_height_m=3, min_area_ha=0.1)

    result = forest_service.update(body, admin)

    assert result.canopy_cover_pct == 20
    assert result.min_height_m == 3
    assert result.min_area_ha == 0.1
    assert result.updated_by_username == admin.username


def test_grantee_without_administrator_role_can_edit(db, forest_service, permission_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    analyst = _make_user(db, Role.ANALYST)
    permission_service.grant(analyst.user_id, _PERMISSION, admin)
    body = UpdateForestDefinitionRequest(canopy_cover_pct=18, min_height_m=2.5, min_area_ha=0.08)

    result = forest_service.update(body, analyst)

    assert result.canopy_cover_pct == 18
    # Read back through GET too, proving the write actually persisted, not
    # just echoed back from the UPDATE ... RETURNING.
    reread = forest_service.get(admin)
    assert reread.min_height_m == 2.5


def test_revoking_the_grant_removes_edit_access_again(db, forest_service, permission_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    analyst = _make_user(db, Role.ANALYST)
    permission_service.grant(analyst.user_id, _PERMISSION, admin)
    permission_service.revoke(analyst.user_id, _PERMISSION, admin)
    body = UpdateForestDefinitionRequest(canopy_cover_pct=18, min_height_m=2.5, min_area_ha=0.08)

    with pytest.raises(ForbiddenError):
        forest_service.update(body, analyst)


# --------------------------------------------------------------- persistence + audit


def test_update_persists_and_is_audit_logged_old_to_new(db, forest_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    body = UpdateForestDefinitionRequest(canopy_cover_pct=25, min_height_m=4, min_area_ha=0.2)

    forest_service.update(body, admin)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT canopy_cover_pct, min_height_m, min_area_ha, updated_by "
            "FROM forest_definition_setting"
        )
        row = cur.fetchone()
        cur.execute(
            "SELECT actor_id, actor_name, detail FROM audit_log "
            "WHERE action = 'update_forest_definition' ORDER BY created_at DESC LIMIT 1"
        )
        audit_row = cur.fetchone()

    assert float(row["canopy_cover_pct"]) == 25
    assert row["updated_by"] == admin.user_id
    assert audit_row is not None
    assert audit_row["actor_id"] == admin.user_id
    assert str(_DEFAULTS["canopy_cover_pct"]) in audit_row["detail"]
    assert "25" in audit_row["detail"]
