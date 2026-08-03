"""DB-backed tests for user-account management (Wave: User Management) against
a REAL PostGIS database - the partial-unique-index fix on app_user.username,
the upsert()-vs-create() distinction, and login-after-deactivate all need real
constraint/transaction semantics, so this is not faked (see
test_db_repositories.py for the same skip-guard convention this file follows).

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
from app.core.errors import AuthError, ConflictError, NotFoundError, ValidationError  # noqa: E402
from app.core.security import hash_password, verify_password  # noqa: E402
from app.domain.dtos import CurrentUser  # noqa: E402
from app.domain.enums import Role  # noqa: E402
from app.repositories.memberships import ProjectMembershipRepository  # noqa: E402
from app.repositories.projects import ProjectRepository  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402
from app.services.user_service import UserService  # noqa: E402


@pytest.fixture(scope="module")
def db() -> Database:
    d = Database(get_settings())
    d.connect()
    yield d
    d.close()


@pytest.fixture
def user_service(db) -> UserService:
    return UserService(db)


@pytest.fixture
def auth_service(db) -> AuthService:
    return AuthService(db, get_settings())


def _make_admin(db: Database) -> CurrentUser:
    username = f"usermgmt-admin-{uuid.uuid4()}"
    with db.transaction() as cur:
        row = UserRepository(cur).upsert(username, hash_password("irrelevant123"), Role.ADMINISTRATOR.value)
    return CurrentUser(user_id=row["user_id"], username=username, role=Role.ADMINISTRATOR)


# --------------------------------------------------------------- create + login end-to-end


def test_create_user_then_actually_log_in_with_the_shown_password(db, user_service, auth_service):
    admin = _make_admin(db)
    username = f"newhire-{uuid.uuid4()}"
    password = "correct-horse-battery"  # the exact password the Administrator would see/copy

    created = user_service.create_user(username, password, Role.ANALYST, admin)

    assert created.username == username
    assert created.role == Role.ANALYST
    assert created.deleted_at is None

    # Real end-to-end login, not just "the row exists" - the same AuthService
    # the actual /auth/login endpoint uses.
    pair = auth_service.login(username, password)
    assert pair.access_token

    me = auth_service.current_user_from_access(pair.access_token)
    assert me.username == username
    assert me.role == Role.ANALYST


# --------------------------------------------------------------- duplicate handling


def test_create_user_with_existing_live_username_is_a_clean_conflict(db, user_service):
    admin = _make_admin(db)
    username = f"dup-{uuid.uuid4()}"
    user_service.create_user(username, "original-password-1", Role.VIEWER, admin)

    with pytest.raises(ConflictError):
        user_service.create_user(username, "attacker-password-2", Role.ADMINISTRATOR, admin)


def test_duplicate_create_attempt_never_touches_the_original_password(db, user_service, auth_service):
    admin = _make_admin(db)
    username = f"dup-{uuid.uuid4()}"
    user_service.create_user(username, "original-password-1", Role.VIEWER, admin)

    with pytest.raises(ConflictError):
        user_service.create_user(username, "attacker-password-2", Role.ADMINISTRATOR, admin)

    # The ORIGINAL account's password still works; the role is still Viewer,
    # not silently promoted to Administrator by the rejected attempt.
    pair = auth_service.login(username, "original-password-1")
    me = auth_service.current_user_from_access(pair.access_token)
    assert me.role == Role.VIEWER
    with pytest.raises(Exception):
        auth_service.login(username, "attacker-password-2")


# --------------------------------------------------------------- deactivate


def test_deactivate_then_login_is_rejected(db, user_service, auth_service):
    admin = _make_admin(db)
    username = f"toboot-{uuid.uuid4()}"
    password = "soon-to-be-deactivated-1"
    created = user_service.create_user(username, password, Role.VIEWER, admin)

    user_service.deactivate_user(created.user_id, admin)

    with pytest.raises(Exception):
        auth_service.login(username, password)


def test_deactivate_records_deleted_by_and_leaves_deleted_at_set(db, user_service):
    admin = _make_admin(db)
    username = f"toboot-{uuid.uuid4()}"
    created = user_service.create_user(username, "whatever-password-1", Role.VIEWER, admin)

    user_service.deactivate_user(created.user_id, admin)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT deleted_at, deleted_by FROM app_user WHERE user_id = %s", (str(created.user_id),)
        )
        row = cur.fetchone()
    assert row["deleted_at"] is not None
    assert str(row["deleted_by"]) == str(admin.user_id)


def test_deactivate_twice_is_a_no_op_not_found(db, user_service):
    admin = _make_admin(db)
    username = f"toboot-{uuid.uuid4()}"
    created = user_service.create_user(username, "whatever-password-1", Role.VIEWER, admin)

    user_service.deactivate_user(created.user_id, admin)
    with pytest.raises(NotFoundError):
        user_service.deactivate_user(created.user_id, admin)


def test_deactivate_missing_user_is_not_found(db, user_service):
    admin = _make_admin(db)
    with pytest.raises(NotFoundError):
        user_service.deactivate_user(uuid.uuid4(), admin)


def test_username_freed_after_deactivation_can_be_reused_for_a_new_account(
    db, user_service, auth_service
):
    """The actual point of the migration 0008 fix: a deactivated username is
    NOT permanently squatted - a brand-new, distinct account can take it."""
    admin = _make_admin(db)
    username = f"reuse-{uuid.uuid4()}"
    first = user_service.create_user(username, "first-password-123", Role.VIEWER, admin)
    user_service.deactivate_user(first.user_id, admin)

    second = user_service.create_user(username, "second-password-456", Role.ANALYST, admin)

    assert second.user_id != first.user_id
    assert second.role == Role.ANALYST
    # The new account's password works; the old one no longer does (it's
    # deactivated) - the two rows are independent, not the same row revived.
    pair = auth_service.login(username, "second-password-456")
    me = auth_service.current_user_from_access(pair.access_token)
    assert me.role == Role.ANALYST
    with pytest.raises(Exception):
        auth_service.login(username, "first-password-123")


# --------------------------------------------------------------- activate (reverses deactivate)


def test_activate_then_login_succeeds_with_existing_password(db, user_service, auth_service):
    admin = _make_admin(db)
    username = f"reactivate-{uuid.uuid4()}"
    password = "same-password-throughout-1"
    created = user_service.create_user(username, password, Role.VIEWER, admin)
    user_service.deactivate_user(created.user_id, admin)
    with pytest.raises(Exception):
        auth_service.login(username, password)

    user_service.activate_user(created.user_id, admin)

    # Nothing about the password itself changed - the SAME password works again.
    pair = auth_service.login(username, password)
    me = auth_service.current_user_from_access(pair.access_token)
    assert me.username == username


def test_activate_clears_deleted_at_and_deleted_by(db, user_service):
    admin = _make_admin(db)
    created = user_service.create_user(
        f"reactivateclear-{uuid.uuid4()}", "whatever-password-1", Role.VIEWER, admin
    )
    user_service.deactivate_user(created.user_id, admin)

    user_service.activate_user(created.user_id, admin)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT deleted_at, deleted_by FROM app_user WHERE user_id = %s", (str(created.user_id),)
        )
        row = cur.fetchone()
    assert row["deleted_at"] is None
    assert row["deleted_by"] is None


def test_activate_missing_user_is_not_found(db, user_service):
    admin = _make_admin(db)
    with pytest.raises(NotFoundError):
        user_service.activate_user(uuid.uuid4(), admin)


def test_activate_a_never_deactivated_user_is_a_no_op_not_found(db, user_service):
    admin = _make_admin(db)
    created = user_service.create_user(
        f"neverdeactivated-{uuid.uuid4()}", "whatever-password-1", Role.VIEWER, admin
    )
    with pytest.raises(NotFoundError):
        user_service.activate_user(created.user_id, admin)


def test_activate_and_hide_are_independent(db, user_service, auth_service):
    """Explicit decision this fix asked for: activating a deactivated-AND-
    hidden account must only clear deleted_at/deleted_by. If they were also
    hidden, they stay hidden - still absent from the default list until
    separately unhidden - proven directly, not just claimed."""
    admin = _make_admin(db)
    username = f"activatehidden-{uuid.uuid4()}"
    password = "still-works-after-activate-1"
    created = user_service.create_user(username, password, Role.VIEWER, admin)
    user_service.deactivate_user(created.user_id, admin)
    user_service.hide_user(created.user_id, admin)

    user_service.activate_user(created.user_id, admin)

    pair = auth_service.login(username, password)  # must NOT raise
    me = auth_service.current_user_from_access(pair.access_token)
    assert me.username == username

    default_page = user_service.list_users(limit=1000, offset=0)
    assert not any(u.user_id == created.user_id for u in default_page.items)

    with_hidden_page = user_service.list_users(limit=1000, offset=0, include_hidden=True)
    match = next(u for u in with_hidden_page.items if u.user_id == created.user_id)
    assert match.deleted_at is None  # activated
    assert match.hidden_at is not None  # still hidden, untouched


def test_activate_is_audit_logged(db, user_service):
    admin = _make_admin(db)
    created = user_service.create_user(
        f"activatelogged-{uuid.uuid4()}", "whatever-password-1", Role.VIEWER, admin
    )
    user_service.deactivate_user(created.user_id, admin)

    user_service.activate_user(created.user_id, admin)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT actor_id, actor_name, detail FROM audit_log "
            "WHERE action = 'activate_user' AND target = %s ORDER BY created_at DESC LIMIT 1",
            (str(created.user_id),),
        )
        row = cur.fetchone()
    assert row is not None
    assert row["actor_id"] == admin.user_id
    assert row["actor_name"] == admin.username
    assert created.username in row["detail"]


# --------------------------------------------------------------- password length


def test_create_user_rejects_short_password(db, user_service):
    admin = _make_admin(db)
    with pytest.raises(ValidationError):
        user_service.create_user(f"short-{uuid.uuid4()}", "short1", Role.VIEWER, admin)


# --------------------------------------------------------------- list


def test_list_users_includes_both_active_and_deactivated(db, user_service):
    admin = _make_admin(db)
    username = f"listme-{uuid.uuid4()}"
    created = user_service.create_user(username, "whatever-password-1", Role.VIEWER, admin)
    user_service.deactivate_user(created.user_id, admin)

    page = user_service.list_users(limit=1000, offset=0)
    match = next(u for u in page.items if u.user_id == created.user_id)
    assert match.deleted_at is not None


# --------------------------------------------------------------- the CLI script's own upsert() path


def test_upsert_still_resets_password_for_a_live_account_like_the_cli_script_relies_on(db, auth_service):
    """The exact case the wave calls out: `create_admin.py` re-run against an
    existing LIVE username must keep working unchanged - e.g. resetting your
    own account's password."""
    username = f"cliuser-{uuid.uuid4()}"
    with db.transaction() as cur:
        UserRepository(cur).upsert(username, hash_password("first-password-1"), Role.GIS_ASSOCIATE.value)

    with db.transaction() as cur:
        UserRepository(cur).upsert(username, hash_password("second-password-2"), Role.ANALYST.value)

    pair = auth_service.login(username, "second-password-2")
    me = auth_service.current_user_from_access(pair.access_token)
    assert me.role == Role.ANALYST
    with pytest.raises(Exception):
        auth_service.login(username, "first-password-1")


