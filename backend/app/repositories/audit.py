"""Audit persistence.

Fixes a domain-critical MVP defect: the audit log recorded the literal string
"api_upload"/"seed_script" and never captured WHO performed an action - the
authenticated user was not threaded into ingestion. For an MRV/verification system
whose entire purpose is a defensible, attributable record, a non-attributable audit
trail is disqualifying. Here every entry carries the acting user's id AND username,
a target reference, and the request id for cross-correlation with the logs."""
from __future__ import annotations

from uuid import UUID

import psycopg

from app.core.logging import request_id_ctx


class AuditRepository:
    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur

    def record(
        self, *, actor_id: UUID | str | None, actor_name: str,
        action: str, target: str | None = None, detail: str | None = None,
    ) -> None:
        self.cur.execute(
            """
            INSERT INTO audit_log (actor_id, actor_name, action, target, detail, request_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                str(actor_id) if actor_id else None,
                actor_name, action, target, detail, request_id_ctx.get(),
            ),
        )
