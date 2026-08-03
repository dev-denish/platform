"""
Project-membership authorization (Wave: project-level RBAC).

Existing implementation: any authenticated user could read any project by UUID,
regardless of role - `require_role` only ever gated a GLOBAL action (upload,
delete), never "can THIS user see THIS project".

This module is the single place that decision gets made, reused by every
service exposing project-scoped data (ProjectService, MembershipService,
TileService, IngestionService's upload path) - the same "define the rule
once" principle app.api.deps's `require_role` already follows for global
RBAC.

Rules, exactly as decided for this wave:
  * Administrator bypasses every membership check unconditionally - NO
    project_membership row lookup is made for them at all, by design (not
    merely as an optimization: an Administrator is never stored as a member
    of anything, so a lookup would only ever come back empty).
  * Every other role needs a live (non-removed) project_membership row before
    it can see OR act on a project at all. No row -> the same 404 a
    nonexistent or soft-deleted project already gets (see
    ProjectRepository.get) - never a 403, which would leak that the project
    exists at all to someone who can't see it.
  * A user's role can differ per project; once a membership row exists, EVERY
    project-scoped permission check uses THAT row's role, never the account's
    global app_user.role (which remains only the account-wide default -
    Administrator-bypass and a sensible prefill when someone's first added to
    a project).
  * Managing a project's membership (add/remove/change-role) additionally
    requires that project-level role to be GIS Associate - or, again,
    Administrator.
"""
from __future__ import annotations

from uuid import UUID

import psycopg

from app.core.errors import ForbiddenError, NotFoundError
from app.domain.dtos import CurrentUser
from app.domain.enums import MANAGE_MEMBERS_ROLES, Role
from app.repositories.memberships import ProjectMembershipRepository
from app.repositories.projects import ProjectRepository


def require_project_view(
    cur: psycopg.Cursor, project_id: UUID, user: CurrentUser
) -> Role | None:
    """Raise NotFoundError unless `user` may see this project; otherwise
    return their PROJECT-level role, or None for an Administrator (there is
    no per-project row to return a role from - they bypass entirely).

    Checks existence first so a genuinely missing/soft-deleted project 404s
    the same way for an Administrator as for anyone else; only THEN does a
    non-Administrator additionally need a live membership row.
    """
    if not ProjectRepository(cur).get(project_id):
        raise NotFoundError("Project not found.")
    if user.role == Role.ADMINISTRATOR:
        return None
    role = ProjectMembershipRepository(cur).get_role(project_id, user.user_id)
    if role is None:
        raise NotFoundError("Project not found.")
    return role


def require_project_manage(cur: psycopg.Cursor, project_id: UUID, user: CurrentUser) -> None:
    """Who may add/remove/change a project's membership: a global
    Administrator, or a user whose PROJECT-level role on THIS project is
    already GIS Associate - never the account's global role. Builds on
    `require_project_view` so someone with no visibility into the project at
    all gets the identical 404 any other project-scoped read gives them,
    rather than a 403 that would confirm the project's existence."""
    role = require_project_view(cur, project_id, user)
    if role is not None and role not in MANAGE_MEMBERS_ROLES:
        raise ForbiddenError(
            "Managing project membership requires the GIS Associate role on this project."
        )


def require_project_upload(cur: psycopg.Cursor, project_id: UUID, user: CurrentUser) -> None:
    """Wave 3 (Added Layers): the same project-scoped re-check
    resolve_project_for_upload already applies to a name-based formal
    upload (find-or-create by name, then GIS-Associate-or-Administrator),
    just against a project that's already known by id - an ad-hoc layer is
    always added to an already-open, already-existing project, never a
    freshly-named one, so there's no find-or-create step here at all. See
    IngestionService._resolve_project's project_id branch, the only caller."""
    role = require_project_view(cur, project_id, user)
    if role is not None and role != Role.GIS_ASSOCIATE:
        raise ForbiddenError("Uploading requires the GIS Associate role on this project.")