def test_upsert_does_not_revive_a_deactivated_account(db):
    """The bug this wave fixes, proven directly against UserRepository:
    upsert() against a DEACTIVATED username must create a fresh, live row -
    never silently touch the dead one's password/role while leaving
    deleted_at untouched."""
    username = f"revive-{uuid.uuid4()}"
    with db.transaction() as cur:
        original = UserRepository(cur).upsert(
            username, hash_password("original-1"), Role.VIEWER.value
        )
        UserRepository(cur).deactivate(original["user_id"], deleted_by=original["user_id"])

    with db.transaction() as cur:
        revived_attempt = UserRepository(cur).upsert(
            username, hash_password("new-2"), Role.ADMINISTRATOR.value
        )

    assert revived_attempt["user_id"] != original["user_id"]  # a NEW row, not the old one revived
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT deleted_at FROM app_user WHERE user_id = %s", (str(original["user_id"]),))
        assert cur.fetchone()["deleted_at"] is not None  # the dead row is still dead, untouched
        cur.execute(
            "SELECT deleted_at, password_hash FROM app_user WHERE user_id = %s",
            (str(revived_attempt["user_id"]),),
        )
        row = cur.fetchone()
        assert row["deleted_at"] is None
        assert verify_password("new-2", row["password_hash"])


