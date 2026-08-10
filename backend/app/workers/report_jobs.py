"""The `generate_report` job lifecycle (Wave: PDF report). Same generic-retry
shape as workers/gee_analysis_jobs.py's `run_gee_analysis_job` - a failure
here (GEE quota/network, a boundary too large) is presumed transient, so
every exception gets the same retry-or-dead-letter treatment."""
from __future__ import annotations

import os
import tempfile
import time
from datetime import date
from typing import Any

from arq.worker import Retry

from app.core.db import Database
from app.core.logging import get_logger
from app.core.metrics import job_duration_seconds, jobs_completed_total
from app.domain.enums import AuditAction
from app.repositories.audit import AuditRepository
from app.repositories.jobs import JobRepository
from app.services.ingestion.storage import Storage
from app.services.report_service import generate_report_pdf_bytes

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
        pdf_bytes = generate_report_pdf_bytes(
            db, project_id, project_name, analysis_ids, boundary_geojson
        )
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

    fd, tmp_path = tempfile.mkstemp(suffix=".pdf", prefix="dmrv_report_")
    with os.fdopen(fd, "wb") as f:
        f.write(pdf_bytes)
    storage_key = f"reports/{project_id}/{job_id}.pdf"
    storage.save(storage_key, tmp_path)
    filename = f"{project_name}-report-{date.today().isoformat()}.pdf"

    with db.transaction() as cur:
        AuditRepository(cur).record(
            actor_id=actor["user_id"], actor_name=actor["username"],
            action=AuditAction.GENERATE_REPORT, target=",".join(analysis_ids),
            detail=(
                f"Generated a PDF report for project {project_id} "
                f"({len(analysis_ids)} analyses, job {job_id})."
            ),
            project_id=project_id,
        )
        JobRepository(cur).mark_succeeded(
            job_id, {"storage_key": storage_key, "filename": filename}
        )
    jobs_completed_total.labels(kind=_KIND, status="succeeded").inc()
    job_duration_seconds.labels(kind=_KIND).observe(time.perf_counter() - start)
    log.info("job.succeeded", analysis_count=len(analysis_ids))
