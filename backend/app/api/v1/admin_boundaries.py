"""District picker (Wave: Admin Boundaries) - backs the dropdown that scopes
a Village layer's district_lgd_code queries (see layers.py's
GET /layers/{id}/geojson and GET /layers/{id}/village-coverage). Not
project-scoped: this is reference data, same visibility as any reference
layer (Wave: Reference Layer Library)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUserDep, get_admin_boundary_service
from app.domain.dtos import AdminDistrictOut
from app.services.admin_boundary_service import AdminBoundaryService

router = APIRouter(tags=["admin-boundaries"])


@router.get("/admin-boundaries/districts", response_model=list[AdminDistrictOut])
def list_districts(
    user: CurrentUserDep,
    svc: Annotated[AdminBoundaryService, Depends(get_admin_boundary_service)],
) -> list[AdminDistrictOut]:
    return [AdminDistrictOut(**row) for row in svc.list_districts()]
