"""Background-job persistence (Phase 2). Cursor-based, plain SQL - mirrors the
style of the other repositories (see audit.py, datasets.py); no ORM.

Idempotency is NOT enforced by a permanent unique index on (user_id,
idempotency_key): a Postgres index predicate must be immutable (can't reference
`now()`), and the same key must remain reusable once its window has passed - a
permanent unique index would block that legitimate reuse. So this repository only
does a plain indexed lookup; the race-safety + window semantics live in
`app.services.jobs_service.JobService.submit`, which wraps
`take_idempotency_lock` + `find_recent` + `insert` in ONE transaction via
`pg_advisory_xact_lock`.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb


class JobRepository:
    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur

    def take_idempotency_lock(self, user_id: UUID | str, idempotency_key: str) -> None:
        """Serializes concurrent submissions sharing the same (user, key) so two
        simultaneous duplicate requests can't both 'win' the find-or-insert race.
        Transaction-scoped: released automatically at commit/rollback."""
        self.cur.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s || ':' || %s))",
            (str(user_id), idempotency_key),
        )

    def find_recent(
        self, user_id: UUID | str, idempotency_key: str, window_hours: int
    ) -> dict[str, Any] | None:
        self.cur.execute(
            """
            SELECT id, status, submitted_at
            FROM jobs
            WHERE user_id = %s AND idempotency_key = %s
              AND submitted_at > now() - make_interval(hours => %s)
            ORDER BY submitted_at DESC
            LIMIT 1
            """,
            (str(user_id), idempotency_key, window_hours),
        )
        return self.cur.fetchone()

    def insert(
        self,
        *,
        job_id: UUID,
        user_id: UUID,
        kind: str,
        idempotency_key: str | None,
        request_id: str | None,
    ) -> None:
        self.cur.execute(
            """
            INSERT INTO jobs (id, user_id, kind, status, idempotency_key, request_id)
            VALUES (%s, %s, %s, 'queued', %s, %s)
            """,
            (str(job_id), str(user_id), kind, idempotency_key, request_id),
        )

    def get_for_user(self, job_id: UUID | str, user_id: UUID | str) -> dict[str, Any] | None:
        """Ownership is enforced HERE (filtered in the WHERE clause), not as an
        app-level check after a generic fetch - so a job belonging to someone else
        is indistinguishable from one that doesn't exist."""
        self.cur.execute(
            """
            SELECT id, kind, status, submitted_at, started_at, finished_at, result, error
            FROM jobs
            WHERE id = %s AND user_id = %s
            """,
            (str(job_id), str(user_id)),
        )
        return self.cur.fetchone()

    def mark_running(self, job_id: UUID | str) -> None:
        self.cur.execute(
            "UPDATE jobs SET status = 'running', started_at = now() WHERE id = %s",
            (str(job_id),),
        )

    def mark_succeeded(self, job_id: UUID | str, result: dict[str, Any]) -> None:
        self.cur.execute(
            "UPDATE jobs SET status = 'succeeded', result = %s, finished_at = now() WHERE id = %s",
            (Jsonb(result), str(job_id)),
        )

    def mark_failed(self, job_id: UUID | str, error: dict[str, Any]) -> None:
        self.cur.execute(
            "UPDATE jobs SET status = 'failed', error = %s, finished_at = now() WHERE id = %s",
            (Jsonb(error), str(job_id)),
        )

    def mark_dead_letter(self, job_id: UUID | str, error: dict[str, Any]) -> None:
        self.cur.execute(
            "UPDATE jobs SET status = 'dead_letter', error = %s, finished_at = now() WHERE id = %s",
            (Jsonb(error), str(job_id)),
        )

    def record_retry_error(self, job_id: UUID | str, error: dict[str, Any]) -> None:
        """Transient failure with retries remaining: record what happened and flip
        back to 'queued' (from 'running') so the row reflects reality while arq's
        own retry/backoff (see workers/jobs.py) reschedules the attempt."""
        self.cur.execute(
            "UPDATE jobs SET status = 'queued', error = %s WHERE id = %s",
            (Jsonb(error), str(job_id)),
        )
