"""layer_kind + vector_feature + external_layer_source + allowed_wms_domain
(Wave: multi-format layer support)

Revision ID: 0010_multi_format_layers
Revises: 0009_user_hide_and_delete
Create Date: 2026-07-31

Everything so far assumed `spatial_layer` was always a raster (COG-backed) row.
This wave adds three new layer shapes - vector (real geometries, not COG tiles),
and two external-service shapes (WMS/WFS, no local storage at all, fetched
live through a server-side proxy - see app/services/external_fetch.py for why
that proxy exists rather than pointing Leaflet straight at the third-party
server).

`layer_kind` is the discriminator every reader (ProjectService.get_layers,
the tile/geojson/external-layer endpoints) branches on. Existing rows all
backfill to 'raster' (the only kind that existed before this migration) -
free, since the column has a DEFAULT.

`spatial_layer.file_key` becomes nullable: a vector layer's real data lives in
`vector_feature`, not a stored file; an external layer has no stored file at
all (every fetch goes straight to the third-party server, revalidated each
time). Both kinds still get a spatial_layer row (bbox/extent/crs), so every
existing per-project "list layers" query keeps working across all four kinds
with one JOIN, unchanged.

`vector_feature` is one row per real feature (not one blob per layer) so a
GIST index over `geom` is actually usable, and lets a future spatial query
(e.g. "layers intersecting this AOI") reach into vector data the same way it
already can for raster's `spatial_layer.extent`.

`external_layer_source` holds what's needed to reissue a GetMap/GetFeature
request against the ORIGINAL third-party server - domain is stored
separately from base_url specifically so the proxy can re-check it against
`allowed_wms_domain` on every single request without re-parsing a URL.

`allowed_wms_domain` is the Administrator-managed allow-list Part B's whole
design depends on: a GIS Associate/Analyst can only ever pick a domain
already present in this table (enforced both at layer-creation time and,
critically, on EVERY proxied fetch afterward - approval is not a one-time
event, see external_fetch.py).
"""
from __future__ import annotations

from alembic import op

revision = "0010_multi_format_layers"
down_revision = "0009_user_hide_and_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE spatial_layer ADD COLUMN layer_kind TEXT NOT NULL DEFAULT 'raster' "
        "CHECK (layer_kind IN ('raster', 'vector', 'external_wms', 'external_wfs'));"
    )
    op.execute("ALTER TABLE spatial_layer ALTER COLUMN file_key DROP NOT NULL;")

    op.execute(
        """
        CREATE TABLE vector_feature (
            feature_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            layer_id   UUID NOT NULL REFERENCES spatial_layer(layer_id) ON DELETE CASCADE,
            geom       GEOMETRY(Geometry, 4326) NOT NULL,
            properties JSONB NOT NULL DEFAULT '{}'::jsonb
        );
        """
    )
    op.execute("CREATE INDEX idx_vector_feature_layer ON vector_feature (layer_id);")
    op.execute("CREATE INDEX idx_vector_feature_geom ON vector_feature USING GIST (geom);")

    op.execute(
        """
        CREATE TABLE external_layer_source (
            layer_id     UUID PRIMARY KEY REFERENCES spatial_layer(layer_id) ON DELETE CASCADE,
            domain       TEXT NOT NULL,
            base_url     TEXT NOT NULL,
            layer_name   TEXT NOT NULL,
            service_kind TEXT NOT NULL CHECK (service_kind IN ('wms', 'wfs'))
        );
        """
    )

    op.execute(
        """
        CREATE TABLE allowed_wms_domain (
            domain_id  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            domain     TEXT NOT NULL UNIQUE,
            added_by   UUID REFERENCES app_user(user_id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS allowed_wms_domain CASCADE;")
    op.execute("DROP TABLE IF EXISTS external_layer_source CASCADE;")
    op.execute("DROP TABLE IF EXISTS vector_feature CASCADE;")
    op.execute("ALTER TABLE spatial_layer DROP COLUMN IF EXISTS layer_kind;")
    # file_key intentionally left nullable on downgrade - restoring NOT NULL
    # would fail outright if any vector/external rows (which never had one)
    # still exist, and downgrading past this migration implies those rows'
    # backing tables are already gone.
