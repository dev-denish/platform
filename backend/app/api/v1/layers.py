"""Cross-layer-kind endpoints not scoped to a single project (Wave:
multi-format layers, Part A) - mirrors tiles.py's GET /layers/{id}/pixel,
which lives outside the /projects/{id}/... nesting for the same reason: a
layer_id alone is enough to resolve its project for the membership check, no
project_id needed in the path."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import (
    CurrentUserDep,
    get_class_legend_service,
    get_layer_rename_service,
    get_vector_layer_service,
    require_role,
)
from app.domain.dtos import (
    ClassLegendUpdateResult,
    CurrentUser,
    LayerRenameResult,
    RenameLayerRequest,
    UpdateClassLegendRequest,
    VillageCoverageOut,
)
from app.domain.enums import RENAME_LAYER_ROLES, UPLOAD_ROLES
from app.services.layer_rename_service import LayerRenameService
from app.services.legend_service import ClassLegendService
from app.services.vector_layer_service import VectorLayerService

router = APIRouter(tags=["layers"])


@router.get("/layers/{layer_id}/geojson")
def get_layer_geojson(
    layer_id: UUID,
    user: CurrentUserDep,
    svc: Annotated[VectorLayerService, Depends(get_vector_layer_service)],
    district_lgd_code: Annotated[str | None, Query()] = None,
) -> dict:
    # Wave: Admin Boundaries. Every other vector layer ignores this param
    # (as before); the Village layer requires it - see
    # VectorLayerService.get_geojson.
    return svc.get_geojson(layer_id, user, district_lgd_code)


@router.get("/layers/{layer_id}/village-coverage", response_model=VillageCoverageOut)
def get_village_coverage(
    layer_id: UUID,
    district_lgd_code: Annotated[str, Query()],
    user: CurrentUserDep,
    svc: Annotated[VectorLayerService, Depends(get_vector_layer_service)],
) -> VillageCoverageOut:
    """Wave: Admin Boundaries. The "boundary not available" list for one
    district's Village layer - official registry vs. what actually has a
    polygon, not the aggregate coverage percentage from the sourcing
    research."""
    return VillageCoverageOut(**svc.get_village_coverage(layer_id, user, district_lgd_code))


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


@router.patch("/layers/{layer_id}/display-name", response_model=LayerRenameResult)
def rename_layer(
    layer_id: UUID,
    body: RenameLayerRequest,
    user: Annotated[CurrentUser, Depends(require_role(*RENAME_LAYER_ROLES))],
    svc: Annotated[LayerRenameService, Depends(get_layer_rename_service)],
) -> LayerRenameResult:
    """Rename-a-layer: sets a cosmetic display_name override, shown by the
    frontend instead of the raw type/source label. Administrator-only,
    global gate - no project-tier fallback (see RENAME_LAYER_ROLES)."""
    return svc.rename(layer_id, body.display_name, user)
