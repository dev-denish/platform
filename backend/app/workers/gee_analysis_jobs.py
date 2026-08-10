"""
The `compute_gee_analysis` job lifecycle (Wave: vegetation indices).

Separate module from workers/jobs.py's `run_ingest_job` deliberately - that
file's own docstring frames it as owning "every jobs row state transition"
for ONE kind (ingest_dataset); this is a second, independent kind with its
own (simpler) generic-retry rule, not a branch bolted onto that function.

Unlike ingest, there's no domain-specific "this input is bad, don't retry"
tier here - a GEE compute failure (quota, transient network, a boundary too
large) is presumed transient, so every exception gets the same retry-or-
dead-letter treatment `run_ingest_job` reserves for its own generic
Exception branch.
"""
from __future__ import annotations

import time
from typing import Any

from arq.worker import Retry

from app.core.db import Database
from app.core.logging import get_logger
from app.core.metrics import job_duration_seconds, jobs_completed_total
from app.domain import analysis_config
from app.domain.enums import AuditAction
from app.repositories.analysis_results import AnalysisResultRepository
from app.repositories.audit import AuditRepository
from app.repositories.jobs import JobRepository
from app.services.gee_analysis_service import _compute_cached

_KIND = "compute_gee_analysis"


def _backoff_seconds(job_try: int) -> float:
    # ponytail: same fixed exponential backoff as run_ingest_job's own -
    # give it a per-kind config knob if these two ever need different pacing.
    return min(2**job_try, 60)


async def run_gee_analysis_job(
    ctx: dict[str, Any],
    *,
    job_id: str,
    project_id: str,
    analysis_id: str,
    boundary_geojson: dict[str, Any],
    canopy_cover_pct: float,
    actor: dict[str, Any],
    request_params: dict[str, Any] | None = None,
) -> None:
    """Job body for the `compute_gee_analysis` kind. All the permission/
    catalog/boundary validation - including, since Wave: analysis config and
    methodology, resolving+validating `request_params` for the 5 vegetation
    indices - already happened synchronously, at request time, in
    GEEAnalysisService.enqueue_refresh; `request_params` arrives here
    ALREADY RESOLVED, never re-validated. By the time this runs, the only
    thing left to do is the actual (slow) GEE computation and the same
    upsert+audit `refresh()` does for the synchronous analyses."""
    db: Database = ctx["db"]
    job_try: int = ctx.get("job_try", 1)
    settings = ctx["settings"]
    log = get_logger("dmrv.jobs").bind(job_id=job_id, kind=_KIND, job_try=job_try)

    with db.transaction() as cur:
        JobRepository(cur).mark_running(job_id)
    log.info("job.running")

    start = time.perf_counter()
    try:
        # Wave: GEE tile caching. `_compute_cached`, not `_compute` directly -
        # a re-run within the TTL window skips the real (5-59s for the
        # vegetation indices) GEE compute entirely.
        stats, legend, tile_url_template = _compute_cached(
            project_id, analysis_id, boundary_geojson, canopy_cover_pct, request_params
        )
    except Exception as e:  # noqa: BLE001 - presumed transient (quota/network); classified below
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

    with db.transaction() as cur:
        row = AnalysisResultRepository(cur).upsert(
            project_id=project_id, analysis_id=analysis_id, computed_by=actor["user_id"],
            stats=stats, legend=legend, tile_url_template=tile_url_template,
            params_key=analysis_config.params_key(analysis_id, request_params),
        )
        AuditRepository(cur).record(
            actor_id=actor["user_id"], actor_name=actor["username"],
            action=AuditAction.REFRESH_ANALYSIS, target=analysis_id,
            detail=f"Recomputed '{analysis_id}' for project {project_id} (async job {job_id}).",
            project_id=project_id,
        )
        JobRepository(cur).mark_succeeded(
            job_id, {"analysis_id": analysis_id, "computed_at": row["computed_at"].isoformat()}
        )
    jobs_completed_total.labels(kind=_KIND, status="succeeded").inc()
    job_duration_seconds.labels(kind=_KIND).observe(time.perf_counter() - start)
    log.info("job.succeeded", analysis_id=analysis_id)
