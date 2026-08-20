"""Domain enumerations. Single source of truth for the vocabulary the platform
speaks. Used by validation (DTOs), authorization (roles), and the DB check
constraints in the Alembic migration - so an invalid value cannot enter through
the API layer OR be written directly to the database."""
from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMINISTRATOR = "Administrator"
    GIS_ASSOCIATE = "GIS Associate"
    ANALYST = "Analyst"
    VERIFIER = "Verifier"
    VIEWER = "Viewer"


# Roles allowed to ingest/upload datasets. Centralised so the rule is defined once
# (the MVP re-implemented this check inline and diverged from its own helper).
# For a project-scoped upload this is re-checked against the PROJECT-level role
# (see IngestionService._resolve_project), not the actor's global one - this
# frozenset still gates entry to the endpoint at all.
UPLOAD_ROLES: frozenset[Role] = frozenset({Role.ADMINISTRATOR, Role.GIS_ASSOCIATE})

# Roles allowed to (soft-)delete a project. Administrator only.
DELETE_PROJECT_ROLES: frozenset[Role] = frozenset({Role.ADMINISTRATOR})

# Roles allowed to create/list/deactivate accounts (Wave: User Management).
# Administrator only - unlike project membership, there is no per-project
# variant of this permission to check.
MANAGE_USERS_ROLES: frozenset[Role] = frozenset({Role.ADMINISTRATOR})

# Wave: project-level RBAC. Valid values for a project_membership row's role.
# Administrator is deliberately excluded - it's a global-only concept
# (app_user.role): an Administrator bypasses membership checks entirely
# (see app.domain.authz), so there is never a per-project row to hold that
# value, and one must never be written.
PROJECT_ROLES: frozenset[Role] = frozenset(
    {Role.GIS_ASSOCIATE, Role.ANALYST, Role.VERIFIER, Role.VIEWER}
)

# Roles allowed to add/remove/change a project's membership. Checked against
# the PROJECT-level role for that specific project (or global Administrator,
# who bypasses) - see app.domain.authz.require_project_manage. Membership
# management is itself gated by "is this a project-scoped GIS Associate",
# not one of the four PROJECT_ROLES values in general.
MANAGE_MEMBERS_ROLES: frozenset[Role] = frozenset({Role.GIS_ASSOCIATE})

# Wave: multi-format layers. Who may manage the WMS/WFS domain allow-list -
# Administrator only, deliberately narrower than UPLOAD_ROLES: a GIS
# Associate can ADD a WMS/WFS layer (picking from this list, see
# UPLOAD_ROLES reuse in external_layers.py) but must never be able to grow
# the list of domains the backend is willing to fetch from server-side -
# that's the entire point of Part B's allow-list design.
MANAGE_WMS_SOURCES_ROLES: frozenset[Role] = frozenset({Role.ADMINISTRATOR})

# Wave: Reference Layer Library. ADDING a reference layer reuses UPLOAD_ROLES
# unchanged (any Administrator/GIS Associate - same as any other upload,
# see reference_layer's own docstrings for why no new permission is needed
# there). REMOVING one is narrower, Administrator only - mirroring
# DELETE_PROJECT_ROLES/MANAGE_WMS_SOURCES_ROLES above: a shared resource
# visible on every project gets the same asymmetry (broad to add, narrow to
# take away) this codebase already applies to the WMS/WFS allow-list.
MANAGE_REFERENCE_LAYERS_ROLES: frozenset[Role] = frozenset({Role.ADMINISTRATOR})

# Rename-a-layer (display_name override). Administrator only, global gate -
# unlike UPLOAD_ROLES/ClassLegendService there is no project-tier fallback
# here: this is a purely cosmetic label edit, not a data-modifying action a
# project's own GIS Associate needs day-to-day, so it doesn't get the same
# two-tier check IngestionService/ClassLegendService apply. A separate
# constant from MANAGE_REFERENCE_LAYERS_ROLES even though identical today,
# matching this codebase's one-named-capability-per-concept convention (see
# MANAGE_USERS_ROLES above).
RENAME_LAYER_ROLES: frozenset[Role] = frozenset({Role.ADMINISTRATOR})

# Delete-a-dataset: a formal, project-scoped upload (not a reference layer or
# an ad-hoc quick-add - those keep their own removal paths and roles).
# Administrator only, global gate, same reasoning as RENAME_LAYER_ROLES: this
# permanently removes a file a project's own GIS Associate can't get back,
# not a routine day-to-day action that needs the two-tier upload check. A
# separate constant from RENAME_LAYER_ROLES even though identical today,
# matching this file's one-named-capability-per-concept convention.
DELETE_DATASET_ROLES: frozenset[Role] = frozenset({Role.ADMINISTRATOR})

