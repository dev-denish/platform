"""GEE analysis registry endpoints (Wave: GEE analysis registry).

GET endpoints are open to any project member (require_project_view, same
tier as GET /projects/{id}/layers) - viewing a cached result costs nothing.
Refresh is gated the same way an upload is (require_project_upload, checked
inside the service): a global UPLOAD_ROLES gate here at the route, PLUS the
project-tier GIS-Associate-or-Administrator re-check the service already
does for every other project-scoped write."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUserDep, get_gee_analysis_service, require_role
from app.domain.dtos import (
    AnalysisCatalogEntryOut,
    AnalysisResultOut,
    CurrentUser,
    ProjectAnalysisCatalog,
)
from app.domain.enums import UPLOAD_ROLES
from app.services.gee_analysis_service import GEEAnalysisService

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
    "/projects/{project_id}/analyses/{analysis_id}/refresh", response_model=AnalysisResultOut
)
def refresh_analysis(
    project_id: UUID,
    analysis_id: str,
    user: Annotated[CurrentUser, Depends(require_role(*UPLOAD_ROLES))],
    svc: Annotated[GEEAnalysisService, Depends(get_gee_analysis_service)],
) -> AnalysisResultOut:
    return svc.refresh(project_id, analysis_id, user)
