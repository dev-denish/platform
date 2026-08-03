"""Cross-layer-kind endpoints not scoped to a single project (Wave:
multi-format layers, Part A) - mirrors tiles.py's GET /layers/{id}/pixel,
which lives outside the /projects/{id}/... nesting for the same reason: a
layer_id alone is enough to resolve its project for the membership check, no
project_id needed in the path."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUserDep, get_vector_layer_service
from app.services.vector_layer_service import VectorLayerService

router = APIRouter(tags=["layers"])


@router.get("/layers/{layer_id}/geojson")
def get_layer_geojson(
    layer_id: UUID,
    user: CurrentUserDep,
    svc: Annotated[VectorLayerService, Depends(get_vector_layer_service)],
) -> dict:
    return svc.get_geojson(layer_id, user)
