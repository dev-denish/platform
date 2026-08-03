"""Ad-hoc layer upload (Wave 3: Added Layers) - a lightweight, project-scoped
upload for a quick raster/vector/KML addition from the map, without leaving
the map view. Reuses IngestionService.ingest entirely - the same async job
pipeline, COG conversion, and vector_feature storage a formal upload gets -
just a display name instead of the full dataset_type/class_legend/
accuracy_score form (see IngestMetadata.is_adhoc/project_id, set only here).
CSV is deliberately excluded: it needs lat/lon column selection, which is
exactly the kind of extra required field this endpoint exists to skip.

Deliberately no `from __future__ import annotations` here (unlike most of
this codebase): with it present, this exact route signature (UploadFile +
Form fields + @limiter.limit, otherwise identical in shape to datasets.py's
upload_dataset) fails at import time - fastapi.exceptions.FastAPIError:
"Invalid args for response field! ... ForwardRef('UploadFile')" - reproduced
against the pinned production fastapi==0.115.6/pydantic==2.10.4 versions
specifically (not the newer versions in this repo's local dev venv, which
mask it). Root cause traced to slowapi's functools.wraps-based limiter
wrapper interacting with lazy (PEP 563) string annotations during FastAPI's
dependant analysis; not fully explained why datasets.py's identically-shaped
endpoint doesn't hit it. Omitting the future import here sidesteps the whole
class of bug for this file - annotations are real objects at decoration
time, nothing left to resolve lazily."""

import contextlib
import os
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile

from app.api.deps import (
    get_adhoc_layer_service,
    get_job_service,
    get_settings,
    get_task_runner,
    require_role,
)
from app.api.v1.datasets import _stream_to_temp
from app.core.config import Settings
from app.core.errors import DomainError, ValidationError
from app.core.logging import request_id_ctx
from app.core.metrics import jobs_submitted_total
from app.core.ratelimit import limiter
from app.domain.dtos import CurrentUser, IngestMetadata, JobAccepted
from app.domain.enums import UPLOAD_ROLES, DatasetType
from app.services.adhoc_layer_service import AdhocLayerService
from app.services.jobs_service import JobService
from app.workers.jobs import run_ingest_job
from app.workers.queue import TaskRunner

router = APIRouter(tags=["adhoc-layers"])

_INGEST_KIND = "ingest_dataset"
# The generic, unclassified/no-legend dataset type every ad-hoc layer is
# stored as - it's never shown to the uploader (no dataset_type field on
# this endpoint at all), just the least-surprising internal label for
# "some raster or vector someone dropped on the map".
_ADHOC_DATASET_TYPE = DatasetType.SATELLITE


@router.post("/projects/{project_id}/adhoc-layers", response_model=JobAccepted, status_code=202)
@limiter.limit("20/hour")
async def upload_adhoc_layer(
    request: Request,
    project_id: UUID,
    settings: Annotated[Settings, Depends(get_settings)],
    jobs: Annotated[JobService, Depends(get_job_service)],
    runner: Annotated[TaskRunner, Depends(get_task_runner)],
    user: Annotated[CurrentUser, Depends(require_role(*UPLOAD_ROLES))],
    file: UploadFile = File(...),
    display_name: str = Form(..., min_length=1, max_length=256),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobAccepted:
    ext = os.path.splitext(file.filename or "")[1].lower()
    allowed = settings.allowed_raster_extensions + tuple(
        e for e in settings.allowed_vector_extensions if e != ".csv"
    )
    if ext not in allowed:
        raise ValidationError(
            f"Unsupported file type '{ext or '(none)'}' for a quick add. "
            f"Allowed: {', '.join(allowed)}. Use the full upload flow for a CSV."
        )

    meta = IngestMetadata(
        project_id=project_id, dataset_type=_ADHOC_DATASET_TYPE,
        source=display_name.strip(), date_processed=date.today().isoformat(),  # type: ignore[arg-type]
        is_adhoc=True,
    )

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
        with contextlib.suppress(OSError):
            os.unlink(staged)
        return JobAccepted(job_id=job_id, status_url=status_url)

    jobs_submitted_total.labels(kind=_INGEST_KIND).inc()

    try:
        await runner.run(
            run_ingest_job,
            job_id=str(job_id), staged_path=staged, meta=meta.model_dump(mode="json"),
            legend=None, actor=user.model_dump(mode="json"), request_id=request_id,
        )
    except Exception as e:
        with contextlib.suppress(OSError):
            os.unlink(staged)
        jobs.mark_enqueue_failed(job_id, str(e))
        raise DomainError("Failed to enqueue the ad-hoc layer upload. Please retry.") from e

    return JobAccepted(job_id=job_id, status_url=status_url)


@router.delete("/adhoc-layers/{layer_id}", status_code=204, response_model=None)
def remove_adhoc_layer(
    layer_id: UUID,
    user: Annotated[CurrentUser, Depends(require_role(*UPLOAD_ROLES))],
    svc: Annotated[AdhocLayerService, Depends(get_adhoc_layer_service)],
) -> None:
    svc.remove(layer_id, user)
