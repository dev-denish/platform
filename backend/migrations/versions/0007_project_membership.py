"""project_membership table (Wave: project-level RBAC)

Revision ID: 0007_project_membership
Revises: 0006_layer_symbology
Create Date: 2026-07-30

Per-project membership, layered on top of the existing global `app_user.role`
(which becomes the account's DEFAULT role - used for Administrator-bypass and
as a sensible prefill when someone's first added to a project, but no longer
consulted for a project-scoped permission check once a membership row exists
- see app.domain.authz).

Soft-deletable like every other core table here (removed_at/removed_by,
never a hard DELETE - see project.deleted_at/deleted_by for the identical
convention), so a user's membership HISTORY on a project survives their
removal. Uniqueness is PARTIAL - WHERE removed_at IS NULL - so someone
removed and later re-added gets a fresh row instead of colliding with their
own past membership.

'Administrator' is deliberately NOT one of the CHECK constraint's allowed
values - it's a global-only concept (app_user.role), never a valid
project-level role, since an Administrator already bypasses membership
checks entirely (require_project_view returns early for them - no row
lookup at all).

No backfill: every project that exists before this migration ships with
ZERO membership rows. That is the intended state, not a defect to notice -
this wave's own writeup is explicit that only Administrators can see any
pre-existing project until people are explicitly added as members.
"""
from __future__ import annotations

from alembic import op

revision = "0007_project_membership"
down_revision = "0006_layer_symbology"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE project_membership (
            membership_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            project_id UUID NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
            user_id    UUID NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
            role       TEXT NOT NULL
                       CHECK (role IN ('GIS Associate','Analyst','Verifier','Viewer')),
            added_by   UUID REFERENCES app_user(user_id) ON DELETE SET NULL,
            added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            removed_at TIMESTAMPTZ,
            removed_by UUID REFERENCES app_user(user_id) ON DELETE SET NULL
        );
        """
    )
    # Soft-deletable uniqueness: the same partial-index pattern as project's own
    # uq_project_name_lower - one LIVE membership per (project, user) at a time,
    # any number of historical (removed) rows.
    op.execute(
        "CREATE UNIQUE INDEX uq_project_membership_active "
        "ON project_membership (project_id, user_id) WHERE removed_at IS NULL;"
    )
    op.execute(
        "CREATE INDEX idx_project_membership_user "
        "ON project_membership (user_id) WHERE removed_at IS NULL;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS project_membership CASCADE;")
