"""User persistence. All SQL is parameterised; the repository layer is the ONLY
place raw SQL lives, so the service/API layers stay database-agnostic and there is
one place to audit for injection (there is none - every value is bound)."""
from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg


class UserRepository:
    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur

    def get_by_username(self, username: str) -> dict[str, Any] | None:
        self.cur.execute(
            "SELECT user_id, username, password_hash, role "
            "FROM app_user WHERE username = %s AND deleted_at IS NULL",
            (username,),
        )
        return self.cur.fetchone()

    def get_by_id(self, user_id: UUID | str) -> dict[str, Any] | None:
        self.cur.execute(
            "SELECT user_id, username, role FROM app_user "
            "WHERE user_id = %s AND deleted_at IS NULL",
            (str(user_id),),
        )
        return self.cur.fetchone()

    def get_any_by_id(self, user_id: UUID | str) -> dict[str, Any] | None:
        """Unlike `get_by_id`, no `deleted_at` filter at all - for the
        hide/unhide/permanent-delete/reset-password actions (Wave: three-tier
        removal; Wave: password reset), which must be able to target an
        ALREADY-deactivated account (e.g. deactivate first, hide or
        permanently delete later) - `get_by_id` would wrongly report "not
        found" for exactly that case. Includes `password_hash` for the
        self-service change-password flow's "verify current password" step
        (AuthService.change_password) - never serialized into any API
        response DTO (UserOut has no such field), so returning it here is
        safe: this method is only ever called from the service layer."""
        self.cur.execute(
            "SELECT user_id, username, role, password_hash FROM app_user WHERE user_id = %s",
            (str(user_id),),
        )
        return self.cur.fetchone()

    def update_password(self, user_id: UUID | str, password_hash: str) -> bool:
        """Used by both password-reset flows (Wave: password reset) - the
        distinction between "an Administrator resetting someone else's" and
        "a user changing their own after proving they know the current one"
        is entirely in the SERVICE layer (UserService.admin_reset_password
        vs AuthService.change_password); this repository method has no
        opinion on which flow called it, same as `hash_password`/
        `verify_password` themselves not caring who invokes them."""
        self.cur.execute(
            "UPDATE app_user SET password_hash = %s WHERE user_id = %s",
            (password_hash, str(user_id)),
        )
        return self.cur.rowcount == 1

    def upsert(self, username: str, password_hash: str, role: str) -> dict[str, Any]:
        """Insert-or-reset-password for a LIVE account only - the CLI's
        (scripts/create_admin.py) legitimate re-run case, e.g. resetting your
        own account's password by running the script again with the same
        username.

        Fixed bug (Wave: User Management): `ON CONFLICT (username)` used to
        match ANY row with that username, including a soft-deleted one -
        silently overwriting a dead account's password/role while leaving
        `deleted_at` untouched, so it stayed permanently invisible to login.
        The conflict target now matches the PARTIAL unique index from
        migration 0008 (`WHERE deleted_at IS NULL`) - a username belonging
        only to a deactivated row no longer conflicts at all, so this
        correctly INSERTs a fresh, live row instead of reviving the dead
        one. The two must never drift out of sync with each other.
        """
        self.cur.execute(
            """
            INSERT INTO app_user (username, password_hash, role)
            VALUES (%s, %s, %s)
            ON CONFLICT (username) WHERE deleted_at IS NULL DO UPDATE
              SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role
            RETURNING user_id, username, role
            """,
            (username, password_hash, role),
        )
        row = self.cur.fetchone()
        assert row is not None
        return row

    def create(self, username: str, password_hash: str, role: str) -> dict[str, Any]:
        """Plain INSERT for the in-app 'create user' screen (Wave: User
        Management) - deliberately NOT `upsert()`: that endpoint's caller
        (UserService.create_user) already did an explicit
        `get_by_username` check and must fail cleanly on a live duplicate,
        never silently update an existing person's password. This still
        relies on the same partial unique index as a last-resort guard
        against a concurrent create racing the same username (see
        UserService.create_user's UniqueViolation handling) - a genuinely
        rare race, not the normal path.
        """
        self.cur.execute(
            """
            INSERT INTO app_user (username, password_hash, role)
            VALUES (%s, %s, %s)
            RETURNING user_id, username, role, created_at, deleted_at
            """,
            (username, password_hash, role),
        )
        row = self.cur.fetchone()
        assert row is not None
        return row

    def list_all(
        self, limit: int, offset: int, *, include_hidden: bool = False
    ) -> tuple[list[dict[str, Any]], int]:
        """EVERY account, active AND deactivated - the Users screen's whole
        point is showing status, so (unlike every other `list_paginated` in
        this codebase) this deliberately does NOT filter on deleted_at.

        `include_hidden` (Wave: three-tier removal) is the ONE filter this
        listing does apply by default (`hidden_at IS NULL`) - hidden is a
        distinct, independent state from deactivated (see migration 0009's
        docstring), and the default Users list must not show it; the
        "Show hidden" toggle passes `include_hidden=True` to see them too.
        """
        hidden_filter = "" if include_hidden else "WHERE hidden_at IS NULL"
        self.cur.execute(f"SELECT count(*) AS n FROM app_user {hidden_filter}")  # noqa: S608 - hidden_filter is a fixed literal, never interpolated user input
        total = int(self.cur.fetchone()["n"])  # type: ignore[index]
        self.cur.execute(
            f"""
            SELECT user_id, username, role, created_at,
                   deleted_at, deleted_by, hidden_at, hidden_by
            FROM app_user
            {hidden_filter}
            ORDER BY username
            LIMIT %s OFFSET %s
            """,  # noqa: S608
            (limit, offset),
        )
        return list(self.cur.fetchall()), total

    def deactivate(self, user_id: UUID | str, deleted_by: UUID | str) -> bool:
        """Soft-delete only, same convention as project/dataset/membership -
        guarded by `deleted_at IS NULL` so a second deactivate of an already
        deactivated account is a no-op (rowcount 0), not an error and not a
        double-write of deleted_at/deleted_by."""
        self.cur.execute(
            """
            UPDATE app_user SET deleted_at = now(), deleted_by = %s
            WHERE user_id = %s AND deleted_at IS NULL
            """,
            (str(deleted_by), str(user_id)),
        )
        return self.cur.rowcount == 1

    def activate(self, user_id: UUID | str) -> bool:
        """Reverses `deactivate`: clears BOTH deleted_at and deleted_by, same
        "fully reversible, no stale attribution" convention as `unhide`.
        Deliberately never touches hidden_at/hidden_by - independent of the
        hide tier, same as `deactivate` never touching them either."""
        self.cur.execute(
            """
            UPDATE app_user SET deleted_at = NULL, deleted_by = NULL
            WHERE user_id = %s AND deleted_at IS NOT NULL
            """,
            (str(user_id),),
        )
        return self.cur.rowcount == 1

    def hide(self, user_id: UUID | str, hidden_by: UUID | str) -> bool:
        """Wave: three-tier removal, middle tier. Deliberately independent of
        `deleted_at` - never touches it, never checks it - so a live account
        can be hidden without being deactivated (and vice versa). Same
        idempotent-guard convention as `deactivate`: a second hide is a
        no-op, not an error."""
        self.cur.execute(
            """
            UPDATE app_user SET hidden_at = now(), hidden_by = %s
            WHERE user_id = %s AND hidden_at IS NULL
            """,
            (str(hidden_by), str(user_id)),
        )
        return self.cur.rowcount == 1

    def unhide(self, user_id: UUID | str) -> bool:
        """Fully reversible - clears BOTH columns, not just hidden_at, so a
        restored account carries no stale hidden_by attribution."""
        self.cur.execute(
            """
            UPDATE app_user SET hidden_at = NULL, hidden_by = NULL
            WHERE user_id = %s AND hidden_at IS NOT NULL
            """,
            (str(user_id),),
        )
        return self.cur.rowcount == 1

    def permanently_delete(self, user_id: UUID | str) -> bool:
        """The actual hard delete. Irreversible by construction - there is no
        soft-delete column to set back. Callers (UserService.
        permanent_delete_user) MUST have already verified every FK
        referencing app_user is safe (see `referencing_foreign_keys` below)
        before calling this."""
        self.cur.execute("DELETE FROM app_user WHERE user_id = %s", (str(user_id),))
        return self.cur.rowcount == 1

    def referencing_foreign_keys(self) -> list[dict[str, Any]]:
        """Every FK in the schema that references `app_user(user_id)`, with
        its ON DELETE behavior - the real, live schema, not a hardcoded
        belief about it that could silently drift after a future migration.
        UserService.permanent_delete_user runs this fresh before every
        permanent delete and blocks with a clear error if anything here
        isn't SET NULL or one of the two explicitly-reviewed CASCADE columns
        (project_membership.user_id, revoked_token.user_id - a membership/
        revoked-token row cannot meaningfully survive with no user, so
        cascading those away IS the correct behavior, not an accident;
        every attribution-only column - deleted_by/added_by/removed_by/
        actor_id/hidden_by - is SET NULL, preserving history text)."""
        self.cur.execute(
            """
            SELECT tc.table_name, kcu.column_name, rc.delete_rule
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.referential_constraints rc
              ON tc.constraint_name = rc.constraint_name
             AND tc.table_schema = rc.constraint_schema
            JOIN information_schema.constraint_column_usage ccu
              ON rc.unique_constraint_name = ccu.constraint_name
             AND rc.unique_constraint_schema = ccu.constraint_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = 'public'
              AND ccu.table_name = 'app_user'
              AND ccu.column_name = 'user_id'
            """
        )
        return list(self.cur.fetchall())
