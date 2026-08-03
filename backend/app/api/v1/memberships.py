"""
Project-membership endpoints (v1) - Wave: project-level RBAC.

Nested under /projects/{project_id}/members, the same project-scoped
sub-resource convention as .../kpis, .../layers, .../evolution. Every route
just needs a valid access token; the actual view-vs-manage decision is made
by MembershipService (Administrator bypass, or a live project_membership row
- see app.domain.authz), not by a `require_role(...)` dependency here - the
allowed set differs per PROJECT, which `require_role`'s global-only role
check has no way to express.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUserDep, get_membership_service
from app.domain.dtos import AddMemberRequest, MemberOut, ProjectMembers, UpdateMemberRoleRequest
from app.services.membership_service import MembershipService

router = APIRouter(tags=["memberships"])


@router.get("/projects/{project_id}/members", response_model=ProjectMembers)
def list_members(
    project_id: UUID,
    user: CurrentUserDep,
    svc: Annotated[MembershipService, Depends(get_membership_service)],
) -> ProjectMembers:
    return svc.list_members(project_id, user)


@router.post("/projects/{project_id}/members", response_model=MemberOut, status_code=201)
def add_member(
    project_id: UUID,
    body: AddMemberRequest,
    user: CurrentUserDep,
    svc: Annotated[MembershipService, Depends(get_membership_service)],
) -> MemberOut:
    return svc.add_member(project_id, body.username, body.role, user)


@router.delete("/projects/{project_id}/members/{user_id}", status_code=204, response_model=None)
def remove_member(
    project_id: UUID,
    user_id: UUID,
    user: CurrentUserDep,
    svc: Annotated[MembershipService, Depends(get_membership_service)],
) -> None:
    svc.remove_member(project_id, user_id, user)


@router.patch("/projects/{project_id}/members/{user_id}", response_model=MemberOut)
def update_member_role(
    project_id: UUID,
    user_id: UUID,
    body: UpdateMemberRoleRequest,
    user: CurrentUserDep,
    svc: Annotated[MembershipService, Depends(get_membership_service)],
) -> MemberOut:
    return svc.update_role(project_id, user_id, body.role, user)