# =========================================================== Wave: three-tier removal


# --------------------------------------------------------------- hide / unhide (tier 2)


def test_hide_excludes_from_default_list_but_show_hidden_includes(db, user_service):
    admin = _make_admin(db)
    created = user_service.create_user(f"tohide-{uuid.uuid4()}", "whatever-password-1", Role.VIEWER, admin)

    user_service.hide_user(created.user_id, admin)

    default_page = user_service.list_users(limit=1000, offset=0)
    assert not any(u.user_id == created.user_id for u in default_page.items)

    with_hidden_page = user_service.list_users(limit=1000, offset=0, include_hidden=True)
    match = next(u for u in with_hidden_page.items if u.user_id == created.user_id)
    assert match.hidden_at is not None
    assert match.hidden_by == admin.user_id


def test_unhide_restores_to_default_list(db, user_service):
    admin = _make_admin(db)
    created = user_service.create_user(f"tounhide-{uuid.uuid4()}", "whatever-password-1", Role.VIEWER, admin)
    user_service.hide_user(created.user_id, admin)

    user_service.unhide_user(created.user_id, admin)

    default_page = user_service.list_users(limit=1000, offset=0)
    match = next(u for u in default_page.items if u.user_id == created.user_id)
    assert match.hidden_at is None
    assert match.hidden_by is None


