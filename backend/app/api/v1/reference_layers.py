"""Removing a reference layer (Wave: Reference Layer Library). Adding one
goes through the existing /datasets/upload and /projects/{id}/external-layers
endpoints (just with `is_reference=true`) - there is no separate "create a
reference layer" route, per this wave's own instruction to reuse the
existing ingestion/WMS pipeline exactly rather than build a second one."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import get_reference_layer_service, require_role
from app.domain.dtos import CurrentUser
from app.domain.enums import MANAGE_REFERENCE_LAYERS_ROLES
from app.services.reference_layer_service import ReferenceLayerService

router = APIRouter(tags=["reference-layers"])


@router.delete("/reference-layers/{layer_id}", status_code=204, response_model=None)
def remove_reference_layer(
    layer_id: UUID,
    user: Annotated[CurrentUser, Depends(require_role(*MANAGE_REFERENCE_LAYERS_ROLES))],
    svc: Annotated[ReferenceLayerService, Depends(get_reference_layer_service)],
) -> None:
    svc.remove(layer_id, user)
