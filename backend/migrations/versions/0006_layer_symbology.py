"""spatial_layer.band_count + class_legend (Phase 3 Wave F - symbology)

Revision ID: 0006_layer_symbology
Revises: 0005_dataset_type_satellite
Create Date: 2026-07-19

Two additive, nullable columns on the existing `spatial_layer` row:

`band_count` - so the frontend's band-to-channel dropdowns can be populated
from the layer's REAL band count without a separate file read on every page
load. Backfilled at query time (ProjectService.get_layers) for pre-existing
rows where it's NULL, by opening the COG once - self-healing, no backfill
migration needed.

`class_legend` - the class_legend a user supplied at upload time was
previously used only transiently (compute_stats/render_preview) and never
persisted (see tile_renderer.py's old "Known gap" note: tiles couldn't
reproduce a custom legend's colors because there was nowhere to read one back
from). Persisting it here is what makes per-class color override possible at
all, and lets tile rendering use the REAL legend instead of the generic
default palette.
"""
from __future__ import annotations

from alembic import op

revision = "0006_layer_symbology"
down_revision = "0005_dataset_type_satellite"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE spatial_layer ADD COLUMN band_count INTEGER;")
    op.execute("ALTER TABLE spatial_layer ADD COLUMN class_legend JSONB;")


def downgrade() -> None:
    op.execute("ALTER TABLE spatial_layer DROP COLUMN IF EXISTS band_count;")
    op.execute("ALTER TABLE spatial_layer DROP COLUMN IF EXISTS class_legend;")
