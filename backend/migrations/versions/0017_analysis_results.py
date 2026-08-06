"""analysis_result cache table (Wave: GEE analysis registry)

Revision ID: 0017_analysis_results
Revises: 0016_forest_definition
Create Date: 2026-08-06

Caches each project+analysis's last computed GEE result so viewing the
Analysis panel never re-queries GEE - only an explicit refresh does. One row
per (project_id, analysis_id), upserted on refresh; `computed_at` is what
the UI shows as "last computed" and what a stale-cache warning would key off
of later, if ever added. `computed_by` is attribution only (SET NULL on
delete, same convention as added_by/deleted_by elsewhere) - the row itself
is meaningless without the project, so project_id is CASCADE (an analysis
result IS that project's cached result, same reasoning as project_membership
in app/services/user_service.py's _REVIEWED_CASCADE_FKS - moot in practice
since ProjectService only ever soft-deletes, never a physical DELETE).
"""
from __future__ import annotations

from alembic import op

revision = "0017_analysis_results"
down_revision = "0016_forest_definition"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE analysis_result (
            project_id        UUID NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
            analysis_id       TEXT NOT NULL,
            computed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            computed_by       UUID REFERENCES app_user(user_id) ON DELETE SET NULL,
            stats             JSONB NOT NULL,
            legend            JSONB,
            tile_url_template TEXT,
            PRIMARY KEY (project_id, analysis_id)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS analysis_result CASCADE;")
