"""
Per-user permission grant endpoints (v1) - Wave: permission grants.

Administrator-only (MANAGE_PERMISSIONS_ROLES), same `require_role` RBAC
dependency users.py already uses - this lives on the same Users screen.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import get_permission_service, require_role
from app.domain.dtos import CurrentUser, PermissionGrantOut, UserPermissions
from app.domain.enums import MANAGE_PERMISSIONS_ROLES
from app.services.permission_service import PermissionService

router = APIRouter(tags=["permissions"])


@router.get("/users/{user_id}/permissions", response_model=UserPermissions)
def list_user_permissions(
    user_id: UUID,
    user: Annotated[CurrentUser, Depends(require_role(*MANAGE_PERMISSIONS_ROLES))],
    svc: Annotated[PermissionService, Depends(get_permission_service)],
) -> UserPermissions:
    return svc.list_grants(user_id, user)


@router.put("/users/{user_id}/permissions/{permission_name}", response_model=PermissionGrantOut)
def grant_permission(
    user_id: UUID,
    permission_name: str,
    user: Annotated[CurrentUser, Depends(require_role(*MANAGE_PERMISSIONS_ROLES))],
    svc: Annotated[PermissionService, Depends(get_permission_service)],
) -> PermissionGrantOut:
    return svc.grant(user_id, permission_name, user)


@router.delete(
    "/users/{user_id}/permissions/{permission_name}", status_code=204, response_model=None
)
def revoke_permission(
    user_id: UUID,
    permission_name: str,
    user: Annotated[CurrentUser, Depends(require_role(*MANAGE_PERMISSIONS_ROLES))],
    svc: Annotated[PermissionService, Depends(get_permission_service)],
) -> None:
    svc.revoke(user_id, permission_name, user)
