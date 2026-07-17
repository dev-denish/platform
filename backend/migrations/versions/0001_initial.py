"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-15

Replaces the MVP's raw `schema.sql` (CREATE TABLE IF NOT EXISTS, no migration
history, no rollback). This is the versioned baseline. Beyond the MVP schema it adds,
with rationale in the review:
  * indexes on every FK/join/order column (project.name lower(), dataset.project_id,
    dataset.loaded_at, kpi.dataset_id, spatial_layer.dataset_id) - the MVP had none,
    so joins sequentially scanned at scale;
  * UNIQUE(kpi.dataset_id, metric_name) - stops KPI duplication + /summary double count;
  * UNIQUE lower(project.name) - makes find-or-create atomic (no race);
  * TIMESTAMPTZ everywhere - the MVP used naive TIMESTAMP (bad for a cross-region
    audit trail);
  * soft-delete (deleted_at) + optimistic-lock (version) columns;
  * attributable audit_log (actor_id FK + actor_name + target + request_id);
  * CHECK constraints mirroring the domain enums;
  * a refresh-token revocation table (used by JWT rotation).
"""
from __future__ import annotations

from alembic import op

# revision identifiers
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')

    op.execute(
        """
        CREATE TABLE app_user (
            user_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'GIS Associate'
                          CHECK (role IN ('Administrator','GIS Associate','Analyst','Verifier','Viewer')),
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at    TIMESTAMPTZ
        );
        """
    )

    op.execute(
        """
        CREATE TABLE project (
            project_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            name       TEXT NOT NULL,
            region     TEXT,
            start_date DATE,
            status     TEXT NOT NULL DEFAULT 'Active'
                       CHECK (status IN ('Active','Archived','Under Review')),
            version    INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at TIMESTAMPTZ
        );
        """
    )
    # Atomic find-or-create + case-insensitive uniqueness.
    op.execute("CREATE UNIQUE INDEX uq_project_name_lower ON project (lower(name)) WHERE deleted_at IS NULL;")

    op.execute(
        """
        CREATE TABLE dataset (
            dataset_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            project_id     UUID NOT NULL REFERENCES project(project_id) ON DELETE RESTRICT,
            type           TEXT NOT NULL CHECK (type IN ('LULC','NDVI','Biomass','Boundary')),
            source         TEXT,
            accuracy_score NUMERIC(5,2) CHECK (accuracy_score >= 0 AND accuracy_score <= 100),
            date_processed DATE,
            batch_id       UUID NOT NULL,
            loaded_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at     TIMESTAMPTZ
        );
        """
    )
    op.execute("CREATE INDEX idx_dataset_project ON dataset (project_id) WHERE deleted_at IS NULL;")
    op.execute("CREATE INDEX idx_dataset_loaded_at ON dataset (project_id, loaded_at DESC);")
    op.execute("CREATE INDEX idx_dataset_batch ON dataset (batch_id);")

    op.execute(
        """
        CREATE TABLE spatial_layer (
            layer_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            dataset_id   UUID NOT NULL REFERENCES dataset(dataset_id) ON DELETE CASCADE,
            file_key     TEXT NOT NULL,
            preview_key  TEXT,
            crs          TEXT NOT NULL,
            bbox_minx    DOUBLE PRECISION,
            bbox_miny    DOUBLE PRECISION,
            bbox_maxx    DOUBLE PRECISION,
            bbox_maxy    DOUBLE PRECISION,
            pixel_size_m NUMERIC(8,2),
            extent       GEOMETRY(Polygon, 4326)
        );
        """
    )
    op.execute("CREATE INDEX idx_spatial_layer_dataset ON spatial_layer (dataset_id);")
    op.execute("CREATE INDEX idx_spatial_layer_extent ON spatial_layer USING GIST (extent);")

    op.execute(
        """
        CREATE TABLE kpi (
            kpi_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            dataset_id  UUID NOT NULL REFERENCES dataset(dataset_id) ON DELETE CASCADE,
            metric_name TEXT NOT NULL,
            value       NUMERIC(18,4) NOT NULL,
            unit        TEXT,
            computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (dataset_id, metric_name)
        );
        """
    )
    op.execute("CREATE INDEX idx_kpi_dataset ON kpi (dataset_id);")
    op.execute("CREATE INDEX idx_kpi_metric ON kpi (metric_name);")

    op.execute(
        """
        CREATE TABLE audit_log (
            log_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            actor_id   UUID REFERENCES app_user(user_id) ON DELETE SET NULL,
            actor_name TEXT NOT NULL,
            action     TEXT NOT NULL,
            target     TEXT,
            detail     TEXT,
            request_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX idx_audit_created_at ON audit_log (created_at DESC);")
    op.execute("CREATE INDEX idx_audit_actor ON audit_log (actor_id);")

    op.execute(
        """
        CREATE TABLE revoked_token (
            jti        UUID PRIMARY KEY,
            user_id    UUID REFERENCES app_user(user_id) ON DELETE CASCADE,
            revoked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL
        );
        """
    )


def downgrade() -> None:
    for tbl in ("revoked_token", "audit_log", "kpi", "spatial_layer", "dataset", "project", "app_user"):
        op.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE;")
