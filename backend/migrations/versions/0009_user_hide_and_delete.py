"""app_user hidden_at/hidden_by (Wave: Three-tier user removal)

Revision ID: 0009_user_hide_and_delete
Revises: 0008_user_management
Create Date: 2026-07-30

Adds the middle tier between Deactivate (existing `deleted_at`/`deleted_by`)
and permanent delete (a real `DELETE FROM app_user`, no schema change needed
for that - it just removes the row).

`hidden_at`/`hidden_by` are a SEPARATE pair of columns, not a reuse of
`deleted_at`/`deleted_by` - explicit design decision (see the wave's own
question about this): a user can be deactivated-and-hidden, hidden-without-
deactivated, or deactivated-without-hidden. Collapsing these into one flag
would make "deactivated but still shows in the default list" and "hidden but
can still log in" impossible to represent at the same time. Hiding does NOT
touch `deleted_at` - it only controls default-list visibility, never login
(that's `deleted_at`'s job alone, unchanged by this wave).

Self-referencing FK, ON DELETE SET NULL - same attribution convention as
every other `*_by` column in this schema (`deleted_by`, `added_by`, etc.).
"""
from __future__ import annotations

from alembic import op

revision = "0009_user_hide_and_delete"
down_revision = "0008_user_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app_user ADD COLUMN hidden_at TIMESTAMPTZ;")
    op.execute(
        "ALTER TABLE app_user ADD COLUMN hidden_by UUID "
        "REFERENCES app_user(user_id) ON DELETE SET NULL;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE app_user DROP COLUMN IF EXISTS hidden_by;")
    op.execute("ALTER TABLE app_user DROP COLUMN IF EXISTS hidden_at;")