def test_hiding_a_user_does_not_block_login(db, user_service, auth_service):
    """Explicit decision this wave asked for: hide and deactivate are fully
    independent - hiding must never touch deleted_at or otherwise prevent
    login."""
    admin = _make_admin(db)
    username = f"hiddenlogin-{uuid.uuid4()}"
    password = "still-can-login-1"
    created = user_service.create_user(username, password, Role.VIEWER, admin)

    user_service.hide_user(created.user_id, admin)

    pair = auth_service.login(username, password)  # must NOT raise
    me = auth_service.current_user_from_access(pair.access_token)
    assert me.username == username


def test_hide_and_deactivate_are_independent_states(db, user_service):
    """A user can be deactivated-and-hidden, hidden-without-deactivated, or
    deactivated-without-hidden - proven directly against the two separate
    column pairs, not collapsed into one flag."""
    admin = _make_admin(db)
    created = user_service.create_user(
        f"independent-{uuid.uuid4()}", "whatever-password-1", Role.VIEWER, admin
    )

    user_service.hide_user(created.user_id, admin)  # hidden, NOT deactivated
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT deleted_at, hidden_at FROM app_user WHERE user_id = %s", (str(created.user_id),)
        )
        row = cur.fetchone()
    assert row["deleted_at"] is None
    assert row["hidden_at"] is not None

    user_service.deactivate_user(created.user_id, admin)  # NOW also deactivated
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT deleted_at, hidden_at FROM app_user WHERE user_id = %s", (str(created.user_id),)
        )
        row = cur.fetchone()
    assert row["deleted_at"] is not None  # both set now
    assert row["hidden_at"] is not None


def test_hide_missing_user_is_not_found(db, user_service):
    admin = _make_admin(db)
    with pytest.raises(NotFoundError):
        user_service.hide_user(uuid.uuid4(), admin)


def test_hide_twice_is_a_no_op_not_found(db, user_service):
    admin = _make_admin(db)
    created = user_service.create_user(f"hidetwice-{uuid.uuid4()}", "whatever-password-1", Role.VIEWER, admin)
    user_service.hide_user(created.user_id, admin)
    with pytest.raises(NotFoundError):
        user_service.hide_user(created.user_id, admin)


