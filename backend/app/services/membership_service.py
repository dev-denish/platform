"""
Project-membership management (Wave: project-level RBAC).

See app.domain.authz for the underlying view/manage decision this service
builds on. This is the CRUD surface over `project_membership`: list a
project's members, add one (with an explicit or prefilled role), remove one,
change one's project-level role - each an audited write, same as
ProjectService.delete_project already is for project deletion.
"""
from __future__ import annotations

from uuid import UUID

import psycopg

from app.core.db import Database
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.authz import require_project_manage, require_project_view
from app.domain.dtos import CurrentUser, MemberOut, ProjectMembers
from app.domain.enums import PROJECT_ROLES, AuditAction, Role
from app.repositories.audit import AuditRepository
from app.repositories.memberships import ProjectMembershipRepository
from app.repositories.users import UserRepository


def _require_project_role(role: Role) -> None:
    """Administrator is a global-only concept (see enums.PROJECT_ROLES's
    docstring) - never a valid value for a project_membership row, so this
    is checked at the service boundary regardless of whether the value came
    from a caller-supplied role or a prefilled default."""
    if role not in PROJECT_ROLES:
        raise ValidationError(
            f"'{role.value}' is not a valid project-level role. Allowed: "
            f"{', '.join(r.value for r in PROJECT_ROLES)}."
        )


class MembershipService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_members(self, project_id: UUID, actor: CurrentUser) -> ProjectMembers:
        with self.db.connection() as conn, conn.cursor() as cur:
            require_project_view(cur, project_id, actor)
            rows = ProjectMembershipRepository(cur).list_for_project(project_id)
        return ProjectMembers(project_id=project_id, members=[MemberOut(**r) for r in rows])

    def add_member(
        self, project_id: UUID, username: str, role: Role | None, actor: CurrentUser
    ) -> MemberOut:
        with self.db.transaction() as cur:
            require_project_manage(cur, project_id, actor)
            target = UserRepository(cur).get_by_username(username)
            if target is None:
                raise NotFoundError("No such user.")
            # Prefill from the target's own global role when the caller
            # doesn't specify one (app.domain.enums's "sensible default"
            # rule) - a global Administrator has no valid project-level
            # role to prefill from, so that case must be given one explicitly.
            if role is not None:
                effective_role = role
            elif target["role"] == Role.ADMINISTRATOR.value:
                raise ValidationError(
                    "This user's global role is Administrator, which has no project-level "
                    "equivalent - specify an explicit role for their membership on this project."
                )
            else:
                effective_role = Role(target["role"])
            _require_project_role(effective_role)

            try:
                row = ProjectMembershipRepository(cur).add(
                    project_id=project_id, user_id=target["user_id"],
                    role=effective_role, added_by=actor.user_id,
                )
            except psycopg.errors.UniqueViolation as e:
                raise ConflictError(f"{username} is already a member of this project.") from e

            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.ADD_PROJECT_MEMBER,
                target=f"{project_id}:{target['user_id']}",
                detail=f"Added {username} as {effective_role.value}.",
            )
        return MemberOut(username=username, **row)

    def remove_member(self, project_id: UUID, user_id: UUID, actor: CurrentUser) -> None:
        with self.db.transaction() as cur:
            require_project_manage(cur, project_id, actor)
            removed = ProjectMembershipRepository(cur).remove(
                project_id=project_id, user_id=user_id, removed_by=actor.user_id,
            )
            if not removed:
                raise NotFoundError("This user is not a member of this project.")
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.REMOVE_PROJECT_MEMBER,
                target=f"{project_id}:{user_id}",
                detail="Removed project member.",
            )

    def update_role(
        self, project_id: UUID, user_id: UUID, role: Role, actor: CurrentUser
    ) -> MemberOut:
        _require_project_role(role)
        with self.db.transaction() as cur:
            require_project_manage(cur, project_id, actor)
            row = ProjectMembershipRepository(cur).update_role(
                project_id=project_id, user_id=user_id, role=role
            )
            if row is None:
                raise NotFoundError("This user is not a member of this project.")
            target = UserRepository(cur).get_by_id(user_id)
            assert target is not None
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.UPDATE_PROJECT_MEMBER_ROLE,
                target=f"{project_id}:{user_id}",
                detail=f"Changed role to {role.value}.",
            )
        return MemberOut(username=target["username"], **row)
