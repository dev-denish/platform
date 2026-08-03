"""
User-account management endpoints (v1) - Wave: User Management.

Administrator-only (MANAGE_USERS_ROLES), same `require_role` RBAC dependency
every other global-role-gated route (upload, delete-project) already uses -
there is no per-project variant of this permission to check, unlike
memberships.py.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_user_service, require_role
from app.domain.dtos import (
    AdminResetPasswordRequest,
    BulkDeleteRequest,
    BulkDeleteResult,
    CreateUserRequest,
    CurrentUser,
    Page,
    UserOut,
)
from app.domain.enums import MANAGE_USERS_ROLES
from app.services.user_service import UserService

router = APIRouter(tags=["users"])


@router.get("/users", response_model=Page[UserOut])
def list_users(
    _user: Annotated[CurrentUser, Depends(require_role(*MANAGE_USERS_ROLES))],
    svc: Annotated[UserService, Depends(get_user_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_hidden: Annotated[
        bool, Query(description="Include hidden accounts (Wave: three-tier removal).")
    ] = False,
) -> Page[UserOut]:
    return svc.list_users(limit, offset, include_hidden=include_hidden)


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    body: CreateUserRequest,
    user: Annotated[CurrentUser, Depends(require_role(*MANAGE_USERS_ROLES))],
    svc: Annotated[UserService, Depends(get_user_service)],
) -> UserOut:
    return svc.create_user(body.username, body.password, body.role, user)


@router.delete("/users/{user_id}", status_code=204, response_model=None)
def deactivate_user(
    user_id: UUID,
    user: Annotated[CurrentUser, Depends(require_role(*MANAGE_USERS_ROLES))],
    svc: Annotated[UserService, Depends(get_user_service)],
) -> None:
    svc.deactivate_user(user_id, user)


@router.post("/users/{user_id}/activate", status_code=204, response_model=None)
def activate_user(
    user_id: UUID,
    user: Annotated[CurrentUser, Depends(require_role(*MANAGE_USERS_ROLES))],
    svc: Annotated[UserService, Depends(get_user_service)],
) -> None:
    svc.activate_user(user_id, user)


@router.post("/users/{user_id}/hide", status_code=204, response_model=None)
def hide_user(
    user_id: UUID,
    user: Annotated[CurrentUser, Depends(require_role(*MANAGE_USERS_ROLES))],
    svc: Annotated[UserService, Depends(get_user_service)],
) -> None:
    svc.hide_user(user_id, user)


@router.post("/users/{user_id}/unhide", status_code=204, response_model=None)
def unhide_user(
    user_id: UUID,
    user: Annotated[CurrentUser, Depends(require_role(*MANAGE_USERS_ROLES))],
    svc: Annotated[UserService, Depends(get_user_service)],
) -> None:
    svc.unhide_user(user_id, user)


@router.delete("/users/{user_id}/permanent", status_code=204, response_model=None)
def permanently_delete_user(
    user_id: UUID,
    user: Annotated[CurrentUser, Depends(require_role(*MANAGE_USERS_ROLES))],
    svc: Annotated[UserService, Depends(get_user_service)],
) -> None:
    svc.permanent_delete_user(user_id, user)


@router.post("/users/bulk-permanent-delete", response_model=BulkDeleteResult)
def bulk_permanently_delete_users(
    body: BulkDeleteRequest,
    user: Annotated[CurrentUser, Depends(require_role(*MANAGE_USERS_ROLES))],
    svc: Annotated[UserService, Depends(get_user_service)],
) -> BulkDeleteResult:
    return BulkDeleteResult(results=svc.bulk_permanent_delete_users(body.ids, user))


@router.post("/users/{user_id}/reset-password", status_code=204, response_model=None)
def reset_user_password(
    user_id: UUID,
    body: AdminResetPasswordRequest,
    user: Annotated[CurrentUser, Depends(require_role(*MANAGE_USERS_ROLES))],
    svc: Annotated[UserService, Depends(get_user_service)],
) -> None:
    svc.admin_reset_password(user_id, body.password, user)
