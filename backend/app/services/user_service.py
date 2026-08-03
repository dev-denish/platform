"""
User-account management (Wave: User Management).

Existing implementation: the only way to create a login account was the
`scripts/create_admin.py` CLI, which required terminal/Docker access to the
API host. This is the service backing the in-app "Users" screen
(Administrator-only) that replaces that for day-to-day account creation.

Deliberately does NOT call `UserRepository.upsert()` for account creation -
that method is a deliberate insert-or-reset-password for a LIVE account
(the CLI's legitimate re-run case). This service's `create_user` instead does
an explicit `get_by_username` check first and fails clearly on a live
duplicate ("Username already taken") rather than silently reviving or
overwriting anyone - see UserRepository.create/upsert's own docstrings for
the bug this distinction fixes.
"""
from __future__ import annotations

from uuid import UUID

import psycopg

from app.core.db import Database
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.security import MIN_PASSWORD_LENGTH, hash_password
from app.domain.dtos import BulkDeleteItemResult, CurrentUser, Page, UserOut
from app.domain.enums import AuditAction, Role
from app.repositories.audit import AuditRepository
from app.repositories.users import UserRepository

# Wave: three-tier removal. FK columns referencing app_user(user_id) that are
# CASCADE rather than SET NULL, reviewed and confirmed intentional (not an
# accident permanent_delete_user should block on): a project_membership row
# IS that user's membership grant - it cannot mean anything with no user, so
# it must disappear with them; a revoked_token row exists only to be checked
# against that exact user_id on token refresh, so it's equally meaningless
# once they're gone. Neither is audit/attribution data (those columns -
# added_by, removed_by, deleted_by, actor_id, hidden_by - are all SET NULL,
# confirmed via UserRepository.referencing_foreign_keys below). Any OTHER
# non-SET-NULL FK discovered at delete time is treated as unreviewed and
# blocks the delete - see permanent_delete_user.
_REVIEWED_CASCADE_FKS = frozenset(
    {("project_membership", "user_id"), ("revoked_token", "user_id")}
)