# --------------------------------------------------------------- FK safety introspection


def test_referencing_foreign_keys_matches_the_reviewed_schema(db):
    """Executable spec for the wave's own 'read first' requirement: confirm
    every FK referencing app_user is SET NULL, except the two explicitly
    reviewed CASCADE columns. If a future migration changes any of this,
    THIS test fails here - not silently in permanent_delete_user at runtime
    with unreviewed data loss."""
    with db.connection() as conn, conn.cursor() as cur:
        fks = UserRepository(cur).referencing_foreign_keys()

    by_column = {(fk["table_name"], fk["column_name"]): fk["delete_rule"] for fk in fks}

    expected_set_null = {
        ("app_user", "deleted_by"),
        ("app_user", "hidden_by"),
        ("audit_log", "actor_id"),
        ("jobs", "user_id"),
        ("project", "deleted_by"),
        # Wave: Reference Layer Library (migration 0011) - mirrors
        # project.deleted_by above; added to the schema without this
        # whitelist being updated to match, caught by this test's own
        # exhaustiveness check below.
        ("dataset", "deleted_by"),
        ("project_membership", "added_by"),
        ("project_membership", "removed_by"),
        # Wave: multi-format layers (migration 0010) - same "who approved
        # this" audit-trail convention as project_membership.added_by above;
        # deleting the approving Administrator must not delete the domain
        # itself from the allow-list.
        ("allowed_wms_domain", "added_by"),
    }
    expected_cascade = {
        ("project_membership", "user_id"),
        ("revoked_token", "user_id"),
    }

    assert expected_set_null <= by_column.keys()
    for key in expected_set_null:
        assert by_column[key] == "SET NULL", key
    for key in expected_cascade:
        assert by_column[key] == "CASCADE", key

    # Nothing else in the live schema is unaccounted for.
    assert set(by_column.keys()) == expected_set_null | expected_cascade


# --------------------------------------------------------------- permanent delete (tier 3)


def test_permanent_delete_removes_the_row_entirely(db, user_service):
    admin = _make_admin(db)
    created = user_service.create_user(
        f"gonefully-{uuid.uuid4()}", "whatever-password-1", Role.VIEWER, admin
    )

    user_service.permanent_delete_user(created.user_id, admin)

    # A REAL, fresh SELECT - not relying on any cached belief.
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM app_user WHERE user_id = %s", (str(created.user_id),))
        assert cur.fetchone()["n"] == 0


def test_permanent_delete_removes_from_every_list_including_show_hidden(db, user_service):
    admin = _make_admin(db)
    created = user_service.create_user(
        f"gonefromlists-{uuid.uuid4()}", "whatever-password-1", Role.VIEWER, admin
    )

    user_service.permanent_delete_user(created.user_id, admin)

    default_page = user_service.list_users(limit=1000, offset=0)
    hidden_page = user_service.list_users(limit=1000, offset=0, include_hidden=True)
    assert not any(u.user_id == created.user_id for u in default_page.items)
    assert not any(u.user_id == created.user_id for u in hidden_page.items)


def test_permanent_delete_blocks_deleting_your_own_account(db, user_service):
    admin = _make_admin(db)
    with pytest.raises(ValidationError):
        user_service.permanent_delete_user(admin.user_id, admin)
    # Never actually deleted - still a real, live row.
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM app_user WHERE user_id = %s", (str(admin.user_id),))
        assert cur.fetchone()["n"] == 1


def test_permanent_delete_missing_user_is_not_found(db, user_service):
    admin = _make_admin(db)
    with pytest.raises(NotFoundError):
        user_service.permanent_delete_user(uuid.uuid4(), admin)


