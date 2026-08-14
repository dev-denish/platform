"""The `compute_vnv_ndfi` job lifecycle (Wave: VNV Pipeline NDFI go-live).

Separate module from workers/gee_analysis_jobs.py deliberately - this is a
second, independent compute source (see app/services/vnv_analysis_service.py's
own docstring) that does NOT touch the GEE compute path/logic at all.
Mirrors that module's generic-retry lifecycle shape for the `jobs` table
row (mark_running at start, the SAME retry-or-dead-letter treatment on ANY
exception - a CDSE fetch failure, an unreachable sidecar, a bad sidecar
response are all presumed transient, same as a GEE compute failure -
mark_succeeded on success), but ALSO drives its own `analysis_runs` row
(migrations/versions/0019_analysis_runs.py) through running/done/failed:
that row - not `jobs.result`/`jobs.error` - is what records this specific
run's real input/output raster paths and failure detail. EVERY exception,
at every step below, lands in `analysis_runs.error_message` before the
generic `jobs` retry/dead-letter handling runs - nothing is silently
swallowed.

Real compute steps (see CDSEClient's own docstring in
app/services/cdse_ingestion.py and the sidecar's own plumber.R for full
detail on each):
  1. A 90-day trailing window ending today (server-computed default - no
     query params exist for a caller-chosen window yet, out of scope this
     session).
  2. `CDSEClient.prepare_sentinel2_aoi_raster` -> a 6-band (B02/B03/B04/
     B08/B11/B12) AOI-clipped, cloud-masked GeoTIFF written to local disk
     under `DMRV_LOCAL_DATA_DIR`. Constructed the same way
     scripts/verify_cdse_ingestion.py already does (`CDSEClient(settings)`
     directly, from `app.core.config.get_settings()`) - there is no other
     DI-wired construction path for it yet (Phase 1 has no route).
  3. `POST {DMRV_FORESTOOLBOX_URL}/ndfi` with that SAME absolute path for
     both `input_path`/`output_path` - this worker and the sidecar
     container are expected to mount the same `DMRV_LOCAL_DATA_DIR`-rooted
     volume (a parallel docker-devops change), so no path remapping
     happens here; this job just passes its own local path straight
     through.
  4. Parse the sidecar's `{status, output_path, stats, endmembers}` JSON
     response.
  5. Upsert into the EXISTING `analysis_result` table (same table/repo the
     GEE path writes, via `AnalysisResultRepository`) so the unchanged,
     already-generic `GET /projects/{id}/analyses/vnv_ndfi` endpoint
     returns something real - no parallel API needed.

Verified live against the real sidecar (not assumed): R's default JSON
serializer (jsonlite, `auto_unbox` unset) wraps every scalar - `output_path`
and each of `stats.min/max/mean/valid_pixel_count/total_pixel_count/
masked_fraction` - in a length-1 JSON array rather than emitting a bare
value. Confirmed independently (a raw POST to /ndfi, not just this job's own
parse) and NOT a plumber.R change to make here - fixing the sidecar's own
serialization is Phase 2/domain-review territory, out of scope this session.
`_unbox` below is this job's own defensive parsing of that contract: unwraps
a real length-1 list to its element, leaves anything else (a real multi-
element list, a dict, already-scalar) untouched. Without it,
`analysis_runs.output_raster_ref` wound up storing the literal text
"{path}" (psycopg's array-literal rendering of a Python list bound to a
TEXT column) instead of a clean path - caught by inspecting the real DB row
after a real end-to-end run, not by inspection alone.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

import httpx
from arq.worker import Retry

from app.core.db import Database
from app.core.logging import get_logger
from app.core.metrics import job_duration_seconds, jobs_completed_total
from app.repositories.analysis_results import AnalysisResultRepository
from app.repositories.analysis_runs import AnalysisRunRepository
from app.repositories.jobs import JobRepository
from app.services.cdse_ingestion import CDSEClient

_KIND = "compute_vnv_ndfi"
_ANALYSIS_ID = "vnv_ndfi"
_WINDOW_DAYS = 90
_ERROR_MESSAGE_MAX_LEN = 2000  # a sidecar/CDSE error body can be arbitrarily large


def _backoff_seconds(job_try: int) -> float:
    # Same fixed exponential backoff as run_gee_analysis_job/run_ingest_job -
    # give it a per-kind config knob if these ever need different pacing.
    return min(2**job_try, 60)


def _trailing_window() -> tuple[str, str]:
    """A 90-day trailing window ending today, as plain "YYYY-MM-DD" strings
    (the shape `CDSEClient.search_scenes`/`prepare_sentinel2_aoi_raster`
    expect). No query params exist for a caller-chosen window yet - out of
    scope this session; this is simply this job's own server-computed
    default."""
    end = datetime.utcnow().date()
    start = end - timedelta(days=_WINDOW_DAYS)
    return start.isoformat(), end.isoformat()


def _unbox(value: Any) -> Any:
    """R/jsonlite serializes a scalar as a length-1 JSON array rather than a
    bare value (verified against the real sidecar response, see module
    docstring) - unwrap that one specific shape. A genuine multi-element
    list (or anything already scalar) passes through unchanged; this is
    intentionally narrow, not a general "flatten everything" helper."""
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


async def run_vnv_ndfi_analysis(
    ctx: dict[str, Any],
    *,
    job_id: str,
    analysis_run_id: str,
    project_id: str,
    boundary_geojson: dict[str, Any],
    actor: dict[str, Any],
) -> None:
    """Job body for the `compute_vnv_ndfi` kind. All the permission/catalog/
    boundary validation already happened synchronously, at request time, in
    `VNVAnalysisService.enqueue_refresh`; by the time this runs, the only
    thing left to do is the real (slow) CDSE fetch + sidecar call and the
    upsert into `analysis_result` mirroring `refresh()`'s own convention for
    the GEE path."""
    db: Database = ctx["db"]
    job_try: int = ctx.get("job_try", 1)
    settings = ctx["settings"]
    log = get_logger("dmrv.jobs").bind(
        job_id=job_id, kind=_KIND, job_try=job_try, analysis_run_id=analysis_run_id
    )

    with db.transaction() as cur:
        JobRepository(cur).mark_running(job_id)
        AnalysisRunRepository(cur).mark_running(analysis_run_id)
    log.info("job.running")

    start = time.perf_counter()
    try:
        date_start, date_end = _trailing_window()
        input_path = f"{settings.local_data_dir}/vnv/{analysis_run_id}/s2_aoi.tif"
        output_path = f"{settings.local_data_dir}/vnv/{analysis_run_id}/s2_aoi_ndfi.tif"

        client = CDSEClient(settings)
        client.prepare_sentinel2_aoi_raster(boundary_geojson, date_start, date_end, input_path)
        with db.transaction() as cur:
            AnalysisRunRepository(cur).set_input_raster_ref(analysis_run_id, input_path)

        forestoolbox_url = settings.forestoolbox_url.rstrip("/")
        resp = httpx.post(
            f"{forestoolbox_url}/ndfi",
            data={"input_path": input_path, "output_path": output_path},
            timeout=300.0,
        )
        resp.raise_for_status()
        payload = resp.json()
        # _unbox only the known scalar fields - `endmembers.spectra`/
        # `endmembers.citations` are real (possibly multi-element) lists and
        # must stay lists, so `endmembers` itself is copied through as-is.
        stats: dict[str, Any] = {k: _unbox(v) for k, v in payload["stats"].items()}
        endmembers = payload.get("endmembers")
        if endmembers is not None:
            stats["endmembers"] = endmembers
        output_raster_ref = _unbox(payload["output_path"])

    except Exception as e:  # noqa: BLE001 - presumed transient (CDSE/sidecar/network); classified below
        error_message = str(e)[:_ERROR_MESSAGE_MAX_LEN]
        with db.transaction() as cur:
            AnalysisRunRepository(cur).mark_failed(analysis_run_id, error_message)

        error = {"code": "job_error", "message": error_message}
        max_tries = settings.job_max_retries
        if job_try >= max_tries:
            with db.transaction() as cur:
                JobRepository(cur).mark_dead_letter(job_id, error)
            jobs_completed_total.labels(kind=_KIND, status="dead_letter").inc()
            job_duration_seconds.labels(kind=_KIND).observe(time.perf_counter() - start)
            log.error("job.dead_letter", error=error_message)
            return
        with db.transaction() as cur:
            JobRepository(cur).record_retry_error(job_id, error)
        log.warning("job.retry_scheduled", error=error_message, next_try=job_try + 1)
        raise Retry(defer=_backoff_seconds(job_try)) from e

    with db.transaction() as cur:
        AnalysisResultRepository(cur).upsert(
            project_id=project_id, analysis_id=_ANALYSIS_ID, computed_by=actor["user_id"],
            stats=stats, legend=None, tile_url_template=None,
        )
        AnalysisRunRepository(cur).mark_done(
            analysis_run_id, output_raster_ref=output_raster_ref, stats=stats,
        )
        JobRepository(cur).mark_succeeded(
            job_id, {"analysis_id": _ANALYSIS_ID, "analysis_run_id": analysis_run_id}
        )
    jobs_completed_total.labels(kind=_KIND, status="succeeded").inc()
    job_duration_seconds.labels(kind=_KIND).observe(time.perf_counter() - start)
    log.info("job.succeeded", analysis_id=_ANALYSIS_ID)
