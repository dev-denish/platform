"""Project-membership persistence (Wave: project-level RBAC).

Soft-deletable like every other core table here - `removed_at`/`removed_by`,
never a hard DELETE (see project.deleted_at/deleted_by for the identical
convention) - so a user's membership HISTORY on a project survives their
removal instead of vanishing. Uniqueness is a PARTIAL index on
(project_id, user_id) WHERE removed_at IS NULL (migration 0007) - the same
"soft-deletable uniqueness" pattern project's own `uq_project_name_lower`
index already uses - so someone removed and later re-added gets a fresh row,
not a collision with their own past membership.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg

from app.domain.enums import Role


class ProjectMembershipRepository:
    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur

    def get_role(self, project_id: UUID | str, user_id: UUID | str) -> Role | None:
        self.cur.execute(
            "SELECT role FROM project_membership "
            "WHERE project_id = %s AND user_id = %s AND removed_at IS NULL",
            (str(project_id), str(user_id)),
        )
        row = self.cur.fetchone()
        return Role(row["role"]) if row else None

    def list_for_project(self, project_id: UUID | str) -> list[dict[str, Any]]:
        self.cur.execute(
            """
            SELECT pm.user_id, u.username, pm.role, pm.added_at, pm.added_by
            FROM project_membership pm
            JOIN app_user u ON u.user_id = pm.user_id
            WHERE pm.project_id = %s AND pm.removed_at IS NULL
            ORDER BY u.username
            """,
            (str(project_id),),
        )
        return list(self.cur.fetchall())

    def add(
        self, *, project_id: UUID | str, user_id: UUID | str, role: Role, added_by: UUID | str
    ) -> dict[str, Any]:
        """Raises psycopg.errors.UniqueViolation if this user already has a
        live membership row on this project - the partial unique index is
        the actual guard; the caller (MembershipService) translates that
        into a client-safe ConflictError."""
        self.cur.execute(
            """
            INSERT INTO project_membership (project_id, user_id, role, added_by)
            VALUES (%s, %s, %s, %s)
            RETURNING user_id, role, added_at, added_by
            """,
            (str(project_id), str(user_id), role.value, str(added_by)),
        )
        row = self.cur.fetchone()
        assert row is not None
        return row

    def remove(self, *, project_id: UUID | str, user_id: UUID | str, removed_by: UUID | str) -> bool:
        self.cur.execute(
            """
            UPDATE project_membership
            SET removed_at = now(), removed_by = %s
            WHERE project_id = %s AND user_id = %s AND removed_at IS NULL
            """,
            (str(removed_by), str(project_id), str(user_id)),
        )
        return self.cur.rowcount == 1

    def update_role(
        self, *, project_id: UUID | str, user_id: UUID | str, role: Role
    ) -> dict[str, Any] | None:
        self.cur.execute(
            """
            UPDATE project_membership SET role = %s
            WHERE project_id = %s AND user_id = %s AND removed_at IS NULL
            RETURNING user_id, role, added_at, added_by
            """,
            (role.value, str(project_id), str(user_id)),
        )
        return self.cur.fetchone()
