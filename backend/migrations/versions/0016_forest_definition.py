"""forest_definition_setting (Wave: permission grants, Part 2)

Revision ID: 0016_forest_definition
Revises: 0015_permission_grants
Create Date: 2026-08-06

A single settings row driving the canopy-cover / min-height / min-area
thresholds used to classify "forest" across every project's reports. Seeded
with India's confirmed DNA values (15% canopy cover, 2m minimum height,
0.05ha minimum area).

Singleton enforced at the schema level, not by application convention: `id`
is a BOOLEAN primary key CHECKed to `true`, so a second row is a constraint
violation, not just an unwritten rule - there is never an id to look up or
guess, `SELECT * FROM forest_definition_setting` with no WHERE always
returns exactly the one live row.
"""
from __future__ import annotations

from alembic import op

revision = "0016_forest_definition"
down_revision = "0015_permission_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE forest_definition_setting (
            id               BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
            canopy_cover_pct NUMERIC(5,2) NOT NULL
                             CHECK (canopy_cover_pct >= 0 AND canopy_cover_pct <= 100),
            min_height_m     NUMERIC(6,2) NOT NULL CHECK (min_height_m > 0),
            min_area_ha      NUMERIC(8,4) NOT NULL CHECK (min_area_ha > 0),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by       UUID REFERENCES app_user(user_id) ON DELETE SET NULL
        );
        """
    )
    op.execute(
        """
        INSERT INTO forest_definition_setting (id, canopy_cover_pct, min_height_m, min_area_ha)
        VALUES (TRUE, 15, 2, 0.05);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS forest_definition_setting CASCADE;")
