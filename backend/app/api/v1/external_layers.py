"""External WMS/WFS layer creation + fetch-time proxy (Wave: multi-format
layers, Part B).

`create_external_layer` is a normal authenticated (Bearer) endpoint, gated by
UPLOAD_ROLES exactly like `/datasets/upload` - Part B's design explicitly
allows a GIS Associate/Analyst to ADD a layer from an approved domain, just
never to name a domain that isn't already approved (enforced inside
WmsService.create_external_layer, re-checking the live allow-list, not
trusting anything client-supplied).

`get_wms_tile`/`get_wfs_features` are the proxy routes an already-authorized
tile/feature request travels through - same signed short-lived tile-token
scheme as tiles.py's `get_tile` (a `<img>`/tileLayer-style request can't carry
an Authorization header), and for the identical reason documented there: the
authorization decision was already made once, at mint time, by
ProjectService.get_layers. What happens on EVERY request here, unconditionally,
is the allow-list + private-IP re-validation inside WmsService/external_fetch -
that is Part B's actual security boundary, not the token."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.deps import get_wms_service, require_role
from app.domain.dtos import CreateExternalLayerRequest, CurrentUser
from app.domain.enums import UPLOAD_ROLES
from app.services.wms_service import WmsService

router = APIRouter(tags=["external-layers"])

_WMS_TILE_CACHE_CONTROL = "no-store"  # a live third-party server's tile - never ours to cache


@router.post("/projects/{project_id}/external-layers", status_code=202)
def create_external_layer(
    project_id: UUID,
    body: CreateExternalLayerRequest,
    user: Annotated[CurrentUser, Depends(require_role(*UPLOAD_ROLES))],
    svc: Annotated[WmsService, Depends(get_wms_service)],
) -> dict:
    # project_id in the path is purely for REST-path consistency with
    # /projects/{id}/layers (Leaflet/the frontend never needs it directly -
    # project resolution is by NAME, exactly like /datasets/upload, since
    # WmsService.create_external_layer shares that find-or-create path);
    # it plays no role in the actual creation logic below.
    layer_id = svc.create_external_layer(
        project_name=body.project_name, region=body.region, domain=body.domain,
        service_kind=body.service_kind, path=body.path, layer_name=body.layer_name,
        actor=user, is_reference=body.is_reference,
    )
    return {"layer_id": str(layer_id)}


@router.get("/external-layers/{layer_id}/wms")
def get_wms_tile(
    layer_id: UUID,
    request: Request,
    token: Annotated[str, Query(...)],
    svc: Annotated[WmsService, Depends(get_wms_service)],
) -> Response:
    query_params = dict(request.query_params)
    body, content_type = svc.fetch_wms_tile(layer_id, token, query_params)
    headers = {"Cache-Control": _WMS_TILE_CACHE_CONTROL}
    return Response(content=body, media_type=content_type, headers=headers)


@router.get("/external-layers/{layer_id}/wfs")
def get_wfs_features(
    layer_id: UUID,
    token: Annotated[str, Query(...)],
    svc: Annotated[WmsService, Depends(get_wms_service)],
) -> Response:
    body = svc.fetch_wfs_features(layer_id, token)
    headers = {"Cache-Control": _WMS_TILE_CACHE_CONTROL}
    return Response(content=body, media_type="application/json", headers=headers)
