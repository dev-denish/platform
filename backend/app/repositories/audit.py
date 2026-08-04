"""Audit persistence.

Fixes a domain-critical MVP defect: the audit log recorded the literal string
"api_upload"/"seed_script" and never captured WHO performed an action - the
authenticated user was not threaded into ingestion. For an MRV/verification system
whose entire purpose is a defensible, attributable record, a non-attributable audit
trail is disqualifying. Here every entry carries the acting user's id AND username,
a target reference, and the request id for cross-correlation with the logs.

`project_id` (migration 0014) is optional and passed at write time by any
call site that already has one in scope - it is NOT derived from `target`
at read time, since `target`'s meaning (layer_id/dataset_id/user_id/a bare
domain string/"project_id:user_id") varies per action and several actions
(WMS domain allow-list, user management, login) have no project at all.
See ProjectService/list_for_project for the read side."""
from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg

from app.core.logging import request_id_ctx


class AuditRepository:
    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur

    def record(
        self, *, actor_id: UUID | str | None, actor_name: str,
        action: str, target: str | None = None, detail: str | None = None,
        project_id: UUID | str | None = None,
    ) -> None:
        self.cur.execute(
            """
            INSERT INTO audit_log (actor_id, actor_name, action, target, detail,
                                    request_id, project_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(actor_id) if actor_id else None,
                actor_name, action, target, detail, request_id_ctx.get(),
                str(project_id) if project_id else None,
            ),
        )

    def list_for_project(self, project_id: UUID | str, limit: int) -> list[dict[str, Any]]:
        self.cur.execute(
            """
            SELECT actor_name, action, detail, target, created_at
            FROM audit_log
            WHERE project_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (str(project_id), limit),
        )
        return list(self.cur.fetchall())
