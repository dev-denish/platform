"""
Dataset upload endpoint (v1).

Existing implementation (Phase 1): `async def` handler streamed the file to a temp
path (size-capped, extension allow-listed, metadata validated by a Pydantic DTO,
RBAC via `require_role`), then dispatched the ingest to the `TaskRunner` and
AWAITED it to completion before returning 201 + the full `IngestResult` body - the
request still blocked for the whole ingest (raster reprojection, stats, DB writes),
just off the event loop's own thread.

Phase 2 solution: this IS the fully async job+polling iteration. The endpoint keeps
every validation step exactly as it was (nothing about trust/size/type checking
changes), but after staging the file it:
  1. inserts a `jobs` row (idempotency-aware - see `Idempotency-Key`),
  2. enqueues the ingest job on the `TaskRunner` WITHOUT awaiting its result,
  3. returns 202 + `{job_id, status_url}` immediately.
Poll `GET /api/v1/jobs/{id}` for the outcome (queued -> running -> succeeded /
failed / dead_letter).
"""

import asyncio
import contextlib
import json
import os
import tempfile
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile

from uuid import UUID

from app.api.deps import get_dataset_delete_service, get_job_service, get_settings, get_task_runner, require_role
from app.core.config import Settings
from app.core.errors import DomainError, PayloadTooLargeError, UnprocessableError, ValidationError
from app.core.logging import request_id_ctx
from app.core.metrics import jobs_submitted_total
from app.core.ratelimit import limiter
from app.domain.dtos import CurrentUser, IngestMetadata, JobAccepted, ScanValuesResult
from app.domain.enums import DELETE_DATASET_ROLES, UPLOAD_ROLES
from app.services.dataset_delete_service import DatasetDeleteService
from app.services.ingestion import raster as R
from app.services.ingestion.vector import csv_header
from app.services.jobs_service import JobService
from app.workers.jobs import run_ingest_job
from app.workers.queue import TaskRunner

router = APIRouter(tags=["datasets"])

_CHUNK = 1024 * 1024  # 1 MiB
_INGEST_KIND = "ingest_dataset"


def _has_real_label(entry: object) -> bool:
    """False for an entry whose label is blank/whitespace/"none" - the one
    thing a class_legend entry must never be, since it would otherwise
    surface as a real "None" class in KPIs/evolution. Checked here (the sole
    entry point for a class_legend, regardless of client) rather than only in
    the upload builder UI, so a direct API call can't reopen the same hole."""
    label = entry.get("label") if isinstance(entry, dict) else entry
    return isinstance(label, str) and bool(label.strip()) and label.strip().lower() != "none"


async def _stream_to_temp(
    file: UploadFile, *, suffix: str, max_bytes: int, staging_dir: str
) -> str:
    """Stream the upload to a temp file, aborting past the size cap. Never loads the
    whole file into memory.

    Staged under `staging_dir`, NOT the OS default tmp dir: this path is handed to a
    job that may run in a separate `worker` container (see workers/arq_worker.py),
    which only shares the upload-staging volume with the API container - not /tmp.
    A path on the API container's local disk would be "No such file or directory"
    to the worker.
    """
    written = 0
    os.makedirs(staging_dir, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="dmrv_upload_", dir=staging_dir)
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await file.read(_CHUNK):
                written += len(chunk)
                if written > max_bytes:
                    raise PayloadTooLargeError(
                        f"Upload exceeds the {max_bytes // (1024 * 1024)} MiB limit."
                    )
                out.write(chunk)
        return path
    except BaseException:
        if os.path.exists(path):
            os.unlink(path)
        raise


