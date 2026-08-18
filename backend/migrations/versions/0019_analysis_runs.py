"""analysis_runs table (Wave: VNV Pipeline NDFI go-live)

Revision ID: 0019_analysis_runs
Revises: 0018_analysis_result_params_key
Create Date: 2026-08-14

Per-run tracking for the VNV Pipeline's own async compute path (CDSE
Sentinel-2 ingestion -> ForesToolboxRS NDFI sidecar, see
app/services/vnv_analysis_service.py / app/workers/vnv_analysis_jobs.py),
distinct from BOTH tables that already exist:

  - `jobs` (0002_jobs) is the generic job-queue row every async kind
    (ingest_dataset, compute_gee_analysis, generate_report, and now
    compute_vnv_ndfi) already reports through - polled via GET /jobs/{id}.
    It stays exactly that: kind-agnostic, no raster/stats detail.
  - `analysis_result` (0017_analysis_results / 0018_analysis_result_params_
    key) is the existing pan-source "current cached result for this
    project+analysis_id" table every GET /projects/{id}/analyses/
    {analysis_id} reads from, regardless of whether GEE or the VNV
    Pipeline computed it - this migration does not touch it.

Neither of those records what THIS specific run actually touched along the
way - which CDSE-fetched raster fed it, which sidecar output raster it
produced, or why a specific attempt failed (a CDSE auth/search error vs. an
unreachable sidecar vs. a bad sidecar response are all distinguishable
here, where `jobs.error` only ever carries the same generic
`{"code": "job_error", ...}` shape every kind uses). `analysis_type` is
intentionally a bare TEXT, not a FK into any catalog table
(app/domain/analysis_catalog.py's CATALOG is static application data, not
a DB table - see that module's own docstring) - same convention
`analysis_result.analysis_id` already uses for the same reason.

`id` is an application-generated UUID with no server-side default,
matching this schema's existing convention for a surrogate-keyed async-work
row - `jobs.id` (0002_jobs) has no DEFAULT either; the job/run id is always
minted in Python (`uuid.uuid4()`) before the INSERT, never left to Postgres
to generate. No migration in this schema uses `gen_random_uuid()`.

`status` mirrors `jobs.status`'s own CHECK-constrained-TEXT convention (not
a native Postgres ENUM), narrowed to this table's own 4 real states -
`dead_letter` doesn't apply here: VNVAnalysisService.enqueue_refresh writes
a terminal `failed` row directly on an enqueue failure, and
run_vnv_ndfi_analysis writes `failed` on any compute-step exception: there
is no separate run-level retry counter to exhaust the way the generic
`jobs` row has.
"""
from __future__ import annotations

from alembic import op

revision = "0019_analysis_runs"
down_revision = "0018_analysis_result_params_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE analysis_runs (
            id                UUID PRIMARY KEY,
            project_id        UUID NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
            analysis_type     TEXT NOT NULL,
            input_raster_ref  TEXT,
            output_raster_ref TEXT,
            status            TEXT NOT NULL DEFAULT 'queued'
                              CHECK (status IN ('queued','running','done','failed')),
            stats             JSONB,
            error_message     TEXT,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at      TIMESTAMPTZ
        );
        """
    )
    op.execute("CREATE INDEX idx_analysis_runs_project_id ON analysis_runs (project_id);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS analysis_runs CASCADE;")
