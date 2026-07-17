"""spatial_layer.cog_key (Phase 3 Wave A - COG conversion + tile serving)

Revision ID: 0004_layer_cog
Revises: 0003_project_soft_delete
Create Date: 2026-07-17

Extends the EXISTING `spatial_layer` row (Phase 1's "layers" concept - see
`LayerOut`/`LayerRepository`) rather than creating a parallel table for the same
thing. `cog_key` is nullable and populated by a step in the ingest job AFTER the
layer row already exists: it starts NULL (COG conversion hasn't run / is still
running / failed) and is set once a Cloud-Optimized GeoTIFF has actually been
written through the Storage abstraction. A NULL cog_key is the signal the tile
endpoint uses to return 404 ("not ready yet") rather than serving from a
non-optimized source.
"""
from __future__ import annotations

from alembic import op

revision = "0004_layer_cog"
down_revision = "0003_project_soft_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE spatial_layer ADD COLUMN cog_key TEXT;")


def downgrade() -> None:
    op.execute("ALTER TABLE spatial_layer DROP COLUMN IF EXISTS cog_key;")
