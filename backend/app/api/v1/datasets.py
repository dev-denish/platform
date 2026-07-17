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

import contextlib
import json
import os
import tempfile
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile

from app.api.deps import get_job_service, get_settings, get_task_runner, require_role
from app.core.config import Settings
from app.core.errors import DomainError, PayloadTooLargeError, ValidationError
from app.core.logging import request_id_ctx
from app.core.metrics import jobs_submitted_total
from app.core.ratelimit import limiter
from app.domain.dtos import CurrentUser, IngestMetadata, JobAccepted
from app.domain.enums import UPLOAD_ROLES
from app.services.jobs_service import JobService
from app.workers.jobs import run_ingest_job
from app.workers.queue import TaskRunner

router = APIRouter(tags=["datasets"])

_CHUNK = 1024 * 1024  # 1 MiB
_INGEST_KIND = "ingest_dataset"


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
    accuracy_score: float = Form(...),
    date_processed: str = Form(...),
    region: str = Form("Unspecified"),
    classification_method: str = Form(""),
    pixel_size_m: float = Form(10.0),
    class_legend: str | None = Form(None),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobAccepted:
    # validate extension against the allow-list
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in settings.allowed_raster_extensions:
        raise ValidationError(
            f"Unsupported file type '{ext or '(none)'}'. "
            f"Allowed: {', '.join(settings.allowed_raster_extensions)}."
        )

    # validate metadata via the DTO (raises 422 on bad values)
    try:
        meta = IngestMetadata(
            project_name=project_name, region=region, dataset_type=dataset_type,  # type: ignore[arg-type]
            source=source, classification_method=classification_method,
            accuracy_score=accuracy_score, date_processed=date_processed,  # type: ignore[arg-type]
            pixel_size_m=pixel_size_m,
        )
    except Exception as e:
        raise ValidationError(f"Invalid metadata: {e}") from e

    legend = None
    if class_legend:
        try:
            legend = json.loads(class_legend)
        except json.JSONDecodeError as e:
            raise ValidationError("class_legend must be valid JSON.") from e

    staged = await _stream_to_temp(
        file, suffix=ext, max_bytes=settings.max_upload_bytes,
        staging_dir=settings.upload_staging_dir,
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