def test_permanent_delete_of_added_by_sets_fk_null_and_membership_survives(db, user_service):
    """The wave's own example verbatim: a user who was `added_by` on a
    project_membership row. Deleting them must leave that membership row
    intact with added_by set to NULL - not RESTRICT-fail, not cascade the
    membership away too (only the added_by ATTRIBUTION disappears; the grant
    itself, keyed by its own distinct user_id, is untouched)."""
    admin = _make_admin(db)
    creator = user_service.create_user(
        f"creator-{uuid.uuid4()}", "whatever-password-1", Role.GIS_ASSOCIATE, admin
    )
    beneficiary = user_service.create_user(
        f"beneficiary-{uuid.uuid4()}", "whatever-password-1", Role.VIEWER, admin
    )
    with db.transaction() as cur:
        project_id, _created = ProjectRepository(cur).find_or_create_by_name(
            f"FkSurvival-{uuid.uuid4()}", "Karnataka"
        )
        ProjectMembershipRepository(cur).add(
            project_id=project_id, user_id=beneficiary.user_id,
            role=Role.VIEWER, added_by=creator.user_id,
        )

    user_service.permanent_delete_user(creator.user_id, admin)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT user_id, added_by FROM project_membership "
            "WHERE project_id = %s AND user_id = %s",
            (str(project_id), str(beneficiary.user_id)),
        )
        row = cur.fetchone()
    assert row is not None  # the membership grant survives
    assert row["added_by"] is None  # only the attribution to the deleted creator is gone


def test_permanent_delete_preserves_audit_log_readable_by_username(db, user_service, auth_service):
    """The other half of the wave's own example: a user with audit_log
    entries. `actor_name` is a plain TEXT column, independent of the
    actor_id FK - confirmed by reading the schema before writing this test -
    so the log stays readable by username even after the row is gone."""
    admin = _make_admin(db)
    username = f"auditedthenremoved-{uuid.uuid4()}"
    password = "audited-password-1"
    created = user_service.create_user(username, password, Role.VIEWER, admin)
    auth_service.login(username, password)  # generates a real LOGIN audit_log row for this user

    user_service.permanent_delete_user(created.user_id, admin)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT actor_id, actor_name, action FROM audit_log "
            "WHERE action = 'login' AND actor_name = %s ORDER BY created_at DESC LIMIT 1",
            (username,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row["actor_id"] is None  # FK correctly went to NULL, row not deleted
    assert row["actor_name"] == username  # still readable as plain text


def test_permanent_delete_itself_is_audit_logged_with_actor_name(db, user_service):
    admin = _make_admin(db)
    created = user_service.create_user(
        f"loggedremoval-{uuid.uuid4()}", "whatever-password-1", Role.VIEWER, admin
    )

    user_service.permanent_delete_user(created.user_id, admin)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT actor_id, actor_name, detail FROM audit_log "
            "WHERE action = 'permanently_delete_user' AND target = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (str(created.user_id),),
        )
        row = cur.fetchone()
    assert row is not None
    assert row["actor_id"] == admin.user_id  # the actor (admin) still exists, so this is populated
    assert row["actor_name"] == admin.username
    assert created.username in row["detail"]  # the REMOVED user's username, readable as text


# =========================================================== Wave: password reset


# --------------------------------------------------------------- admin reset (tier: no old password)


def test_admin_reset_password_then_target_logs_in_with_new_password(db, user_service, auth_service):
    admin = _make_admin(db)
    username = f"resetme-{uuid.uuid4()}"
    old_password = "original-password-1"
    new_password = "brand-new-password-2"
    created = user_service.create_user(username, old_password, Role.VIEWER, admin)

    user_service.admin_reset_password(created.user_id, new_password, admin)

    pair = auth_service.login(username, new_password)  # must NOT raise
    me = auth_service.current_user_from_access(pair.access_token)
    assert me.username == username

    with pytest.raises(AuthError):
        auth_service.login(username, old_password)  # old password is dead


def test_admin_reset_password_is_audit_logged(db, user_service):
    admin = _make_admin(db)
    created = user_service.create_user(
        f"resetlogged-{uuid.uuid4()}", "original-password-1", Role.VIEWER, admin
    )

    user_service.admin_reset_password(created.user_id, "brand-new-password-2", admin)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT actor_id, actor_name, detail FROM audit_log "
            "WHERE action = 'reset_user_password' AND target = %s ORDER BY created_at DESC LIMIT 1",
            (str(created.user_id),),
        )
        row = cur.fetchone()
    assert row is not None
    assert row["actor_id"] == admin.user_id
    assert row["actor_name"] == admin.username
    assert created.username in row["detail"]


