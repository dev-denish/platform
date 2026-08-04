"""Shared "find-or-create the target project, enforcing project-level upload
RBAC" logic (Wave: project-level RBAC). Originally lived only in
IngestionService._resolve_project; extracted here (Wave: multi-format layers)
because creating an external WMS/WFS layer is the same "upload-shaped"
action - find-or-create a named project, re-check UPLOAD_ROLES against the
PROJECT-level role, auto-add the creator as the project's first member - just
without a file to stage first. See IngestionService._resolve_project's
history for why this re-check exists at all (the endpoint's `require_role`
only gates entry to the flow globally, not per-project)."""
from __future__ import annotations

from uuid import UUID

import psycopg

from app.core.errors import ForbiddenError, NotFoundError
from app.domain.dtos import CurrentUser
from app.domain.enums import AuditAction, Role
from app.repositories.audit import AuditRepository
from app.repositories.memberships import ProjectMembershipRepository
from app.repositories.projects import ProjectRepository

# Wave: Reference Layer Library. One real, auto-created project every
# reference-layer dataset attaches to (dataset.project_id is NOT NULL) -
# purely a storage home, never surfaced as "the" reference library concept
# anywhere else; a layer is globally visible because of its OWN
# `is_reference` flag (see LayerRepository.list_for_project), not because of
# which project_id it happens to carry.
REFERENCE_LIBRARY_PROJECT_NAME = "Reference Layer Library"
REFERENCE_LIBRARY_PROJECT_REGION = "Global"


def resolve_reference_library_project(cur: psycopg.Cursor) -> UUID:
    """A reference layer isn't scoped to any one project's membership - every
    UPLOAD_ROLES actor may add one regardless of which projects they belong
    to (see MANAGE_REFERENCE_LAYERS_ROLES's own docstring for why removal is
    narrower). Unlike resolve_project_for_upload, this never checks or
    grants project membership - the endpoint's own `require_role(*UPLOAD_ROLES)`
    is the entire permission check for adding one. Reuses
    find_or_create_by_name so the shared project is the exact same atomic
    find-or-create every other project name goes through, not a special-cased
    row."""
    project_id, _created = ProjectRepository(cur).find_or_create_by_name(
        REFERENCE_LIBRARY_PROJECT_NAME, REFERENCE_LIBRARY_PROJECT_REGION
    )
    return project_id


def resolve_project_for_upload(
    cur: psycopg.Cursor, *, project_name: str, region: str, actor: CurrentUser
) -> UUID:
    project_id, created = ProjectRepository(cur).find_or_create_by_name(project_name, region)
    if actor.role == Role.ADMINISTRATOR:
        return project_id
    if created:
        ProjectMembershipRepository(cur).add(
            project_id=project_id, user_id=actor.user_id, role=actor.role,
            added_by=actor.user_id,
        )
        AuditRepository(cur).record(
            actor_id=actor.user_id, actor_name=actor.username,
            action=AuditAction.ADD_PROJECT_MEMBER,
            target=f"{project_id}:{actor.user_id}",
            detail=f"Auto-added as first member ({actor.role.value}) on project creation.",
            project_id=project_id,
        )
        return project_id
    role = ProjectMembershipRepository(cur).get_role(project_id, actor.user_id)
    if role is None:
        raise NotFoundError("Project not found.")
    if role != Role.GIS_ASSOCIATE:
        raise ForbiddenError("Uploading requires the GIS Associate role on this project.")
    return project_id
