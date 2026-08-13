"""District-scoped serving + village coverage registry (Wave: Admin Boundaries)

Revision ID: 0019_admin_boundaries
Revises: 0018_analysis_result_params_key
Create Date: 2026-08-13

Country/State/District/Block boundaries are small enough (a few thousand
features nationwide) to be a normal `is_reference` vector layer, served
whole through the existing GET /layers/{id}/geojson - no schema change
needed for those. Village is not: ~537K polygons nationwide (LGD_Villages,
see the research summary this wave is built from) makes a whole-layer
GeoJSON response impractical, so a village-kind vector layer is flagged
`requires_district_scope` and MUST be queried with a `district_lgd_code`
filter (enforced in VectorLayerService, not just convention).

`properties->>'district_lgd_code'` is the filter key every district-scoped
query uses - normalized at ingestion time to this one name regardless of
which source's own column naming (dist_lgd, dtcode11, Dist_LGD, ...)
produced it. The composite index makes "this layer's features in this
district" a fast lookup instead of a sequential scan per request.

`admin_village_registry` is the OFFICIAL LGD village list (all 664,395,
name + code + parent hierarchy, no geometry) - it exists specifically so a
village with an LGD code but no boundary polygon in LGD_Villages (~19% of
the country, per the verification pass) can still be listed and shown as
"boundary not available" in the UI instead of silently vanishing, which is
the whole reason this table has no `geom` column: `vector_feature.geom` is
NOT NULL by design (0010_multi_format_layers) and correctly can't represent
"known village, no geometry available" at all.

`admin_district_registry` is a small (~700-row) distinct-district lookup
derived from the same LGD source, used only to populate the district picker
that scopes a Village layer's queries - not a boundary source itself.
"""
from __future__ import annotations

from alembic import op

revision = "0019_admin_boundaries"
down_revision = "0018_analysis_result_params_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE spatial_layer ADD COLUMN requires_district_scope BOOLEAN "
        "NOT NULL DEFAULT false;"
    )
    op.execute(
        "CREATE INDEX idx_vector_feature_district_lgd ON vector_feature "
        "(layer_id, (properties->>'district_lgd_code'));"
    )

    op.execute(
        """
        CREATE TABLE admin_village_registry (
            village_lgd_code  BIGINT PRIMARY KEY,
            village_name      TEXT NOT NULL,
            block_lgd_code    BIGINT,
            block_name        TEXT,
            district_lgd_code BIGINT NOT NULL,
            district_name     TEXT NOT NULL,
            state_lgd_code    BIGINT NOT NULL,
            state_name        TEXT NOT NULL
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_admin_village_registry_district ON admin_village_registry "
        "(district_lgd_code);"
    )

    op.execute(
        """
        CREATE TABLE admin_district_registry (
            district_lgd_code BIGINT PRIMARY KEY,
            district_name     TEXT NOT NULL,
            state_lgd_code    BIGINT NOT NULL,
            state_name        TEXT NOT NULL
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS admin_district_registry;")
    op.execute("DROP TABLE IF EXISTS admin_village_registry;")
    op.execute("DROP INDEX IF EXISTS idx_vector_feature_district_lgd;")
    op.execute("ALTER TABLE spatial_layer DROP COLUMN IF EXISTS requires_district_scope;")
