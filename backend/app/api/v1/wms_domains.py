"""WMS/WFS domain allow-list management (Wave: multi-format layers, Part B).

Read (`GET`) is available to anyone who can upload at all (UPLOAD_ROLES) -
a GIS Associate/Analyst adding a WMS layer needs to see the approved list to
pick from it (a dropdown, never free text - enforced by the frontend AND,
more importantly, by create_external_layer's own server-side allow-list
check). Write (`POST`/`DELETE`) is Administrator-only
(MANAGE_WMS_SOURCES_ROLES) - growing or shrinking the set of domains this
backend will fetch from server-side is deliberately a narrower permission
than uploading itself."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import get_wms_service, require_role
from app.domain.dtos import AddWmsDomainRequest, CurrentUser, WmsDomainOut
from app.domain.enums import MANAGE_WMS_SOURCES_ROLES, UPLOAD_ROLES
from app.services.wms_service import WmsService

router = APIRouter(tags=["wms-domains"])


@router.get("/wms-domains", response_model=list[WmsDomainOut])
def list_wms_domains(
    user: Annotated[CurrentUser, Depends(require_role(*UPLOAD_ROLES))],
    svc: Annotated[WmsService, Depends(get_wms_service)],
) -> list[WmsDomainOut]:
    return svc.list_domains()


@router.post("/wms-domains", response_model=WmsDomainOut, status_code=201)
def add_wms_domain(
    body: AddWmsDomainRequest,
    user: Annotated[CurrentUser, Depends(require_role(*MANAGE_WMS_SOURCES_ROLES))],
    svc: Annotated[WmsService, Depends(get_wms_service)],
) -> WmsDomainOut:
    return svc.add_domain(body.domain, user)


@router.delete("/wms-domains/{domain_id}", status_code=204, response_model=None)
def remove_wms_domain(
    domain_id: UUID,
    user: Annotated[CurrentUser, Depends(require_role(*MANAGE_WMS_SOURCES_ROLES))],
    svc: Annotated[WmsService, Depends(get_wms_service)],
) -> None:
    svc.remove_domain(domain_id, user)
