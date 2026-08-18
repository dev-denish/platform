"""Block Boundaries: district-scope like Village (Wave: map perf fix)

Revision ID: 0020_block_district_scope
Revises: 0019_admin_boundaries
Create Date: 2026-08-18

0019_admin_boundaries judged Block "small enough" (a few thousand features
nationwide) to serve whole, unlike Village (~537K nationwide). Measured
reality on a live deploy: ST_AsGeoJSON of the whole Block layer (7,114
features, already ST_SimplifyPreserveTopology'd) is ~29MB of GeoJSON text -
enough on its own to make every pan/zoom janky client-side (confirmed via a
Chrome performance trace: ~75% of a 3.3s pan+zoom sequence spent in long
main-thread tasks), regardless of what else is on the map, since Block is an
`is_reference` layer attached to every project.

Block features already carry `properties->>'district_lgd_code'`
(idx_vector_feature_district_lgd from 0019 already covers this), so this is
a data-only fix: flip the existing `requires_district_scope` flag for
already-ingested Block layers to match what
scripts/ingest_admin_boundaries.py now does for new ones. No schema change.
"""
from __future__ import annotations

from alembic import op

revision = "0020_block_district_scope"
down_revision = "0019_admin_boundaries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE spatial_layer sl
        SET requires_district_scope = true
        FROM dataset d
        WHERE sl.dataset_id = d.dataset_id
          AND sl.layer_kind = 'vector'
          AND d.source = 'admin-boundaries-block-lgd'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE spatial_layer sl
        SET requires_district_scope = false
        FROM dataset d
        WHERE sl.dataset_id = d.dataset_id
          AND sl.layer_kind = 'vector'
          AND d.source = 'admin-boundaries-block-lgd'
        """
    )
