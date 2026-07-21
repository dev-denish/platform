"""allow 'Satellite / Raw Imagery' as a dataset type

Revision ID: 0005_dataset_type_satellite
Revises: 0004_layer_cog
Create Date: 2026-07-19

UI-only convenience type added on top of the legend-driven ingestion path from
0002/the raster stats rework: it carries no new columns or ingestion logic,
just one more value the DB's own check constraint (0001_initial) has to
accept - the app-layer DatasetType enum (app/domain/enums.py) was already
updated to match.
"""
from __future__ import annotations

from alembic import op

revision = "0005_dataset_type_satellite"
down_revision = "0004_layer_cog"
branch_labels = None
depends_on = None

_OLD = "type IN ('LULC','NDVI','Biomass','Boundary')"
_NEW = "type IN ('LULC','NDVI','Biomass','Boundary','Satellite / Raw Imagery')"


def upgrade() -> None:
    op.execute("ALTER TABLE dataset DROP CONSTRAINT dataset_type_check;")
    op.execute(f"ALTER TABLE dataset ADD CONSTRAINT dataset_type_check CHECK ({_NEW});")


def downgrade() -> None:
    op.execute("ALTER TABLE dataset DROP CONSTRAINT dataset_type_check;")
    op.execute(f"ALTER TABLE dataset ADD CONSTRAINT dataset_type_check CHECK ({_OLD});")
