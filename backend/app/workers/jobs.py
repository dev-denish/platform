"""
The ingest job lifecycle.

Runs identically from BOTH `TaskRunner` backends (see workers/queue.py):
`ArqTaskRunner` dispatches it by name to a separate `arq worker` process
(app/workers/arq_worker.py); `ThreadPoolTaskRunner` calls it in-process for local
dev without Redis. One function owns every `jobs` row state transition, so the
retry/dead-letter rules can't drift between the two call paths.
"""
from __future__ import annotations

import time
from typing import Any

from arq.worker import Retry

from app.core.db import Database
from app.core.errors import UnprocessableError, ValidationError
from app.core.logging import get_logger
from app.core.metrics import job_duration_seconds, jobs_completed_total
from app.domain.dtos import CurrentUser, IngestMetadata
from app.repositories.jobs import JobRepository
from app.services.ingestion.service import IngestionService

_KIND = "ingest_dataset"


def _backoff_seconds(job_try: int) -> float:
    # ponytail: fixed exponential backoff capped at 60s. Good enough for one job
    # kind; give it a per-kind config knob if/when a second kind needs different
    # pacing.
    return min(2**job_try, 60)


async def run_ingest_job(
    ctx: dict[str, Any],
    *,
    job_id: str,
    staged_path: str,
    meta: dict[str, Any],
    legend: dict[str, Any] | None,
    actor: dict[str, Any],
    request_id: str | None,
) -> None:
    """Job body for the `ingest_dataset` kind. `ctx` is either arq's own per-job
    context (worker process; carries `job_try`, populated by `arq_worker.on_startup`
    with `db`/`storage`/`settings`) or `ThreadPoolTaskRunner`'s fixed ctx dict - see
    workers/queue.py for why both shapes match.
    """
    db: Database = ctx["db"]
    settings = ctx["settings"]
    storage = ctx["storage"]
    job_try: int = ctx.get("job_try", 1)

    log = get_logger("dmrv.jobs").bind(job_id=job_id, request_id=request_id, job_try=job_try)

    with db.transaction() as cur:
        JobRepository(cur).mark_running(job_id)
    log.info("job.running")

    start = time.perf_counter()
    try:
        meta_obj = IngestMetadata(**meta)
        actor_obj = CurrentUser(**actor)
        svc = IngestionService(db, settings, storage)
        # Synchronous, CPU/IO-bound (rasterio, numpy, DB writes); calling it inline
        # blocks THIS worker process's event loop for the duration. That's fine for
        # a dedicated worker process handling one job at a time - unlike the API
        # process, which must stay responsive to many concurrent requests. Upgrade
        # path if that trade-off ever bites: `loop.run_in_executor`.
        result = svc.ingest(
            staged_path=staged_path, meta=meta_obj, legend=legend, actor=actor_obj
        )
    except (UnprocessableError, ValidationError) as e:
        # Not worth retrying: the input itself is bad (corrupt raster, bad values).
        with db.transaction() as cur:
            JobRepository(cur).mark_failed(job_id, {"code": e.code, "message": e.message})
        jobs_completed_total.labels(kind=_KIND, status="failed").inc()
        job_duration_seconds.labels(kind=_KIND).observe(time.perf_counter() - start)
        log.warning("job.failed", code=e.code, error=e.message)
        return
    except Exception as e:  # noqa: BLE001 - transient/infra failure, classified below
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
        # Verified by reading arq.worker.Worker.run_job (arq 0.28.0) directly: a
        # BARE exception is a PERMANENT arq-level failure - arq only retries on an
        # explicit `Retry`, never on a plain re-raise. So raise it explicitly here.
        # Under ThreadPoolTaskRunner there is no worker loop to catch `Retry`
        # either; it just surfaces to the add_done_callback logger in
        # workers/queue.py, and the row stays `queued` with the error recorded,
        # awaiting a manual/administrative retry (see that class's ponytail note).
        raise Retry(defer=_backoff_seconds(job_try)) from e

    with db.transaction() as cur:
        JobRepository(cur).mark_succeeded(job_id, result.model_dump(mode="json"))
    jobs_completed_total.labels(kind=_KIND, status="succeeded").inc()
    job_duration_seconds.labels(kind=_KIND).observe(time.perf_counter() - start)
    log.info("job.succeeded", dataset_id=str(result.dataset_id))
