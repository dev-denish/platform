"""Project persistence: paginated listing (with the latest dataset per project via
a LATERAL join), atomic find-or-create by name (ON CONFLICT - no race), and soft
delete. Requires the unique index on lower(name) and the FK/loaded_at indexes added
in the Alembic migration, without which these queries would sequentially scan."""
from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg


class ProjectRepository:
    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur

    def list_paginated(self, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        self.cur.execute(
            "SELECT count(*) AS n FROM project WHERE deleted_at IS NULL"
        )
        total = int(self.cur.fetchone()["n"])  # type: ignore[index]
        self.cur.execute(
            """
            SELECT p.project_id, p.name, p.region, p.status,
                   d.dataset_id AS latest_dataset_id,
                   d.accuracy_score AS latest_accuracy,
                   d.date_processed AS latest_processed
            FROM project p
            LEFT JOIN LATERAL (
                SELECT dataset_id, accuracy_score, date_processed
                FROM dataset d
                WHERE d.project_id = p.project_id AND d.deleted_at IS NULL
                ORDER BY d.loaded_at DESC
                LIMIT 1
            ) d ON true
            WHERE p.deleted_at IS NULL
            ORDER BY p.name
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
        return list(self.cur.fetchall()), total

    def get(self, project_id: UUID | str) -> dict[str, Any] | None:
        self.cur.execute(
            "SELECT project_id, name, region, status, start_date "
            "FROM project WHERE project_id = %s AND deleted_at IS NULL",
            (str(project_id),),
        )
        return self.cur.fetchone()

    def find_or_create_by_name(self, name: str, region: str) -> UUID:
        """Atomic: relies on the unique index on lower(name). Concurrent first-time
        uploads of the same project can no longer create duplicates.

        The index (see migration 0001) is PARTIAL - `WHERE deleted_at IS NULL` - so
        Postgres will only accept it as an ON CONFLICT arbiter if the same predicate
        is repeated here.
        """
        self.cur.execute(
            """
            INSERT INTO project (name, region, start_date, status)
            VALUES (%s, %s, CURRENT_DATE, 'Active')
            ON CONFLICT (lower(name)) WHERE deleted_at IS NULL
              DO UPDATE SET name = project.name
            RETURNING project_id
            """,
            (name, region),
        )
        return self.cur.fetchone()["project_id"]  # type: ignore[index]

    def get_version(self, project_id: UUID | str) -> int | None:
        """Read the current optimistic-lock version for a live (non-deleted) row,
        for a caller about to soft-delete it. Deliberately narrow - unlike `get()`,
        it doesn't need to return the rest of the row for that."""
        self.cur.execute(
            "SELECT version FROM project WHERE project_id = %s AND deleted_at IS NULL",
            (str(project_id),),
        )
        row = self.cur.fetchone()
        return int(row["version"]) if row else None

    def soft_delete(
        self, project_id: UUID | str, expected_version: int, deleted_by: UUID | str
    ) -> bool:
        """Optimistic lock: succeeds only if the row's version matches what the
        caller last read, preventing lost updates under concurrent edits. Also
        guarded by `deleted_at IS NULL`, so a second concurrent delete attempt
        (even one that happened to read the same version first) affects zero rows
        - the caller treats that identically to "not found"."""
        self.cur.execute(
            """
            UPDATE project
            SET deleted_at = now(), deleted_by = %s, version = version + 1
            WHERE project_id = %s AND version = %s AND deleted_at IS NULL
            """,
            (str(deleted_by), str(project_id), expected_version),
        )
        return self.cur.rowcount == 1
