"""
Per-user permission grants (Wave: permission grants) - separate from
role-based RBAC (app.domain.enums.Role / app.domain.authz).

A person can hold zero or more named grants regardless of their role. This
module is the ONE place "who may do X" is decided for a grant-gated feature:
every future permission-gated feature calls `has_permission()`, not a
bespoke check - same "define the rule once" principle app.domain.authz
already follows for project-membership RBAC.

Adding a second grantable permission is a `PERMISSION_REGISTRY` entry here,
never new UI or endpoint code - see UsersPage's permissions panel, which
renders this list generically.
"""
from __future__ import annotations

from dataclasses import dataclass

import psycopg

from app.domain.dtos import CurrentUser
from app.domain.enums import Role
from app.repositories.permission_grants import PermissionGrantRepository


@dataclass(frozen=True)
class PermissionDef:
    name: str
    label: str
    description: str


PERMISSION_REGISTRY: list[PermissionDef] = [
    PermissionDef(
        name="edit_forest_definition",
        label="Edit forest-definition threshold",
        description=(
            "Change the canopy cover, minimum height, and minimum area values "
            "that define a forest for this platform's reports."
        ),
    ),
]

PERMISSION_NAMES: frozenset[str] = frozenset(p.name for p in PERMISSION_REGISTRY)


def has_permission(cur: psycopg.Cursor, user: CurrentUser, permission_name: str) -> bool:
    """True if `user` may perform `permission_name` - an Administrator
    unconditionally (same global-bypass convention as
    app.domain.authz.require_project_view), or anyone individually granted
    it. The one function every permission-gated feature calls."""
    if user.role == Role.ADMINISTRATOR:
        return True
    return PermissionGrantRepository(cur).has_grant(user.user_id, permission_name)
