"""dataset.display_name (rename-a-layer, Administrator-only)

Revision ID: 0013_layer_display_name
Revises: 0012_adhoc_layers
Create Date: 2026-08-04

An optional override shown instead of the raw `type`/`source` in the Layers
panel - never filtered/searched on, so no index (same reasoning as
`accuracy_score`, which also has none). NULL means "no override, fall back
to the existing type/source display" - the default for every layer today.
"""
from __future__ import annotations

from alembic import op

revision = "0013_layer_display_name"
down_revision = "0012_adhoc_layers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE dataset ADD COLUMN display_name VARCHAR(256);")


def downgrade() -> None:
    op.execute("ALTER TABLE dataset DROP COLUMN IF EXISTS display_name;")
