"""user_permission_grant (Wave: per-user permission grants)

Revision ID: 0015_permission_grants
Revises: 0014_audit_log_project_id
Create Date: 2026-08-06

A reusable, per-user grant system independent of role-based RBAC
(app_user.role): a person can hold zero or more named grants regardless of
their role. `permission_name` is free text here (validated against
app.domain.permissions.PERMISSION_REGISTRY at the service layer, not by a
DB CHECK) - a new grantable permission is then a registry entry, never a
migration.

`user_id` is ON DELETE CASCADE, not SET NULL like the attribution-only `*_by`
columns elsewhere in this schema - a grant IS that user's grant, it cannot
mean anything once they're gone (added to UserService's
_REVIEWED_CASCADE_FKS allow-list in the same commit, so a permanent user
delete isn't wrongly blocked by this FK).
"""
from __future__ import annotations

from alembic import op

revision = "0015_permission_grants"
down_revision = "0014_audit_log_project_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE user_permission_grant (
            user_id         UUID NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
            permission_name TEXT NOT NULL,
            granted_by      UUID REFERENCES app_user(user_id) ON DELETE SET NULL,
            granted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, permission_name)
        );
        """
    )
    op.execute("CREATE INDEX idx_user_permission_grant_user ON user_permission_grant (user_id);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_permission_grant CASCADE;")