@router.post("/datasets/upload", response_model=JobAccepted, status_code=202)
@limiter.limit("20/hour")
async def upload_dataset(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    jobs: Annotated[JobService, Depends(get_job_service)],
    runner: Annotated[TaskRunner, Depends(get_task_runner)],
    user: Annotated[CurrentUser, Depends(require_role(*UPLOAD_ROLES))],
    file: UploadFile = File(...),
    project_name: str = Form(...),
    dataset_type: str = Form(...),
    source: str = Form(...),
    accuracy_score: float | None = Form(None),
    date_processed: str = Form(...),
    region: str = Form("Unspecified"),
    classification_method: str = Form(""),
    pixel_size_m: float = Form(10.0),
    class_legend: str | None = Form(None),
    lat_column: str | None = Form(None),
    lon_column: str | None = Form(None),
    is_reference: bool = Form(False),
    # Wave: upload project-name footgun fix. Defaults to False - a mismatched
    # project_name (e.g. a typo) errors instead of silently forking a
    # duplicate project. Set True only when the caller (the UI's "This is a
    # new project" checkbox) explicitly confirms this project doesn't exist
    # yet - see project_access.resolve_project_for_upload.
    create_new_project: bool = Form(False),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobAccepted:
    # validate extension against the allow-list (raster OR vector - Wave:
    # multi-format layers; IngestionService.ingest branches on this same
    # extension to pick raster vs vector parsing).
    ext = os.path.splitext(file.filename or "")[1].lower()
    allowed = settings.allowed_raster_extensions + settings.allowed_vector_extensions
    if ext not in allowed:
        raise ValidationError(
            f"Unsupported file type '{ext or '(none)'}'. Allowed: {', '.join(allowed)}."
        )
    if ext == ".csv" and (not lat_column or not lon_column):
        raise ValidationError("lat_column and lon_column are required for a CSV upload.")

    # validate metadata via the DTO (raises 422 on bad values)
    try:
        meta = IngestMetadata(
            project_name=project_name, region=region, dataset_type=dataset_type,  # type: ignore[arg-type]
            source=source, classification_method=classification_method,
            accuracy_score=accuracy_score, date_processed=date_processed,  # type: ignore[arg-type]
            pixel_size_m=pixel_size_m, lat_column=lat_column, lon_column=lon_column,
            is_reference=is_reference, create_new_project=create_new_project,
        )
    except Exception as e:
        raise ValidationError(f"Invalid metadata: {e}") from e

    legend = None
    if class_legend:
        try:
            legend = json.loads(class_legend)
        except json.JSONDecodeError as e:
            raise ValidationError("class_legend must be valid JSON.") from e
        if isinstance(legend, dict):
            legend = {k: v for k, v in legend.items() if _has_real_label(v)} or None

    # accuracy_score is a classification-accuracy metric: only meaningful (and
    # required) when a class_legend defines what's being classified.
    if legend and meta.accuracy_score is None:
        raise ValidationError("accuracy_score is required when a class_legend is supplied.")

    staged = await _stream_to_temp(
        file, suffix=ext, max_bytes=settings.max_upload_bytes,
        staging_dir=settings.upload_staging_dir,
    )

    # Wave: multi-format layers. Validated against the REAL uploaded file's
    # header, not the client's own (UX-only) CSV parse - a direct API call
    # supplying columns that don't actually exist must fail here, before a
    # job slot is even consumed, exactly like "reject clearly if no valid
    # lat/lon can be resolved" for the KML/shapefile/GeoJSON formats above.
    if ext == ".csv":
        header = csv_header(staged)
        if lat_column not in header or lon_column not in header:
            with contextlib.suppress(OSError):
                os.unlink(staged)
            raise ValidationError(
                f"Columns '{lat_column}'/'{lon_column}' not found in the CSV header: {header}."
            )

    request_id = request_id_ctx.get()
    job_id, is_new = jobs.submit(
        user_id=user.user_id, kind=_INGEST_KIND,
        idempotency_key=idempotency_key, request_id=request_id,
    )
    status_url = f"{settings.api_v1_prefix}/jobs/{job_id}"

    if not is_new:
        # Idempotent replay: an earlier submission already owns this job; nothing
        # new to stage or enqueue.
        with contextlib.suppress(OSError):
            os.unlink(staged)
        return JobAccepted(job_id=job_id, status_url=status_url)

    jobs_submitted_total.labels(kind=_INGEST_KIND).inc()

    try:
        # Dispatch only - `run()` no longer awaits the ingest to completion (see
        # workers/queue.py); the outcome lands in the `jobs` row via workers/jobs.py.
        await runner.run(
            run_ingest_job,
            job_id=str(job_id), staged_path=staged, meta=meta.model_dump(mode="json"),
            legend=legend, actor=user.model_dump(mode="json"), request_id=request_id,
        )
    except Exception as e:
        # The jobs-row insert already committed. If enqueueing then fails (e.g.
        # Redis unreachable), mark the row failed instead of leaving an orphaned
        # `queued` job nothing will ever process.
        with contextlib.suppress(OSError):
            os.unlink(staged)
        jobs.mark_enqueue_failed(job_id, str(e))
        raise DomainError("Failed to enqueue the ingest job. Please retry the upload.") from e

    return JobAccepted(job_id=job_id, status_url=status_url)


@router.post("/datasets/scan-values", response_model=ScanValuesResult)
@limiter.limit("30/hour")
async def scan_raster_values(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    user: Annotated[CurrentUser, Depends(require_role(*UPLOAD_ROLES))],
    file: UploadFile = File(...),
) -> ScanValuesResult:
    """Class Legend Builder's "Scan file" action (matches QGIS's "Classify"):
    read the actual distinct pixel values in band 1 of the file about to be
    uploaded, so the legend can be pre-populated with one row per real value
    instead of the user guessing what's in the raster. Synchronous - a
    windowed, memory-bounded read (`R.scan_distinct_values`), same size cap
    as the real upload, off the event loop via `asyncio.to_thread` but with
    nothing to persist, so there's no job/polling round trip needed."""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in settings.allowed_raster_extensions:
        raise ValidationError(
            f"Unsupported file type '{ext or '(none)'}'. "
            f"Allowed: {', '.join(settings.allowed_raster_extensions)}."
        )
    staged = await _stream_to_temp(
        file, suffix=ext, max_bytes=settings.max_upload_bytes,
        staging_dir=settings.upload_staging_dir,
    )
    try:
        values = await asyncio.to_thread(
            R.scan_distinct_values, staged, settings.raster_window_size
        )
    except Exception as e:  # rasterio/GDAL failure -> client-safe 422
        raise UnprocessableError(f"Raster could not be scanned: {e}") from e
    finally:
        with contextlib.suppress(OSError):
            os.unlink(staged)
    return ScanValuesResult(values=values)


@router.delete("/datasets/{layer_id}", status_code=204, response_model=None)
def delete_dataset(
    layer_id: UUID,
    user: Annotated[CurrentUser, Depends(require_role(*DELETE_DATASET_ROLES))],
    svc: Annotated[DatasetDeleteService, Depends(get_dataset_delete_service)],
) -> None:
    """Delete-a-dataset: a formal, project-scoped upload - not a reference
    layer (`DELETE /reference-layers/{id}`) or an ad-hoc quick-add
    (`DELETE /adhoc-layers/{id}`), which keep their own endpoints/roles.
    Administrator-only, global gate - see DELETE_DATASET_ROLES."""
    svc.remove(layer_id, user)
