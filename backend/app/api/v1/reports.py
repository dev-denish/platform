"""PDF report endpoints (Wave: PDF report).

GET options is open to any project member (require_project_view, checked
inside ReportService.get_options) - same tier as GET /projects/{id}/analyses,
since listing what's already computed costs nothing. Generation is gated the
same way a refresh is (UPLOAD_ROLES at the route, project-tier
GIS-Associate-or-Administrator re-checked inside the service).

Generation is ALWAYS async (unlike /analyses/{id}/refresh's sync/async
split) - every report needs at least one fresh GEE tile fetch per selected
analysis on top of chart/PDF rendering, so there is no "fast enough for a
normal request/response" case here at all (see report_service.py's own
docstring for the measured reasoning).

The download endpoint uses plain CurrentUserDep, not a role gate - same as
GET /jobs/{id} ("every authenticated user may see the status of THEIR OWN
jobs - this is not a role gate"). Ownership is enforced by
JobService.get_for_user (a job belonging to someone else 404s, identical to
that endpoint) - a generated report is only downloadable by whoever
requested it; there is no cross-member report-sharing concept yet."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.deps import (
    CurrentUserDep,
    get_job_service,
    get_report_service,
    get_settings,
    get_storage,
    get_task_runner,
    require_role,
)
from app.core.config import Settings
from app.core.errors import NotFoundError, ValidationError
from app.core.http_headers import content_disposition_attachment
from app.domain.dtos import CurrentUser, GenerateReportRequest, JobAccepted, ReportOptions
from app.domain.enums import UPLOAD_ROLES, ReportFormat
from app.services.ingestion.storage import Storage
from app.services.jobs_service import JobService
from app.services.report_service import ReportService
from app.workers.queue import TaskRunner

router = APIRouter(tags=["reports"])


@router.get("/projects/{project_id}/report/options", response_model=ReportOptions)
def get_report_options(
    project_id: UUID,
    user: Annotated[CurrentUser, Depends(require_role(*UPLOAD_ROLES))],
    svc: Annotated[ReportService, Depends(get_report_service)],
) -> ReportOptions:
    return svc.get_options(project_id, user)


@router.post(
    "/projects/{project_id}/report", response_model=JobAccepted, status_code=202,
)
async def generate_report(
    project_id: UUID,
    body: GenerateReportRequest,
    user: Annotated[CurrentUser, Depends(require_role(*UPLOAD_ROLES))],
    svc: Annotated[ReportService, Depends(get_report_service)],
    jobs: Annotated[JobService, Depends(get_job_service)],
    runner: Annotated[TaskRunner, Depends(get_task_runner)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> JobAccepted:
    job_id = await svc.enqueue_generate(project_id, body, user, jobs, runner)
    return JobAccepted(job_id=job_id, status_url=f"{settings.api_v1_prefix}/jobs/{job_id}")


@router.get("/reports/{job_id}/download")
def download_report(
    job_id: UUID,
    user: CurrentUserDep,
    jobs: Annotated[JobService, Depends(get_job_service)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> StreamingResponse:
    job = jobs.get_for_user(job_id, user.user_id)
    if job.kind != "generate_report":
        raise NotFoundError("Job not found.")
    if job.status != "succeeded":
        raise ValidationError(f"This report is not ready yet (status: {job.status}).")
    result = job.result or {}
    storage_key = result.get("storage_key")
    # Wave: HTML report rendering. Backward-compat default for a legacy job
    # record (job.result is a JSONB/dict blob read at request time - no
    # schema migration needed, this `.get` default is the only compat shim)
    # persisted before this wave ever wrote a "format" key - every such row
    # is a PDF, since PDF was the only format that ever existed.
    output_format = result.get("format", "pdf")
    filename = result.get("filename", "report.pdf")
    if not storage_key:
        raise NotFoundError("This report's file is no longer available.")
    # Wave: security fix pass. `filename` is ultimately derived from a
    # user-entered project_name (app.domain.dtos, max_length=256 but no
    # charset constraint) - appsec-reviewer found two concrete failures from
    # interpolating it unquoted here: (1) Starlette encodes header values as
    # latin-1, so any non-latin-1 character (Kannada script, etc.) 500'd this
    # endpoint outright; (2) `"`/CR/LF/`;` could break Content-Disposition's
    # own quoting, newly more consequential now that HTML is a
    # browser-executable download format. `content_disposition_attachment`
    # strips the injection characters and emits the RFC 5987/6266 dual form
    # (ASCII-safe `filename=` fallback + `filename*=UTF-8''...`) so this
    # never 500s and never lets a crafted project name break the header,
    # regardless of whether app.workers.report_jobs already sanitised
    # `filename` before persisting it (a legacy job row predating that fix
    # could still hold an unsanitised one).
    default_name = f"report.{'html' if output_format == ReportFormat.HTML else 'pdf'}"
    # Wave: headers-dropped fix. FastAPI does NOT merge headers set on an
    # injected `response: Response` param into an endpoint's own returned
    # Response subclass (StreamingResponse here) - only `background` tasks
    # get merged across the two (see fastapi/routing.py's
    # `isinstance(raw_response, Response)` branch). Mutating `response.headers`
    # here was therefore a silent no-op in production: Content-Disposition,
    # X-Content-Type-Options and the CSP header never reached a real client.
    # Fix: build the headers dict up front and pass it to StreamingResponse's
    # constructor instead.
    headers = {
        "Content-Disposition": content_disposition_attachment(
            filename, fallback=default_name,
        ),
    }
    if output_format == ReportFormat.HTML:
        # HTML is executable in a browser - Content-Disposition: attachment
        # (never inline, unchanged above) is the main mitigation; these two
        # headers are defense-in-depth for a client that renders it anyway.
        headers["X-Content-Type-Options"] = "nosniff"
        headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
        )
        media_type = "text/html; charset=utf-8"
    else:
        media_type = "application/pdf"
    return StreamingResponse(
        storage.open_stream(storage_key), media_type=media_type, headers=headers,
    )
