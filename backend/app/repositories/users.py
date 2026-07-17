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

    def upsert(self, username: str, password_hash: str, role: str) -> dict[str, Any]:
        self.cur.execute(
            """
            INSERT INTO app_user (username, password_hash, role)
            VALUES (%s, %s, %s)
            ON CONFLICT (username) DO UPDATE
              SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role
            RETURNING user_id, username, role
            """,
            (username, password_hash, role),
        )
        row = self.cur.fetchone()
        assert row is not None
        return row