def test_admin_cannot_reset_their_own_password(db, user_service, auth_service):
    """Explicit decision this wave asked for: the two flows stay separate -
    an Administrator must use self-service change for their own account."""
    admin = _make_admin(db)
    with pytest.raises(ValidationError):
        user_service.admin_reset_password(admin.user_id, "some-new-password-1", admin)


def test_admin_reset_password_rejects_short_password(db, user_service):
    admin = _make_admin(db)
    created = user_service.create_user(
        f"resetshort-{uuid.uuid4()}", "original-password-1", Role.VIEWER, admin
    )
    with pytest.raises(ValidationError):
        user_service.admin_reset_password(created.user_id, "short1", admin)


def test_admin_reset_password_missing_user_is_not_found(db, user_service):
    admin = _make_admin(db)
    with pytest.raises(NotFoundError):
        user_service.admin_reset_password(uuid.uuid4(), "some-new-password-1", admin)


# --------------------------------------------------------------- self-service change


def test_change_password_with_correct_current_password_then_login_with_new(db, user_service, auth_service):
    admin = _make_admin(db)
    username = f"selfchange-{uuid.uuid4()}"
    old_password = "original-password-1"
    new_password = "chosen-by-myself-2"
    created = user_service.create_user(username, old_password, Role.VIEWER, admin)
    actor = CurrentUser(user_id=created.user_id, username=username, role=Role.VIEWER)

    auth_service.change_password(actor, old_password, new_password)

    pair = auth_service.login(username, new_password)  # must NOT raise
    assert pair.access_token
    with pytest.raises(AuthError):
        auth_service.login(username, old_password)  # old password is dead


def test_change_password_with_wrong_current_password_is_rejected_and_unchanged(
    db, user_service, auth_service
):
    admin = _make_admin(db)
    username = f"selfchangewrong-{uuid.uuid4()}"
    real_password = "my-real-password-1"
    created = user_service.create_user(username, real_password, Role.VIEWER, admin)
    actor = CurrentUser(user_id=created.user_id, username=username, role=Role.VIEWER)

    with pytest.raises(AuthError):
        auth_service.change_password(actor, "totally-wrong-guess", "attacker-chosen-password-2")

    # Password is UNCHANGED - proven via a subsequent real login with the
    # ORIGINAL password (not just "no exception was raised for the change").
    pair = auth_service.login(username, real_password)
    assert pair.access_token
    with pytest.raises(AuthError):
        auth_service.login(username, "attacker-chosen-password-2")


def test_change_password_rejects_short_new_password(db, user_service, auth_service):
    admin = _make_admin(db)
    username = f"selfchangeshort-{uuid.uuid4()}"
    real_password = "my-real-password-1"
    created = user_service.create_user(username, real_password, Role.VIEWER, admin)
    actor = CurrentUser(user_id=created.user_id, username=username, role=Role.VIEWER)

    with pytest.raises(ValidationError):
        auth_service.change_password(actor, real_password, "short1")

    # Still unchanged - the old password still works.
    pair = auth_service.login(username, real_password)
    assert pair.access_token


def test_change_password_is_audit_logged_with_own_username(db, user_service, auth_service):
    admin = _make_admin(db)
    username = f"selfchangelogged-{uuid.uuid4()}"
    old_password = "original-password-1"
    created = user_service.create_user(username, old_password, Role.VIEWER, admin)
    actor = CurrentUser(user_id=created.user_id, username=username, role=Role.VIEWER)

    auth_service.change_password(actor, old_password, "new-password-for-logging-2")

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT actor_id, actor_name, detail FROM audit_log "
            "WHERE action = 'change_own_password' AND target = %s ORDER BY created_at DESC LIMIT 1",
            (str(created.user_id),),
        )
        row = cur.fetchone()
    assert row is not None
    assert row["actor_id"] == created.user_id  # they are their own actor
    assert row["actor_name"] == username
