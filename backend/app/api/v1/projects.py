"""Project + analytics read endpoints (v1). Every route requires a valid access
token. Listing is paginated (limit/offset with sane bounds). Shapes are typed
response models published in OpenAPI. Meaning is identical to the MVP's endpoints."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUserDep, get_project_service, require_role
from app.domain.dtos import (
    CurrentUser,
    Page,
    ProjectDetail,
    ProjectEvolution,
    ProjectKpis,
    ProjectLayers,
    ProjectSummary,
)
from app.domain.enums import DELETE_PROJECT_ROLES
from app.services.project_service import ProjectService

router = APIRouter(tags=["projects"])


@router.get("/projects", response_model=Page[ProjectSummary])
def list_projects(
    _user: CurrentUserDep,
    svc: Annotated[ProjectService, Depends(get_project_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ProjectSummary]:
    return svc.list_projects(limit, offset)


@router.get("/projects/{project_id}", response_model=ProjectDetail)
def get_project(
    project_id: UUID,
    _user: CurrentUserDep,
    svc: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectDetail:
    return svc.get_project(project_id)


@router.get("/projects/{project_id}/kpis", response_model=ProjectKpis)
def get_kpis(
    project_id: UUID,
    _user: CurrentUserDep,
    svc: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectKpis:
    return svc.get_kpis(project_id)


@router.get("/projects/{project_id}/layers", response_model=ProjectLayers)
def get_layers(
    project_id: UUID,
    _user: CurrentUserDep,
    svc: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectLayers:
    return svc.get_layers(project_id)


@router.get("/projects/{project_id}/evolution", response_model=ProjectEvolution)
def get_evolution(
    project_id: UUID,
    _user: CurrentUserDep,
    svc: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectEvolution:
    """Phase 3 Wave G: land-class change across the project's real
    classified dated layers. `applicable=False` (not a 404/422) when fewer
    than 2 eligible dates exist - see ProjectService.get_evolution."""
    return svc.get_evolution(project_id)


@router.get("/summary")
def portfolio_summary(
    _user: CurrentUserDep,
    svc: Annotated[ProjectService, Depends(get_project_service)],
) -> dict:
    return svc.portfolio_summary()


@router.delete("/projects/{project_id}", status_code=204, response_model=None)
def delete_project(
    project_id: UUID,
    user: Annotated[CurrentUser, Depends(require_role(*DELETE_PROJECT_ROLES))],
    svc: Annotated[ProjectService, Depends(get_project_service)],
) -> None:
    svc.delete_project(project_id, user)
