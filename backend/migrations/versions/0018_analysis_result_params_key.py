"""analysis_result params_key scoping (Wave: analysis config and methodology)

Revision ID: 0018_analysis_result_params_key
Revises: 0017_analysis_results
Create Date: 2026-08-10

Extends analysis_result's primary key with `params_key` so a narrower
request (a single year instead of the old hardcoded full range, a different
season/source/masking) gets its OWN stored row instead of overwriting
whatever was already there - the whole point of this wave's storage design
(see app/domain/analysis_config.py's own module docstring).

The tricky part: `params_key` can't just default every existing row to the
literal string `"default"`. `analysis_config.resolve_and_validate()` always
returns a complete, concrete dict for the 7 ids this wave makes configurable
(io_lulc/modis_lulc/ndvi/evi/savi/mndwi/nbr) - even a bare "no params at all"
request resolves to something concrete (e.g. single-latest-year) and gets
its own real JSON key, NEVER the bare word "default". So if a pre-existing
row for one of those 7 ids were backfilled to `"default"`, the very FIRST
ordinary refresh anyone runs after this ships - using the new default,
single-year behavior - would collide with and overwrite that legacy full-
range result. That is exactly the destructive overwrite this wave exists to
prevent, on day one, for every project.

Fix: only hansen_gfc/dynamic_world/esa_worldcover (which stay genuinely
parameter-free forever) and the 3 browse ids keep the bare `"default"` key -
harmless, nothing about them ever changes. The 7 newly-configurable ids'
EXISTING rows are backfilled to a distinct, reserved sentinel
(`analysis_config.LEGACY_FULL_RANGE_PARAMS_KEY`, `'legacy_full_range'`) that
`resolve_and_validate`'s output can never naturally produce - so a fresh
default run gets its own real key and can never collide with the legacy row
it must never destroy.
"""
from __future__ import annotations

from alembic import op

revision = "0018_analysis_result_params_key"
down_revision = "0017_analysis_results"
branch_labels = None
depends_on = None

_STORAGE_SCOPED_IDS = ("io_lulc", "modis_lulc", "ndvi", "evi", "savi", "mndwi", "nbr")


def upgrade() -> None:
    op.execute("ALTER TABLE analysis_result ADD COLUMN params_key TEXT NOT NULL DEFAULT 'default';")
    op.execute(
        f"""
        UPDATE analysis_result SET params_key = 'legacy_full_range'
        WHERE analysis_id IN ({",".join(f"'{a}'" for a in _STORAGE_SCOPED_IDS)});
        """
    )
    op.execute("ALTER TABLE analysis_result DROP CONSTRAINT analysis_result_pkey;")
    op.execute(
        "ALTER TABLE analysis_result ADD PRIMARY KEY (project_id, analysis_id, params_key);"
    )


def downgrade() -> None:
    # Lossy by construction: once this migration has run, a project can
    # legitimately have MULTIPLE rows for the same (project_id, analysis_id)
    # (one per configured variant). Restoring the old 2-column PK can only
    # keep one - this keeps the most-recently-computed row per pair and
    # discards the rest. Never expected to run against real data; a dev-only
    # escape hatch.
    op.execute(
        """
        DELETE FROM analysis_result a USING analysis_result b
        WHERE a.project_id = b.project_id AND a.analysis_id = b.analysis_id
          AND a.computed_at < b.computed_at;
        """
    )
    op.execute("ALTER TABLE analysis_result DROP CONSTRAINT analysis_result_pkey;")
    op.execute("ALTER TABLE analysis_result DROP COLUMN params_key;")
    op.execute("ALTER TABLE analysis_result ADD PRIMARY KEY (project_id, analysis_id);")
