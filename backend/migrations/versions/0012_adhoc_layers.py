"""dataset.is_adhoc (Wave 3: Added Layers)

Revision ID: 0012_adhoc_layers
Revises: 0011_reference_layers
Create Date: 2026-08-01

An ad-hoc layer is a normal `dataset`/`spatial_layer` row, ingested through
the EXACT same pipeline as a formal upload (raster COG conversion or
vector_feature storage - see IngestionService.ingest), just flagged
`is_adhoc` and missing the formal metadata (class_legend, accuracy_score)
a real upload requires. `is_adhoc` is the only new concept, same shape as
0011's `is_reference`: every reader that already joins `spatial_layer` to
`dataset` widens its SELECT to read it, and KpiRepository.for_project /
ProjectService.get_evolution exclude it explicitly so an ad-hoc layer never
pollutes a project's official KPI/evolution numbers.
"""
from __future__ import annotations

from alembic import op

revision = "0012_adhoc_layers"
down_revision = "0011_reference_layers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE dataset ADD COLUMN is_adhoc BOOLEAN NOT NULL DEFAULT false;")
    # Partial - only a handful of ad-hoc layers relative to total datasets,
    # and KpiRepository.for_project checks this on every project KPI read.
    op.execute(
        "CREATE INDEX idx_dataset_is_adhoc ON dataset (is_adhoc) WHERE is_adhoc = true;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_dataset_is_adhoc;")
    op.execute("ALTER TABLE dataset DROP COLUMN IF EXISTS is_adhoc;")
