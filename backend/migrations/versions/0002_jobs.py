"""jobs table (Phase 2 async ingestion)

Revision ID: 0002_jobs
Revises: 0001_initial
Create Date: 2026-07-16

Backs `POST /datasets/upload` (202 + job_id) and `GET /jobs/{id}` polling. `status`
is a CHECK-constrained TEXT, mirroring `dataset.type`/`project.status` in
0001_initial (not a native Postgres ENUM), for consistency with the rest of this
schema.

Idempotency is deliberately NOT a permanent unique index on
(user_id, idempotency_key): a Postgres index predicate must be immutable (it can't
reference `now()`), and the same key must remain reusable once its window has
passed - a permanent unique index would block that legitimate reuse. So this
migration only adds a plain lookup index; the race-safety + window semantics are
enforced at the application layer inside one transaction via
`pg_advisory_xact_lock` - see `app.services.jobs_service.JobService.submit`.
"""
from __future__ import annotations

from alembic import op

revision = "0002_jobs"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE jobs (
            id              UUID PRIMARY KEY,
            user_id         UUID REFERENCES app_user(user_id) ON DELETE SET NULL,
            kind            TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'queued'
                            CHECK (status IN ('queued','running','succeeded','failed','dead_letter')),
            submitted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at      TIMESTAMPTZ,
            finished_at     TIMESTAMPTZ,
            result          JSONB,
            error           JSONB,
            idempotency_key TEXT,
            request_id      TEXT
        );
        """
    )
    op.execute("CREATE INDEX idx_jobs_user_submitted ON jobs (user_id, submitted_at DESC);")
    op.execute("CREATE INDEX idx_jobs_user_idempotency ON jobs (user_id, idempotency_key);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS jobs CASCADE;")