# Wave: permission grants. Who may view/grant/revoke another user's
# individual permission grants - Administrator only, same global gate as
# MANAGE_USERS_ROLES (this is part of the same Users screen). Distinct from
# `has_permission()` (app.domain.permissions), which decides who may USE a
# granted permission, not who may grant one.
MANAGE_PERMISSIONS_ROLES: frozenset[Role] = frozenset({Role.ADMINISTRATOR})


class LayerKind(StrEnum):
    RASTER = "raster"
    VECTOR = "vector"
    EXTERNAL_WMS = "external_wms"
    EXTERNAL_WFS = "external_wfs"


class DatasetType(StrEnum):
    LULC = "LULC"
    NDVI = "NDVI"
    BIOMASS = "Biomass"
    BOUNDARY = "Boundary"
    # UI-only convenience label around the same legend-driven ingestion path as
    # LULC (raster.py branches on legend presence, not dataset_type): a raw
    # scene with no class_legend gets band_stats, one with a legend gets
    # class_stats - identical to any other type.
    SATELLITE = "Satellite / Raw Imagery"


class ProjectStatus(StrEnum):
    ACTIVE = "Active"
    ARCHIVED = "Archived"
    UNDER_REVIEW = "Under Review"


class ReportType(StrEnum):
    """Wave: ai-report-narrative, Phase 3. `SYSTEM` sources every section's
    summary from `stats["summary"]` (index_summary.py, deterministic, 5/13
    analysis types only) same as before this wave; `AI` sources it instead
    from `ai_narrative.generate_ai_summaries` for every section, plus a fixed
    disclosure line on the cover page (see
    report_pdf.AI_NARRATIVE_DISCLOSURE_TEMPLATE). Maps/charts/stats/note/
    description/disclaimer are identical either way."""

    SYSTEM = "system"
    AI = "ai"


class ReportFormat(StrEnum):
    """Wave: HTML report rendering. The output ARTIFACT format for a
    generated report - orthogonal to `ReportType` above (which decides
    whether a section's narrative is system- or AI-sourced content, not
    what container it's laid out into). One `generate_report` job produces
    exactly one artifact in exactly one of these formats; the two axes are
    independent (an AI report can be rendered as PDF or HTML, and so can a
    system report) and are threaded through the job/download path as two
    separate fields, never combined into one enum. Does not affect
    `GenerateReportRequest.analysis_ids` or its max-13 cap - that limit is
    about how many analyses one report may cover, unrelated to which file
    format the result is written into."""

    PDF = "pdf"
    HTML = "html"


class AuditAction(StrEnum):
    LOGIN = "login"
    INGEST_DATASET = "ingest_dataset"
    CREATE_PROJECT = "create_project"
    DELETE_DATASET = "delete_dataset"
    DELETE_PROJECT = "delete_project"
    ADD_PROJECT_MEMBER = "add_project_member"
    REMOVE_PROJECT_MEMBER = "remove_project_member"
    UPDATE_PROJECT_MEMBER_ROLE = "update_project_member_role"
    CREATE_USER = "create_user"
    DEACTIVATE_USER = "deactivate_user"
    ACTIVATE_USER = "activate_user"
    HIDE_USER = "hide_user"
    UNHIDE_USER = "unhide_user"
    PERMANENTLY_DELETE_USER = "permanently_delete_user"
    RESET_USER_PASSWORD = "reset_user_password"  # noqa: S105 - an action name, not a credential
    CHANGE_OWN_PASSWORD = "change_own_password"  # noqa: S105 - an action name, not a credential
    ADD_WMS_DOMAIN = "add_wms_domain"
    REMOVE_WMS_DOMAIN = "remove_wms_domain"
    CREATE_EXTERNAL_LAYER = "create_external_layer"
    UPDATE_CLASS_LEGEND = "update_class_legend"
    RENAME_LAYER = "rename_layer"
    GRANT_PERMISSION = "grant_permission"
    REVOKE_PERMISSION = "revoke_permission"
    UPDATE_FOREST_DEFINITION = "update_forest_definition"
    REFRESH_ANALYSIS = "refresh_analysis"
    GENERATE_REPORT = "generate_report"
