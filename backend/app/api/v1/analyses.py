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
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

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
    AnalysisPointValue,
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


def _analysis_config_query_params(
    # Wave: analysis config and methodology. Meaningful only for the 7
    # configurable ids (see analysis_catalog.py's own `config` field) -
    # every other analysis_id's `GEEAnalysisService.get_result`/
    # `_prepare_refresh` ignores whatever's here, unchanged, same as the
    # pre-existing `year` param below already does for anything but the 3
    # browse ids. A shared FastAPI sub-dependency (not 8 repeated Query()
    # declarations) since both GET and POST /refresh accept the identical
    # set - resolution/validation itself happens once, in
    # `GEEAnalysisService`/`analysis_config.resolve_and_validate`, not here.
    year_mode: Annotated[str | None, Query()] = None,
    year: Annotated[int | None, Query()] = None,
    year_start: Annotated[int | None, Query()] = None,
    year_end: Annotated[int | None, Query()] = None,
    season_start: Annotated[str | None, Query()] = None,
    season_end: Annotated[str | None, Query()] = None,
    imagery_source: Annotated[str | None, Query()] = None,
    cloud_masking: Annotated[str | None, Query()] = None,
) -> dict[str, Any] | None:
    raw = {
        "year_mode": year_mode, "year": year, "year_start": year_start, "year_end": year_end,
        "season_start": season_start, "season_end": season_end,
        "imagery_source": imagery_source, "cloud_masking": cloud_masking,
    }
    filtered = {k: v for k, v in raw.items() if v is not None}
    return filtered or None


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
    config_params: Annotated[dict[str, Any] | None, Depends(_analysis_config_query_params)],
) -> AnalysisResultOut:
    return svc.get_result(project_id, analysis_id, user, config_params)


@router.get(
    "/projects/{project_id}/analyses/{analysis_id}/point", response_model=AnalysisPointValue
)
def get_analysis_point_value(
    project_id: UUID,
    analysis_id: str,
    user: CurrentUserDep,
    svc: Annotated[GEEAnalysisService, Depends(get_gee_analysis_service)],
    lon: Annotated[float, Query(ge=-180, le=180)],
    lat: Annotated[float, Query(ge=-90, le=90)],
    # Wave: raw-imagery browsing. Only meaningful for the 3 browse ids -
    # every other analysis_id ignores it. MUST match whatever `year` the
    # caller's currently-displayed tile was last refreshed with (see
    # GEEAnalysisService.get_point_value's own comment) or a click could
    # sample a different year than the one the map tile visually shows.
    # No `le=` upper bound: unlike `_current_veg_index_years()` (which this
    # module docstring elsewhere warns must be evaluated fresh per call, not
    # frozen at import time), a `le=date.today().year` HERE would be baked
    # into this route's signature once at import time and never refresh for
    # the life of the process - a future year is harmless anyway (the
    # underlying ImageCollection just has no scenes yet).
    year: Annotated[int | None, Query(ge=2013)] = None,
) -> AnalysisPointValue:
    """Identify-tool support (GET /layers/{id}/pixel's GEE-layer analog): a
    normal `def` route (not `async def`) so FastAPI's automatic thread-pool
    offload applies to the blocking GEE call inside, same reasoning as
    GET /layers/{id}/pixel and unlike refresh_analysis below (see that
    route's own comment)."""
    return svc.get_point_value(project_id, analysis_id, lon, lat, user, year=year)


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
    # One shared `request_params` source for both the 3 browse ids' `year`
    # (Wave: raw-imagery browsing) and the 7 configurable ids' full param set
    # (Wave: analysis config and methodology) - both read the SAME `?year=`
    # wire key for "which year", so this is one query param, not two
    # differently-validated ones bound to the same name. Each analysis_id's
    # own dispatch/resolver reads only the keys it recognizes and ignores
    # the rest, same "ignore what doesn't apply" convention `_compute`
    # already used for `year` before this dependency existed.
    config_params: Annotated[dict[str, Any] | None, Depends(_analysis_config_query_params)],
) -> AnalysisResultOut | JobAccepted:
    entry = get_catalog_entry(analysis_id)
    if entry is not None and entry.get("execution") == "async":
        job_id = await svc.enqueue_refresh(
            project_id, analysis_id, user, jobs, runner, config_params
        )
        response.status_code = 202
        return JobAccepted(job_id=job_id, status_url=f"{settings.api_v1_prefix}/jobs/{job_id}")
    # "sync"-execution (or unknown/not-yet-implemented, which svc.refresh()
    # itself 404s/422s) - off the event loop via to_thread, since this is an
    # async route now (see module docstring for why that matters here).
    return await asyncio.to_thread(svc.refresh, project_id, analysis_id, user, config_params)
