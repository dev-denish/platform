"""GEE analysis registry endpoints (Wave: GEE analysis registry; Wave:
vegetation indices adds the sync/async split below).

GET endpoints are open to any project member (require_project_view, same
tier as GET /projects/{id}/layers) - viewing a cached result costs nothing.
Refresh is gated the same way an upload is (require_project_upload, checked
inside the service): a global UPLOAD_ROLES gate here at the route, PLUS the
project-tier GIS-Associate-or-Administrator re-check the service already
does for every other project-scoped write.

refresh_analysis is `async def` (the other routes here are plain `def`,
offloaded to FastAPI's own thread pool automatically) because the "async"
-execution path (app/services/gee_analysis_service.py's enqueue_refresh)
awaits the job dispatch itself, same as POST /datasets/upload. The "sync"
-execution path's blocking GEE call is explicitly moved off the event loop
via asyncio.to_thread - an async route does NOT get FastAPI's automatic
thread-pool offload a plain `def` route would, so leaving that out here
would block every other request on this process for however long that
analysis takes."""
from __future__ import annotations

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from app.api.deps import (
    CurrentUserDep,
    get_gee_analysis_service,
    get_job_service,
    get_settings,
    get_task_runner,
    require_role,
)
from app.core.config import Settings
from app.domain.analysis_catalog import get_catalog_entry
from app.domain.dtos import (
    AnalysisCatalogEntryOut,
    AnalysisResultOut,
    CurrentUser,
    JobAccepted,
    ProjectAnalysisCatalog,
)
from app.domain.enums import UPLOAD_ROLES
from app.services.gee_analysis_service import GEEAnalysisService
from app.services.jobs_service import JobService
from app.workers.queue import TaskRunner

router = APIRouter(tags=["analyses"])


@router.get("/analysis-catalog", response_model=list[AnalysisCatalogEntryOut])
def get_analysis_catalog(
    user: CurrentUserDep,
    svc: Annotated[GEEAnalysisService, Depends(get_gee_analysis_service)],
) -> list[dict]:
    return svc.list_catalog()


@router.get("/projects/{project_id}/analyses", response_model=ProjectAnalysisCatalog)
def get_project_analyses(
    project_id: UUID,
    user: CurrentUserDep,
    svc: Annotated[GEEAnalysisService, Depends(get_gee_analysis_service)],
) -> ProjectAnalysisCatalog:
    return svc.get_project_analyses(project_id, user)


@router.get(
    "/projects/{project_id}/analyses/{analysis_id}", response_model=AnalysisResultOut
)
def get_analysis_result(
    project_id: UUID,
    analysis_id: str,
    user: CurrentUserDep,
    svc: Annotated[GEEAnalysisService, Depends(get_gee_analysis_service)],
) -> AnalysisResultOut:
    return svc.get_result(project_id, analysis_id, user)


@router.post(
    "/projects/{project_id}/analyses/{analysis_id}/refresh",
    response_model=AnalysisResultOut | JobAccepted,
    responses={202: {"model": JobAccepted, "description": "Queued - poll status_url."}},
)
async def refresh_analysis(
    project_id: UUID,
    analysis_id: str,
    user: Annotated[CurrentUser, Depends(require_role(*UPLOAD_ROLES))],
    svc: Annotated[GEEAnalysisService, Depends(get_gee_analysis_service)],
    jobs: Annotated[JobService, Depends(get_job_service)],
    runner: Annotated[TaskRunner, Depends(get_task_runner)],
    settings: Annotated[Settings, Depends(get_settings)],
    response: Response,
) -> AnalysisResultOut | JobAccepted:
    entry = get_catalog_entry(analysis_id)
    if entry is not None and entry.get("execution") == "async":
        job_id = await svc.enqueue_refresh(project_id, analysis_id, user, jobs, runner)
        response.status_code = 202
        return JobAccepted(job_id=job_id, status_url=f"{settings.api_v1_prefix}/jobs/{job_id}")
    # "sync"-execution (or unknown/not-yet-implemented, which svc.refresh()
    # itself 404s/422s) - off the event loop via to_thread, since this is an
    # async route now (see module docstring for why that matters here).
    return await asyncio.to_thread(svc.refresh, project_id, analysis_id, user)
