"""audit_log.project_id (per-project recent activity feed)

Revision ID: 0014_audit_log_project_id
Revises: 0013_layer_display_name
Create Date: 2026-08-04

Nullable, backfill-free: every write-site that already has a project_id in
scope (layer/legend/rename/ingest/membership/project-delete/external-layer-
creation actions) is updated to pass it; the few genuinely global actions
(WMS domain allow-list, user management, login/password-change) leave it
NULL on purpose - they aren't "this project's" activity. Historical rows
before this migration are also NULL and simply won't appear in a
project-scoped feed - a disclosed gap, not a bug.
"""
from __future__ import annotations

from alembic import op

revision = "0014_audit_log_project_id"
down_revision = "0013_layer_display_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE audit_log ADD COLUMN project_id UUID "
        "REFERENCES project(project_id) ON DELETE SET NULL;"
    )
    op.execute("CREATE INDEX idx_audit_log_project_id ON audit_log (project_id, created_at DESC);")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_audit_log_project_id;")
    op.execute("ALTER TABLE audit_log DROP COLUMN IF EXISTS project_id;")
