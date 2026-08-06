"""
Per-user permission grant management (Wave: permission grants).

The CRUD surface over `user_permission_grant`: list a user's grants, grant
one, revoke one - each an audited write, same convention as
MembershipService's project-membership CRUD. Administrator-only at the API
layer (MANAGE_PERMISSIONS_ROLES) - see app.domain.permissions.has_permission
for the separate "who may USE a granted permission" check every gated
feature calls.
"""
from __future__ import annotations

from uuid import UUID

from app.core.db import Database
from app.core.errors import NotFoundError, ValidationError
from app.domain.dtos import CurrentUser, PermissionGrantOut, UserPermissions
from app.domain.enums import AuditAction, Role
from app.domain.permissions import PERMISSION_NAMES
from app.repositories.audit import AuditRepository
from app.repositories.permission_grants import PermissionGrantRepository
from app.repositories.users import UserRepository


def _require_known_permission(permission_name: str) -> None:
    if permission_name not in PERMISSION_NAMES:
        raise ValidationError(f"'{permission_name}' is not a grantable permission.")


class PermissionService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_grants(self, user_id: UUID, actor: CurrentUser) -> UserPermissions:
        with self.db.connection() as conn, conn.cursor() as cur:
            if UserRepository(cur).get_any_by_id(user_id) is None:
                raise NotFoundError("User not found.")
            rows = PermissionGrantRepository(cur).list_for_user(user_id)
        return UserPermissions(user_id=user_id, grants=[PermissionGrantOut(**r) for r in rows])

    def grant(self, user_id: UUID, permission_name: str, actor: CurrentUser) -> PermissionGrantOut:
        _require_known_permission(permission_name)
        with self.db.transaction() as cur:
            target = UserRepository(cur).get_any_by_id(user_id)
            if target is None:
                raise NotFoundError("User not found.")
            # An Administrator already bypasses has_permission() unconditionally
            # - a grant on top of that would be dead data that never does
            # anything, so it's rejected here rather than silently accepted.
            if target["role"] == Role.ADMINISTRATOR.value:
                raise ValidationError(
                    "This user is an Administrator and already has every permission implicitly."
                )
            row = PermissionGrantRepository(cur).grant(user_id, permission_name, actor.user_id)
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.GRANT_PERMISSION, target=f"{user_id}:{permission_name}",
                detail=f"Granted '{permission_name}' to '{target['username']}'.",
            )
        return PermissionGrantOut(granted_by_username=actor.username, **row)

    def revoke(self, user_id: UUID, permission_name: str, actor: CurrentUser) -> None:
        _require_known_permission(permission_name)
        with self.db.transaction() as cur:
            target = UserRepository(cur).get_any_by_id(user_id)
            if target is None:
                raise NotFoundError("User not found.")
            if not PermissionGrantRepository(cur).revoke(user_id, permission_name):
                raise NotFoundError("This user does not hold that permission.")
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.REVOKE_PERMISSION, target=f"{user_id}:{permission_name}",
                detail=f"Revoked '{permission_name}' from '{target['username']}'.",
            )
