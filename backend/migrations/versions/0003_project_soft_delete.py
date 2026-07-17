"""project soft-delete attribution

Revision ID: 0003_project_soft_delete
Revises: 0002_jobs
Create Date: 2026-07-17

`project.deleted_at` and `project.version` already exist from 0001_initial (the
soft-delete/optimistic-lock columns were provisioned there for every core table,
but no endpoint used them for `project` until now). The one thing genuinely
missing is WHO deleted it - `deleted_by`, mirroring `audit_log.actor_id`'s
FK-to-app_user, ON DELETE SET NULL so a later account deletion never blocks or
rewrites project history.
"""
from __future__ import annotations

from alembic import op

revision = "0003_project_soft_delete"
down_revision = "0002_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE project ADD COLUMN deleted_by UUID "
        "REFERENCES app_user(user_id) ON DELETE SET NULL;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE project DROP COLUMN IF EXISTS deleted_by;")
