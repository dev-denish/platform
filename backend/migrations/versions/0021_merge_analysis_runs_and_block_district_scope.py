"""Merge heads: 0019_analysis_runs + 0020_block_district_scope

Revision ID: 0021_merge_heads
Revises: 0019_analysis_runs, 0020_block_district_scope
Create Date: 2026-08-18

Two independent feature branches each added a migration on top of the same
parent (0018_analysis_result_params_key): feature/vnv-band-indices' own
prerequisite history added 0019_analysis_runs (VNV Pipeline analysis_runs
table), while main's own line added 0019_admin_boundaries -> 0020_
block_district_scope (India Administrative Boundaries + a district-scope
fix). Neither migration's author knew about the other at the time - this
is a pure merge point, no schema change of its own, converging the two
branches back into one linear head so `alembic upgrade head` has exactly
one target again.
"""
from __future__ import annotations

revision = "0021_merge_heads"
down_revision = ("0019_analysis_runs", "0020_block_district_scope")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
