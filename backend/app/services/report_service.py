"""PDF report generation (Wave: PDF report).

Two-tier split mirroring gee_analysis_service.py's own convention:
`ReportService` owns the synchronous DB/permission validation and job
dispatch (`get_options`/`enqueue_generate`, called from the request path);
`generate_report_pdf_bytes` is the one function that does the actual slow
work (per-analysis GEE tile fetch + map/chart rendering + PDF assembly) - it
takes plain DB/GEE inputs and returns bytes, with no job-table/Storage
concerns mixed in, so `run_generate_report_job` (app/workers/report_jobs.py)
can call it and separately handle persisting the result.

Async by design, not by default: generating N sections each needs its OWN
fresh GEE tile fetch (`_compute_cached` - map thumbnails aren't reusable
across analyses and the stored `tile_url_template` from a past compute is
almost certainly expired by report time, see report_map_image.py's own
docstring) on top of chart rendering and PDF assembly - the same "several GEE
round trips + real render work, well past a normal request/response" shape
that already justified async for the vegetation indices (see
gee_analysis_service.py's own module docstring). Measured end-to-end timing
recorded in this wave's test/verification notes, not assumed."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.db import Database
from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.domain import analysis_config
from app.domain.analysis_catalog import CATALOG, get_catalog_entry
from app.domain.authz import require_project_view
from app.domain.dtos import CurrentUser, GenerateReportRequest, ReportAnalysisOption, ReportOptions
from app.repositories.analysis_results import AnalysisResultRepository
from app.repositories.forest_definition import ForestDefinitionRepository
from app.repositories.projects import ProjectRepository
from app.services.gee_analysis_service import _compute_cached
from app.services.jobs_service import JobService
from app.services.report_charts import render_trend_chart_png
from app.services.report_content import build_section_content, is_multi_year_index
from app.services.report_map_image import render_boundary_map_png
from app.services.report_pdf import build_report_pdf
from app.workers.queue import TaskRunner

log = get_logger("dmrv.report")


class ReportService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get_options(self, project_id: UUID, user: CurrentUser) -> ReportOptions:
        with self.db.connection() as conn, conn.cursor() as cur:
            require_project_view(cur, project_id, user)
            project = ProjectRepository(cur).get(project_id)
            computed_at_by_id = AnalysisResultRepository(cur).list_for_project(project_id)
        assert project is not None  # require_project_view already 404s otherwise  # noqa: S101
        analyses = [
            ReportAnalysisOption(
                analysis_id=entry["id"], name=entry["name"], category=entry["category"],
                computed_at=computed_at_by_id[entry["id"]],
                is_multi_year=is_multi_year_index(entry["id"]),
            )
            for entry in CATALOG
            if computed_at_by_id.get(entry["id"]) is not None
        ]
        return ReportOptions(project_id=project_id, project_name=project["name"], analyses=analyses)

    def _prepare_generate(
        self, project_id: UUID, body: GenerateReportRequest, user: CurrentUser
    ) -> tuple[str, dict[str, Any]]:
        """Synchronous validation, same convention as gee_analysis_service.py's
        own `_prepare_refresh`: a bad request (an analysis this project hasn't
        actually computed, no boundary) fails immediately with a normal HTTP
        error, never surfaced later inside a job with no one watching."""
        with self.db.connection() as conn, conn.cursor() as cur:
            require_project_view(cur, project_id, user)
            project = ProjectRepository(cur).get(project_id)
            computed_at_by_id = AnalysisResultRepository(cur).list_for_project(project_id)
            boundary_geojson = AnalysisResultRepository(cur).get_project_boundary_geojson(
                project_id
            )
        assert project is not None  # require_project_view already 404s otherwise  # noqa: S101
        not_computed = [a for a in body.analysis_ids if computed_at_by_id.get(a) is None]
        if not_computed:
            raise ValidationError(
                f"These analyses have not been computed for this project yet: "
                f"{', '.join(not_computed)}."
            )
        if boundary_geojson is None:
            raise ValidationError(
                "This project has no Boundary layer yet - upload one before generating a report."
            )
        return project["name"], boundary_geojson

    async def enqueue_generate(
        self,
        project_id: UUID,
        body: GenerateReportRequest,
        user: CurrentUser,
        jobs: JobService,
        runner: TaskRunner,
    ) -> UUID:
        project_name, boundary_geojson = self._prepare_generate(project_id, body, user)
        # Deferred import: app.workers.report_jobs imports from this module,
        # a top-level import here would be circular (same reasoning as
        # gee_analysis_service.py's own enqueue_refresh).
        from app.workers.report_jobs import run_generate_report_job

        job_id, is_new = jobs.submit(
            user_id=user.user_id, kind="generate_report", idempotency_key=None, request_id=None,
        )
        if is_new:
            try:
                await runner.run(
                    run_generate_report_job,
                    job_id=str(job_id), project_id=str(project_id), project_name=project_name,
                    analysis_ids=list(body.analysis_ids), boundary_geojson=boundary_geojson,
                    actor=user.model_dump(mode="json"),
                )
            except Exception as e:
                jobs.mark_enqueue_failed(job_id, str(e))
                raise ValidationError(
                    "Failed to enqueue report generation. Please retry."
                ) from e
        return job_id


def generate_report_pdf_bytes(
    db: Database,
    project_id: str,
    project_name: str,
    analysis_ids: list[str],
    boundary_geojson: dict[str, Any],
) -> bytes:
    """The one function that does the actual slow work - called only from
    the `generate_report` job body (app/workers/report_jobs.py), never from
    a request handler. All CONTENT (summary/stats/methodology text) comes
    from the durable `analysis_result` row via `get_result`-equivalent reads
    below - never recomputed, so the report always describes exactly what
    the UI showed at computed_at, not a silently different re-run. Only the
    MAP IMAGE is freshly fetched per section (`_compute_cached`) - a stored
    tile_url_template is a GEE token that has likely already expired by
    report time (see report_map_image.py); a single section's map-tile
    fetch failing is logged and skipped, never sinks the whole report.

    `_compute_cached` is called with the SAME resolved params that produced
    `row["stats"]` (decoded from `row["params_key"]`, Wave: analysis config
    and methodology) - not a bare no-params call - so the freshly-fetched
    map tile matches the year/season/source the text/chart above it
    describe, not whatever the CURRENT default happens to be. A
    `legacy_full_range` row (pre-this-wave, migrated in place) decodes to
    `None`; its map tile falls back to the new default variant rather than
    the old full range - an accepted, honest degradation for old data, not
    a crash (legacy rows are transient and get superseded by fresh runs)."""
    with db.connection() as conn, conn.cursor() as cur:
        canopy_cover_pct = float(ForestDefinitionRepository(cur).get()["canopy_cover_pct"])
        rows = {
            aid: AnalysisResultRepository(cur).get(project_id, aid) for aid in analysis_ids
        }

    sections = []
    map_images: dict[str, bytes] = {}
    chart_images: dict[str, bytes] = {}
    for analysis_id in analysis_ids:
        row = rows[analysis_id]
        if row is None:  # validated at request time; a defensive guard, not expected
            continue
        entry = get_catalog_entry(analysis_id)
        assert entry is not None  # noqa: S101 - row exists only for a real catalog id
        section = build_section_content(
            entry, analysis_id, row["computed_at"], row["stats"], row["legend"]
        )
        sections.append(section)

        if section.series:
            chart_images[analysis_id] = render_trend_chart_png(section.name, section.series)

        try:
            resolved_params = analysis_config.decode_params_key(row["params_key"])
            _, _, tile_url_template = _compute_cached(
                project_id, analysis_id, boundary_geojson, canopy_cover_pct, resolved_params
            )
            if tile_url_template:
                map_images[analysis_id] = render_boundary_map_png(
                    tile_url_template, boundary_geojson
                )
        except Exception as e:  # noqa: BLE001 - one section's map must not sink the report
            log.warning(
                "report.map_image_failed", analysis_id=analysis_id, project_id=project_id,
                error=str(e),
            )

    return build_report_pdf(
        project_name=project_name, project_id=project_id, generated_at=datetime.now(UTC),
        sections=sections, map_images=map_images, chart_images=chart_images,
    )
