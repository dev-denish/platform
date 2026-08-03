"""app_user soft-delete attribution + fix the username-revival bug (Wave: User Management)

Revision ID: 0008_user_management
Revises: 0007_project_membership
Create Date: 2026-07-30

Two changes, both needed for the new in-app "create/deactivate user" screen:

1. `app_user.deleted_by` - who deactivated this account, mirroring
   `project.deleted_by`/`project_membership.removed_by`'s identical
   attribution convention. Self-referencing FK (an Administrator deactivates
   another app_user row), ON DELETE SET NULL so a later account deletion
   never blocks or rewrites this history - same reasoning as every other
   `*_by` FK in this schema.

2. THE ACTUAL BUG FIX: `username` was a bare `UNIQUE` constraint
   (`app_user_username_key`, from migration 0001), not partial on
   `deleted_at IS NULL`. That meant `UserRepository.upsert()`'s
   `ON CONFLICT (username) DO UPDATE` fired for ANY row with that username -
   including a soft-deleted one - silently overwriting a dead account's
   password_hash/role while leaving `deleted_at` untouched. The account stayed
   permanently invisible to login (AuthService.login filters on
   `deleted_at IS NULL`), and the username could never be reused for a
   genuinely NEW live account either, since the conflict target matched
   regardless of deleted_at.

   Fixed the same way `project.name` and `project_membership` already handle
   "unique only while live" in this schema: drop the bare constraint, replace
   it with a PARTIAL unique index `WHERE deleted_at IS NULL`. A deactivated
   username becomes free for a brand-new account (new user_id, fresh row) -
   the old row's history (and every FK pointing at its user_id) is untouched.
   `UserRepository.upsert()`'s `ON CONFLICT` target is updated to match this
   migration in the same commit (see that file) - the two must never drift.

Downgrade note: re-adding the original bare UNIQUE constraint will FAIL if
any username now exists on more than one row (i.e. a deactivated-then-
recreated account) - by design, this is not meant to be a clean round-trip
once that's happened, same informal rigor migration 0007's own downgrade
already accepts for this codebase.
"""
from __future__ import annotations

from alembic import op

revision = "0008_user_management"
down_revision = "0007_project_membership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE app_user ADD COLUMN deleted_by UUID "
        "REFERENCES app_user(user_id) ON DELETE SET NULL;"
    )
    op.execute("ALTER TABLE app_user DROP CONSTRAINT app_user_username_key;")
    op.execute(
        "CREATE UNIQUE INDEX uq_app_user_username_live ON app_user (username) "
        "WHERE deleted_at IS NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_app_user_username_live;")
    op.execute("ALTER TABLE app_user ADD CONSTRAINT app_user_username_key UNIQUE (username);")
    op.execute("ALTER TABLE app_user DROP COLUMN IF EXISTS deleted_by;")
