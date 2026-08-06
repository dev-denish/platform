"""
Forest-definition threshold endpoints (v1) - Wave: permission grants, Part 2.

GET has no role/permission gate beyond being authenticated - every role
reads the same live value reports are built from. PUT has no route-level
gate either: ForestDefinitionService.update enforces
has_permission(actor, "edit_forest_definition") itself (Administrator or an
individually granted user), the same way project-scoped writes enforce
their own rule inside the service rather than via `require_role`.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUserDep, get_forest_definition_service
from app.domain.dtos import ForestDefinitionOut, UpdateForestDefinitionRequest
from app.services.forest_definition_service import ForestDefinitionService

router = APIRouter(tags=["forest-definition"])


@router.get("/forest-definition", response_model=ForestDefinitionOut)
def get_forest_definition(
    user: CurrentUserDep,
    svc: Annotated[ForestDefinitionService, Depends(get_forest_definition_service)],
) -> ForestDefinitionOut:
    return svc.get(user)


@router.put("/forest-definition", response_model=ForestDefinitionOut)
def update_forest_definition(
    body: UpdateForestDefinitionRequest,
    user: CurrentUserDep,
    svc: Annotated[ForestDefinitionService, Depends(get_forest_definition_service)],
) -> ForestDefinitionOut:
    return svc.update(body, user)
