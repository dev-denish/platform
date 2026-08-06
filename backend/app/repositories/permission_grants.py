"""Per-user permission grant persistence (Wave: permission grants)."""
from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg


class PermissionGrantRepository:
    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur

    def list_for_user(self, user_id: UUID | str) -> list[dict[str, Any]]:
        self.cur.execute(
            """
            SELECT g.permission_name, g.granted_by, g.granted_at,
                   u.username AS granted_by_username
            FROM user_permission_grant g
            LEFT JOIN app_user u ON u.user_id = g.granted_by
            WHERE g.user_id = %s
            ORDER BY g.permission_name
            """,
            (str(user_id),),
        )
        return list(self.cur.fetchall())

    def has_grant(self, user_id: UUID | str, permission_name: str) -> bool:
        self.cur.execute(
            "SELECT 1 FROM user_permission_grant WHERE user_id = %s AND permission_name = %s",
            (str(user_id), permission_name),
        )
        return self.cur.fetchone() is not None

    def grant(
        self, user_id: UUID | str, permission_name: str, granted_by: UUID | str
    ) -> dict[str, Any]:
        """Idempotent: re-granting an already-held permission just refreshes
        who granted it and when, rather than erroring."""
        self.cur.execute(
            """
            INSERT INTO user_permission_grant (user_id, permission_name, granted_by)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, permission_name) DO UPDATE
              SET granted_by = EXCLUDED.granted_by, granted_at = now()
            RETURNING permission_name, granted_by, granted_at
            """,
            (str(user_id), permission_name, str(granted_by)),
        )
        row = self.cur.fetchone()
        assert row is not None
        return row

    def revoke(self, user_id: UUID | str, permission_name: str) -> bool:
        self.cur.execute(
            "DELETE FROM user_permission_grant WHERE user_id = %s AND permission_name = %s",
            (str(user_id), permission_name),
        )
        return self.cur.rowcount == 1
