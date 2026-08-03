"""dataset.is_reference + deleted_by (Wave: Reference Layer Library)

Revision ID: 0011_reference_layers
Revises: 0010_multi_format_layers
Create Date: 2026-07-31

A reference layer is a normal `dataset`/`spatial_layer` row (raster, vector,
or external_wms/wfs - the exact same four shapes 0010 introduced) - it goes
through the EXACT same ingestion/WMS pipeline as any project-scoped layer,
just flagged `is_reference` and attached to one shared, auto-created project
(see app.services.project_access.resolve_reference_library_project) instead
of a user-named one. `is_reference` is the ONLY new concept: every reader
that already joins `spatial_layer` to `dataset` (LayerRepository.
list_for_project) just widens its WHERE clause to also include
`is_reference = true` rows regardless of the requested project_id - no new
table, no parallel "reference layer" model to keep in sync with the real one.

`deleted_by` mirrors `project.deleted_by` (0003) - removing a reference layer
soft-deletes its `dataset` row via the same `deleted_at` column every layer
listing already filters on, attributed the same way project deletion is.
"""
from __future__ import annotations

from alembic import op

revision = "0011_reference_layers"
down_revision = "0010_multi_format_layers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE dataset ADD COLUMN is_reference BOOLEAN NOT NULL DEFAULT false;")
    op.execute(
        "ALTER TABLE dataset ADD COLUMN deleted_by UUID "
        "REFERENCES app_user(user_id) ON DELETE SET NULL;"
    )
    # Partial - only ever a handful of reference layers relative to total
    # datasets, and this is checked on EVERY project's layer listing.
    op.execute(
        "CREATE INDEX idx_dataset_is_reference ON dataset (is_reference) "
        "WHERE is_reference = true AND deleted_at IS NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_dataset_is_reference;")
    op.execute("ALTER TABLE dataset DROP COLUMN IF EXISTS deleted_by;")
    op.execute("ALTER TABLE dataset DROP COLUMN IF EXISTS is_reference;")