class UserService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_users(self, limit: int, offset: int, *, include_hidden: bool = False) -> Page[UserOut]:
        with self.db.connection() as conn, conn.cursor() as cur:
            rows, total = UserRepository(cur).list_all(limit, offset, include_hidden=include_hidden)
        return Page[UserOut](
            items=[UserOut(**r) for r in rows], total=total, limit=limit, offset=offset
        )

    def create_user(
        self, username: str, password: str, role: Role, actor: CurrentUser
    ) -> UserOut:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValidationError(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
            )
        with self.db.transaction() as cur:
            repo = UserRepository(cur)
            if repo.get_by_username(username) is not None:
                raise ConflictError(f"'{username}' is already taken by an active account.")
            try:
                row = repo.create(username, hash_password(password), role.value)
            except psycopg.errors.UniqueViolation as e:
                # Lost a race with a concurrent create of the same username
                # between the check above and this INSERT - same client-safe
                # message, not a raw 500.
                raise ConflictError(f"'{username}' is already taken by an active account.") from e
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.CREATE_USER, target=str(row["user_id"]),
                detail=f"Created account '{username}' with role {role.value}.",
            )
        return UserOut(**row)

    def deactivate_user(self, user_id: UUID, actor: CurrentUser) -> None:
        with self.db.transaction() as cur:
            repo = UserRepository(cur)
            target = repo.get_by_id(user_id)
            if target is None:
                raise NotFoundError("User not found.")
            deactivated = repo.deactivate(user_id, deleted_by=actor.user_id)
            if not deactivated:
                # Lost a race with a concurrent deactivate (or someone
                # deactivated it between the read above and now) - same
                # "not found" a caller would get for a user that never
                # existed, not an error.
                raise NotFoundError("User not found.")
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.DEACTIVATE_USER, target=str(user_id),
                detail=f"Deactivated account '{target['username']}'.",
            )

    def activate_user(self, user_id: UUID, actor: CurrentUser) -> None:
        """Reverses `deactivate_user` - fully independent of hide/unhide (see
        UserRepository.activate's docstring): an activated account that was
        also hidden stays hidden until separately unhidden. Uses
        `get_any_by_id`, not `get_by_id`, since the whole point is targeting
        an already-deactivated account."""
        with self.db.transaction() as cur:
            repo = UserRepository(cur)
            target = repo.get_any_by_id(user_id)
            if target is None:
                raise NotFoundError("User not found.")
            if not repo.activate(user_id):
                raise NotFoundError("User not found.")
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.ACTIVATE_USER, target=str(user_id),
                detail=f"Reactivated account '{target['username']}'.",
            )

    def hide_user(self, user_id: UUID, actor: CurrentUser) -> None:
        """Wave: three-tier removal, middle tier - deliberately independent
        of deactivate (see UserRepository.hide's docstring). Uses
        `get_any_by_id`, not `get_by_id`: an already-deactivated account
        must still be hideable."""
        with self.db.transaction() as cur:
            repo = UserRepository(cur)
            target = repo.get_any_by_id(user_id)
            if target is None:
                raise NotFoundError("User not found.")
            if not repo.hide(user_id, hidden_by=actor.user_id):
                raise NotFoundError("User not found.")
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.HIDE_USER, target=str(user_id),
                detail=f"Hid account '{target['username']}' from the default Users list.",
            )

    def unhide_user(self, user_id: UUID, actor: CurrentUser) -> None:
        """Fully reversible restore - the whole point of this tier."""
        with self.db.transaction() as cur:
            repo = UserRepository(cur)
            target = repo.get_any_by_id(user_id)
            if target is None:
                raise NotFoundError("User not found.")
            if not repo.unhide(user_id):
                raise NotFoundError("User not found.")
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.UNHIDE_USER, target=str(user_id),
                detail=f"Restored account '{target['username']}' to the default Users list.",
            )

    def permanent_delete_user(self, user_id: UUID, actor: CurrentUser) -> None:
        """The hard-delete tier. Irreversible - no restore path, by design.

        Explicit decisions this wave asked for:
          * Blocks deleting your OWN account - unlike deactivate/hide, there
            is no undo, and an admin mid-request deleting the account whose
            token is authorizing that very request is a footgun with no
            recovery.
          * Verifies every FK referencing app_user is either SET NULL or one
            of the two reviewed-safe CASCADE columns (see
            `_REVIEWED_CASCADE_FKS` above) BEFORE attempting the DELETE -
            fresh, live schema introspection every call, not a cached belief
            that could go stale after a future migration. Any other
            behavior (RESTRICT/NO ACTION, or a CASCADE this code hasn't been
            told is safe) blocks the delete with a clear client-safe error
            instead of letting Postgres raise an unhandled IntegrityError or
            silently cascading away data nobody reviewed.
        """
        if user_id == actor.user_id:
            raise ValidationError("You cannot permanently delete your own account.")
        with self.db.transaction() as cur:
            repo = UserRepository(cur)
            target = repo.get_any_by_id(user_id)
            if target is None:
                raise NotFoundError("User not found.")

            unsafe = [
                fk for fk in repo.referencing_foreign_keys()
                if fk["delete_rule"] != "SET NULL"
                and (fk["table_name"], fk["column_name"]) not in _REVIEWED_CASCADE_FKS
            ]
            if unsafe:
                names = ", ".join(f"{fk['table_name']}.{fk['column_name']}" for fk in unsafe)
                raise ConflictError(
                    f"Cannot permanently delete this account: {names} would be affected in "
                    "an unreviewed way. Fix the schema before retrying."
                )

            if not repo.permanently_delete(user_id):
                raise NotFoundError("User not found.")
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.PERMANENTLY_DELETE_USER, target=str(user_id),
                detail=f"Permanently deleted account '{target['username']}'.",
            )

    def bulk_permanent_delete_users(
        self, user_ids: list[UUID], actor: CurrentUser
    ) -> list[BulkDeleteItemResult]:
        """Same checks as `permanent_delete_user`, per id, in ONE transaction
        for the whole batch: self-delete blocked, and the live schema-wide
        FK-safety check (an unreviewed non-SET-NULL FK blocks every id
        uniformly - it's a schema property, not a per-row one, so it's
        computed once, not re-queried per id). Policy: partial success - one
        id failing (self, not found, or the FK guard) never blocks the
        others; each id's outcome is reported individually, never silently
        dropped."""
        results: list[BulkDeleteItemResult] = []
        with self.db.transaction() as cur:
            repo = UserRepository(cur)
            audit = AuditRepository(cur)
            unsafe = [
                fk for fk in repo.referencing_foreign_keys()
                if fk["delete_rule"] != "SET NULL"
                and (fk["table_name"], fk["column_name"]) not in _REVIEWED_CASCADE_FKS
            ]
            unsafe_reason = None
            if unsafe:
                names = ", ".join(f"{fk['table_name']}.{fk['column_name']}" for fk in unsafe)
                unsafe_reason = (
                    f"Cannot permanently delete: {names} would be affected in an "
                    "unreviewed way. Fix the schema before retrying."
                )
            for user_id in user_ids:
                target = repo.get_any_by_id(user_id)
                name = target["username"] if target else str(user_id)
                if user_id == actor.user_id:
                    results.append(
                        BulkDeleteItemResult(
                            id=user_id, name=name, success=False,
                            error="You cannot permanently delete your own account.",
                        )
                    )
                    continue
                if target is None:
                    results.append(
                        BulkDeleteItemResult(
                            id=user_id, name=name, success=False, error="User not found.",
                        )
                    )
                    continue
                if unsafe_reason:
                    results.append(
                        BulkDeleteItemResult(
                            id=user_id, name=name, success=False, error=unsafe_reason,
                        )
                    )
                    continue
                if not repo.permanently_delete(user_id):
                    results.append(
                        BulkDeleteItemResult(
                            id=user_id, name=name, success=False, error="User not found.",
                        )
                    )
                    continue
                audit.record(
                    actor_id=actor.user_id, actor_name=actor.username,
                    action=AuditAction.PERMANENTLY_DELETE_USER, target=str(user_id),
                    detail=f"Permanently deleted account '{name}' (bulk delete).",
                )
                results.append(BulkDeleteItemResult(id=user_id, name=name, success=True))
        return results

    def admin_reset_password(self, user_id: UUID, new_password: str, actor: CurrentUser) -> None:
        """Wave: password reset, Administrator side. No old password
        involved - the admin isn't that person, so there's nothing of
        theirs to verify (contrast AuthService.change_password, the
        self-service side, which DOES verify one).

        Explicit decision this wave asked for: an Administrator can NOT use
        this to reset their OWN password - that's what self-service change
        is for, and blurring the two would mean an admin could bypass ever
        having to prove they know their current password just by routing
        through this endpoint instead."""
        if user_id == actor.user_id:
            raise ValidationError(
                "Use the self-service password change to update your own password."
            )
        if len(new_password) < MIN_PASSWORD_LENGTH:
            raise ValidationError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
        with self.db.transaction() as cur:
            repo = UserRepository(cur)
            target = repo.get_any_by_id(user_id)
            if target is None:
                raise NotFoundError("User not found.")
            repo.update_password(user_id, hash_password(new_password))
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.RESET_USER_PASSWORD, target=str(user_id),
                detail=f"Reset password for account '{target['username']}'.",
            )
