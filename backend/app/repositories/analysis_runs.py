"""`analysis_runs` persistence (Wave: VNV Pipeline NDFI go-live).

Per-run tracking for the VNV Pipeline's own async compute path - see
migrations/versions/0019_analysis_runs.py for the full reasoning on why
this is a separate table from both `jobs` (generic job-queue row, every
async kind) and `analysis_result` (the existing pan-source "current cached
result" table). Cursor-based, plain SQL - mirrors the style of
app/repositories/jobs.py / app/repositories/analysis_results.py; no ORM.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb


class AnalysisRunRepository:
    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur

    def insert(
        self,
        *,
        run_id: UUID,
        project_id: UUID | str,
        analysis_type: str,
        status: str = "queued",
    ) -> None:
        self.cur.execute(
            """
            INSERT INTO analysis_runs (id, project_id, analysis_type, status)
            VALUES (%s, %s, %s, %s)
            """,
            (str(run_id), str(project_id), analysis_type, status),
        )

    def mark_running(self, run_id: UUID | str) -> None:
        self.cur.execute(
            "UPDATE analysis_runs SET status = 'running' WHERE id = %s",
            (str(run_id),),
        )

    def set_input_raster_ref(self, run_id: UUID | str, input_raster_ref: str) -> None:
        self.cur.execute(
            "UPDATE analysis_runs SET input_raster_ref = %s WHERE id = %s",
            (input_raster_ref, str(run_id)),
        )

    def mark_done(
        self,
        run_id: UUID | str,
        *,
        output_raster_ref: str,
        stats: dict[str, Any],
    ) -> None:
        self.cur.execute(
            """
            UPDATE analysis_runs
            SET status = 'done', output_raster_ref = %s, stats = %s, completed_at = now()
            WHERE id = %s
            """,
            (output_raster_ref, Jsonb(stats), str(run_id)),
        )

    def mark_failed(self, run_id: UUID | str, error_message: str) -> None:
        self.cur.execute(
            """
            UPDATE analysis_runs
            SET status = 'failed', error_message = %s, completed_at = now()
            WHERE id = %s
            """,
            (error_message, str(run_id)),
        )

    def get(self, run_id: UUID | str) -> dict[str, Any] | None:
        self.cur.execute(
            """
            SELECT id, project_id, analysis_type, input_raster_ref, output_raster_ref,
                   status, stats, error_message, created_at, completed_at
            FROM analysis_runs
            WHERE id = %s
            """,
            (str(run_id),),
        )
        return self.cur.fetchone()
