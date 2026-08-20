"""The `generate_report` job lifecycle (Wave: PDF report). Same generic-retry
shape as workers/gee_analysis_jobs.py's `run_gee_analysis_job` - a failure
here (GEE quota/network, a boundary too large) is presumed transient, so
every exception gets the same retry-or-dead-letter treatment.

Wave: ai-report-narrative, Phase 3 - ONE deliberate carve-out from that rule:
`AiNarrativeError` (report_type="ai" only) is dead-lettered immediately,
never retried. Reasoning: `generate_section_summary` calls Gemini with
`temperature=0`, so a numeric-grounding rejection is reproducible, not
transient - retrying it burns up to `job_max_retries` more attempts at up to
`ai_narrative.TOTAL_BUDGET_S` (600s) each for a result that will not change.
A genuine Gemini-unreachable/timeout failure already got its own generous
per-call and total-batch budget inside `generate_ai_summaries` before ever
raising here, unlike a single GEE tile request. Either way the end state is
the same as any other dead-letter: a clear job-failure surfaced via
GET /jobs/{id}, never a silently-substituted system-only report."""
from __future__ import annotations

import os
import tempfile
import time
from datetime import date
from typing import Any

from arq.worker import Retry

from app.core.db import Database
from app.core.http_headers import strip_header_injection_chars
from app.core.logging import get_logger
from app.core.metrics import job_duration_seconds, jobs_completed_total
from app.domain.enums import AuditAction, ReportFormat
from app.repositories.audit import AuditRepository
from app.repositories.jobs import JobRepository
from app.services.ai_narrative import AiNarrativeError
from app.services.ingestion.storage import Storage
from app.services.report_service import generate_report_bytes

_KIND = "generate_report"


def _backoff_seconds(job_try: int) -> float:
    # ponytail: same fixed exponential backoff as run_ingest_job/
    # run_gee_analysis_job's own - give it a per-kind config knob if these
    # three ever need different pacing.
    return min(2**job_try, 60)


async def run_generate_report_job(
    ctx: dict[str, Any],
    *,
    job_id: str,
    project_id: str,
    project_name: str,
    analysis_ids: list[str],
    boundary_geojson: dict[str, Any],
    actor: dict[str, Any],
    report_type: str = "system",
    output_format: str = "pdf",
) -> None:
    db: Database = ctx["db"]
    storage: Storage = ctx["storage"]
    job_try: int = ctx.get("job_try", 1)
    settings = ctx["settings"]
    log = get_logger("dmrv.jobs").bind(job_id=job_id, kind=_KIND, job_try=job_try)

    with db.transaction() as cur:
        JobRepository(cur).mark_running(job_id)
    log.info("job.running")

    start = time.perf_counter()
    try:
        report_bytes = generate_report_bytes(
            db, project_id, project_name, analysis_ids, boundary_geojson,
            report_type=report_type, output_format=output_format,
        )
    except AiNarrativeError as e:
        # Deliberately NOT retried - see this module's own docstring.
        error = {"code": "ai_narrative_error", "message": str(e)}
        with db.transaction() as cur:
            JobRepository(cur).mark_dead_letter(job_id, error)
        jobs_completed_total.labels(kind=_KIND, status="dead_letter").inc()
        job_duration_seconds.labels(kind=_KIND).observe(time.perf_counter() - start)
        log.error("job.dead_letter", error=str(e), reason="ai_narrative_error_not_retried")
        return
    except Exception as e:  # noqa: BLE001 - presumed transient (GEE quota/network); classified below
        error = {"code": "job_error", "message": str(e)}
        max_tries = settings.job_max_retries
        if job_try >= max_tries:
            with db.transaction() as cur:
                JobRepository(cur).mark_dead_letter(job_id, error)
            jobs_completed_total.labels(kind=_KIND, status="dead_letter").inc()
            job_duration_seconds.labels(kind=_KIND).observe(time.perf_counter() - start)
            log.error("job.dead_letter", error=str(e))
            return
        with db.transaction() as cur:
            JobRepository(cur).record_retry_error(job_id, error)
        log.warning("job.retry_scheduled", error=str(e), next_try=job_try + 1)
        raise Retry(defer=_backoff_seconds(job_try)) from e

    # Wave: HTML report rendering. `ReportFormat` is a `StrEnum`, so this
    # comparison also accepts the plain "pdf"/"html" strings this function's
    # own `output_format` parameter is typed as (arq job args are plain
    # JSON-serialisable values, never real enum instances) - see
    # app.domain.enums.ReportFormat's own docstring.
    ext = "pdf" if output_format == ReportFormat.PDF else "html"
    fd, tmp_path = tempfile.mkstemp(suffix=f".{ext}", prefix="dmrv_report_")
    with os.fdopen(fd, "wb") as f:
        f.write(report_bytes)
    storage_key = f"reports/{project_id}/{job_id}.{ext}"
    storage.save(storage_key, tmp_path)
    # Wave: security fix pass. `project_name` is user-entered (max_length=256,
    # no charset constraint - see app.domain.dtos) and flows straight into a
    # Content-Disposition header at download time; strip the characters that
    # can break that header's own quoting/parsing (`"`, CR, LF, `;`) here, at
    # the one place `filename` is constructed and persisted into
    # `job.result`, rather than trusting every future reader of that column
    # to re-sanitise it. `app.core.http_headers.content_disposition_attachment`
    # still re-applies the same stripping defensively at response time (a
    # legacy job row written before this fix could still hold an
    # unsanitised filename), so this is belt-and-braces, not the only guard.
    safe_project_name = strip_header_injection_chars(project_name)
    filename = f"{safe_project_name}-report-{date.today().isoformat()}.{ext}"

    with db.transaction() as cur:
        AuditRepository(cur).record(
            actor_id=actor["user_id"], actor_name=actor["username"],
            action=AuditAction.GENERATE_REPORT, target=",".join(analysis_ids),
            detail=(
                f"Generated a {ext.upper()} report for project {project_id} "
                f"({len(analysis_ids)} analyses, job {job_id})."
            ),
            project_id=project_id,
        )
        JobRepository(cur).mark_succeeded(
            job_id, {"storage_key": storage_key, "filename": filename, "format": output_format}
        )
    jobs_completed_total.labels(kind=_KIND, status="succeeded").inc()
    job_duration_seconds.labels(kind=_KIND).observe(time.perf_counter() - start)
    log.info("job.succeeded", analysis_count=len(analysis_ids))
