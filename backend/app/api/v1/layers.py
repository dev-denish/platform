"""Cross-layer-kind endpoints not scoped to a single project (Wave:
multi-format layers, Part A) - mirrors tiles.py's GET /layers/{id}/pixel,
which lives outside the /projects/{id}/... nesting for the same reason: a
layer_id alone is enough to resolve its project for the membership check, no
project_id needed in the path."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import (
    CurrentUserDep,
    get_class_legend_service,
    get_vector_layer_service,
    require_role,
)
from app.domain.dtos import ClassLegendUpdateResult, CurrentUser, UpdateClassLegendRequest
from app.domain.enums import UPLOAD_ROLES
from app.services.legend_service import ClassLegendService
from app.services.vector_layer_service import VectorLayerService

router = APIRouter(tags=["layers"])


@router.get("/layers/{layer_id}/geojson")
def get_layer_geojson(
    layer_id: UUID,
    user: CurrentUserDep,
    svc: Annotated[VectorLayerService, Depends(get_vector_layer_service)],
) -> dict:
    return svc.get_geojson(layer_id, user)


@router.patch("/layers/{layer_id}/class-legend", response_model=ClassLegendUpdateResult)
def update_class_legend(
    layer_id: UUID,
    body: UpdateClassLegendRequest,
    user: Annotated[CurrentUser, Depends(require_role(*UPLOAD_ROLES))],
    svc: Annotated[ClassLegendService, Depends(get_class_legend_service)],
) -> ClassLegendUpdateResult:
    """Wave: editable class legend. Global gate here (Administrator or GIS
    Associate, same as any other layer-modifying action - upload, ad-hoc
    layer add/remove); the service re-checks the PROJECT-level role this
    specific layer belongs to, same two-tier pattern as
    IngestionService/AdhocLayerService."""
    return svc.update_legend(layer_id, body.class_legend, user)
