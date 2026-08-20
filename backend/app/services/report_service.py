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

from dataclasses import replace
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
from app.domain.enums import ReportFormat, ReportType
from app.repositories.analysis_results import AnalysisResultRepository
from app.repositories.forest_definition import ForestDefinitionRepository
from app.repositories.projects import ProjectRepository
from app.services.ai_narrative import GEMINI_MODEL, generate_ai_summaries
from app.services.gee_analysis_service import _compute_cached
from app.services.jobs_service import JobService
from app.services.report_charts import render_trend_chart_png
from app.services.report_content import ReportSection, build_section_content, is_multi_year_index
from app.services.report_html import build_report_html
from app.services.report_map_image import render_boundary_map_png
from app.services.report_pdf import AI_NARRATIVE_DISCLOSURE_TEMPLATE, build_report_pdf
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
        return ReportOptions(
            project_id=project_id, project_name=project["name"], analyses=analyses,
            ai_narrative_disclosure=AI_NARRATIVE_DISCLOSURE_TEMPLATE.format(model=GEMINI_MODEL),
        )

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
                    report_type=body.report_type.value, output_format=body.output_format.value,
                    actor=user.model_dump(mode="json"),
                )
            except Exception as e:
                jobs.mark_enqueue_failed(job_id, str(e))
                raise ValidationError(
                    "Failed to enqueue report generation. Please retry."
                ) from e
        return job_id


def _assemble_sections(
    db: Database,
    project_id: str,
    project_name: str,
    analysis_ids: list[str],
    boundary_geojson: dict[str, Any],
    report_type: str = ReportType.SYSTEM,
) -> tuple[list[ReportSection], dict[str, bytes], dict[str, bytes], str | None]:
    """The one place that does the actual slow work of assembling a report's
    CONTENT - called only from `generate_report_pdf_bytes`/
    `generate_report_html_bytes` below (Wave: HTML report rendering split
    this out of what used to be `generate_report_pdf_bytes` itself, so both
    output formats share identical sections/images/ai_model rather than each
    re-deriving them). All CONTENT (summary/stats/methodology text) comes
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
    a crash (legacy rows are transient and get superseded by fresh runs).

    `report_type="ai"` (Wave: ai-report-narrative, Phase 3; narrative fields
    extended to 5 keys per section in the 11-section report restructure):
    maps/charts/stats/methodology/carbon-relevance/data-quality/limitations
    are built EXACTLY as above, identical to `report_type="system"` - the
    only difference is that each section's `narrative` dict is overwritten
    (via `dataclasses.replace`, merged over the deterministic wording
    `build_section_content` already put there - `build_section_content`
    itself is never told about `report_type`) from a SINGLE batched
    `generate_ai_summaries` call across every section. `AiNarrativeError` from
    that call is NOT caught here; it propagates to `run_generate_report_job`,
    which dead-letters the job immediately rather than substituting a
    partial/system report for it (see that module's own comment for why this
    is not retried)."""
    with db.connection() as conn, conn.cursor() as cur:
        canopy_cover_pct = float(ForestDefinitionRepository(cur).get()["canopy_cover_pct"])
        rows = {
            aid: AnalysisResultRepository(cur).get(project_id, aid) for aid in analysis_ids
        }

    sections = []
    ai_inputs: list[tuple[str, str, str, dict[str, Any]]] = []
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
        ai_inputs.append((analysis_id, entry["name"], entry["category"], row["stats"]))

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

    ai_model: str | None = None
    if report_type == ReportType.AI:
        # One batched call for the whole report (never per-section) - see
        # ai_narrative.generate_ai_summaries' own FAILURE SEMANTICS: raises
        # AiNarrativeError rather than returning a partial dict, and that
        # exception is deliberately left to propagate out of this function.
        ai_summaries = generate_ai_summaries(ai_inputs, project_name=project_name)
        sections = [
            replace(s, narrative={**s.narrative, **ai_summaries[s.analysis_id]}) for s in sections
        ]
        # R2: the disclosure names the model that actually ran. A fixed
        # constant here, never from ai_summaries/section content, so nothing
        # in the narrative path can spoof it.
        ai_model = GEMINI_MODEL

    return sections, map_images, chart_images, ai_model


def generate_report_pdf_bytes(
    db: Database,
    project_id: str,
    project_name: str,
    analysis_ids: list[str],
    boundary_geojson: dict[str, Any],
    report_type: str = ReportType.SYSTEM,
) -> bytes:
    """Thin wrapper around `_assemble_sections` + `report_pdf.build_report_pdf`
    - kept with this EXACT name/signature (existing tests reference it
    directly and monkeypatch `report_service.generate_ai_summaries`) even
    though it no longer does the assembly work itself (see
    `_assemble_sections`'s own docstring, Wave: HTML report rendering)."""
    sections, map_images, chart_images, ai_model = _assemble_sections(
        db, project_id, project_name, analysis_ids, boundary_geojson, report_type=report_type,
    )
    return build_report_pdf(
        project_name=project_name, project_id=project_id, generated_at=datetime.now(UTC),
        sections=sections, map_images=map_images, chart_images=chart_images,
        report_type=report_type, ai_model=ai_model,
    )


def generate_report_html_bytes(
    db: Database,
    project_id: str,
    project_name: str,
    analysis_ids: list[str],
    boundary_geojson: dict[str, Any],
    report_type: str = ReportType.SYSTEM,
) -> bytes:
    """Wave: HTML report rendering. The HTML sibling of
    `generate_report_pdf_bytes` above - identical assembly
    (`_assemble_sections`), identical sections/images/ai_model, laid out via
    `report_html.build_report_html` instead of `report_pdf.build_report_pdf`."""
    sections, map_images, chart_images, ai_model = _assemble_sections(
        db, project_id, project_name, analysis_ids, boundary_geojson, report_type=report_type,
    )
    return build_report_html(
        project_name=project_name, project_id=project_id, generated_at=datetime.now(UTC),
        sections=sections, map_images=map_images, chart_images=chart_images,
        report_type=report_type, ai_model=ai_model,
    )


def generate_report_bytes(
    db: Database,
    project_id: str,
    project_name: str,
    analysis_ids: list[str],
    boundary_geojson: dict[str, Any],
    report_type: str = ReportType.SYSTEM,
    output_format: str = ReportFormat.PDF,
) -> bytes:
    """Wave: HTML report rendering. The ONE place the PDF/HTML output-format
    switch happens (see `app.domain.enums.ReportFormat`'s own docstring for
    why this is orthogonal to `report_type`) - called from
    `run_generate_report_job` instead of either format-specific function
    directly, so a new output format only ever needs a new branch here."""
    if output_format == ReportFormat.HTML:
        return generate_report_html_bytes(
            db, project_id, project_name, analysis_ids, boundary_geojson, report_type=report_type,
        )
    return generate_report_pdf_bytes(
        db, project_id, project_name, analysis_ids, boundary_geojson, report_type=report_type,
    )
