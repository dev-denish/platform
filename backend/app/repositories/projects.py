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

    def list_paginated(
        self,
        limit: int,
        offset: int,
        *,
        member_id: UUID | str | None = None,
        search: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """`member_id` is None for an Administrator (every project, no
        filter - see ProjectService.list_projects) or a user_id to restrict
        the listing to projects that user has a live membership row on
        (Wave: project-level RBAC). Same two-query shape (count, then page)
        either way, just with an extra JOIN + WHERE clause spliced in.

        `search` is an optional case-insensitive partial match on name,
        ANDed alongside the deleted_at/membership filters above - never a
        replacement for them."""
        member_join = ""
        params: list[Any] = []
        if member_id is not None:
            member_join = (
                "JOIN project_membership pm ON pm.project_id = p.project_id "
                "AND pm.user_id = %s AND pm.removed_at IS NULL"
            )
            params.append(str(member_id))

        search_clause = ""
        if search:
            search_clause = "AND p.name ILIKE %s"
            params.append(f"%{search}%")

        # member_join/search_clause are always one of the fixed literals set
        # above - never derived from the raw `member_id`/`search` values
        # (which travel only as bound %s parameters) - so this f-string
        # splices in constant clauses, not attacker-reachable SQL.
        self.cur.execute(
            f"SELECT count(*) AS n FROM project p {member_join} WHERE p.deleted_at IS NULL {search_clause}",  # noqa: S608, E501
            tuple(params),
        )
        total = int(self.cur.fetchone()["n"])  # type: ignore[index]
        self.cur.execute(
            f"""
            SELECT p.project_id, p.name, p.region, p.status,
                   d.dataset_id AS latest_dataset_id,
                   d.accuracy_score AS latest_accuracy,
                   d.date_processed AS latest_processed
            FROM project p
            {member_join}
            LEFT JOIN LATERAL (
                SELECT dataset_id, accuracy_score, date_processed
                FROM dataset d
                WHERE d.project_id = p.project_id AND d.deleted_at IS NULL
                ORDER BY d.loaded_at DESC
                LIMIT 1
            ) d ON true
            WHERE p.deleted_at IS NULL {search_clause}
            ORDER BY p.name
            LIMIT %s OFFSET %s
            """,  # noqa: S608 - see the identical note on the count query above
            (*params, limit, offset),
        )
        return list(self.cur.fetchall()), total

    def get(self, project_id: UUID | str) -> dict[str, Any] | None:
        self.cur.execute(
            "SELECT project_id, name, region, status, start_date "
            "FROM project WHERE project_id = %s AND deleted_at IS NULL",
            (str(project_id),),
        )
        return self.cur.fetchone()

    def find_or_create_by_name(self, name: str, region: str) -> tuple[UUID, bool]:
        """Atomic: relies on the unique index on lower(name). Concurrent first-time
        uploads of the same project can no longer create duplicates.

        The index (see migration 0001) is PARTIAL - `WHERE deleted_at IS NULL` - so
        Postgres will only accept it as an ON CONFLICT arbiter if the same predicate
        is repeated here.

        Returns `(project_id, created)`. `created` is the standard Postgres
        "was this an INSERT or did ON CONFLICT DO UPDATE fire" idiom
        (`xmax = 0` is true only for a row this same command just inserted) -
        Wave: project-level RBAC uses it to decide whether the uploader
        becomes this project's first member (see
        IngestionService._resolve_project) rather than needing a matching
        membership row that, for a genuinely new project, cannot exist yet.
        """
        self.cur.execute(
            """
            INSERT INTO project (name, region, start_date, status)
            VALUES (%s, %s, CURRENT_DATE, 'Active')
            ON CONFLICT (lower(name)) WHERE deleted_at IS NULL
              DO UPDATE SET name = project.name
            RETURNING project_id, (xmax = 0) AS created
            """,
            (name, region),
        )
        row = self.cur.fetchone()
        assert row is not None
        return row["project_id"], bool(row["created"])  # type: ignore[index]

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
