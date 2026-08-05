/**
 * Mirrors app/domain/enums.py so the UI's notion of "who can do what" never
 * drifts from the API's. The API is still the enforcement point (403 on a
 * disallowed upload) - this only controls what the UI offers, for a cleaner
 * experience, not as a security boundary.
 */
export const ROLES = {
  ADMINISTRATOR: "Administrator",
  GIS_ASSOCIATE: "GIS Associate",
  ANALYST: "Analyst",
  VERIFIER: "Verifier",
  VIEWER: "Viewer",
};

export const UPLOAD_ROLES = new Set([ROLES.ADMINISTRATOR, ROLES.GIS_ASSOCIATE]);

export function canUpload(role) {
  return UPLOAD_ROLES.has(role);
}

export function canDeleteProject(role) {
  return role === ROLES.ADMINISTRATOR;
}

// Wave: User Management. Administrator-only, mirrors app.domain.enums.MANAGE_USERS_ROLES -
// a distinct named capability (not just an inline reuse of canDeleteProject)
// even though the check is identical today, since the two are conceptually
// separate permissions that could diverge later.
export const MANAGE_USERS_ROLES = new Set([ROLES.ADMINISTRATOR]);

export function canManageUsers(role) {
  return MANAGE_USERS_ROLES.has(role);
}

// Wave: multi-format layers (Part B). Administrator-only, mirrors
// app.domain.enums.MANAGE_WMS_SOURCES_ROLES - deliberately narrower than
// UPLOAD_ROLES: a GIS Associate can ADD a WMS/WFS layer (see canUpload) but
// must never be able to grow the set of domains the backend will fetch
// from server-side.
export const MANAGE_WMS_SOURCES_ROLES = new Set([ROLES.ADMINISTRATOR]);

export function canManageWmsSources(role) {
  return MANAGE_WMS_SOURCES_ROLES.has(role);
}

// Wave: Reference Layer Library. ADDING a reference layer reuses canUpload
// unchanged (any Administrator/GIS Associate). REMOVING one is Administrator-
// only, mirrors app.domain.enums.MANAGE_REFERENCE_LAYERS_ROLES - same
// broad-to-add/narrow-to-remove asymmetry as the WMS/WFS allow-list above.
export const MANAGE_REFERENCE_LAYERS_ROLES = new Set([ROLES.ADMINISTRATOR]);

export function canManageReferenceLayers(role) {
  return MANAGE_REFERENCE_LAYERS_ROLES.has(role);
}

// Rename-a-layer. Administrator-only, mirrors
// app.domain.enums.RENAME_LAYER_ROLES - a separate named capability from
// MANAGE_REFERENCE_LAYERS_ROLES even though identical today, same
// one-named-capability-per-concept convention as MANAGE_USERS_ROLES above.
// Note this is NARROWER than canUpload: a GIS Associate can upload and edit
// a layer's classes but not relabel it for everyone else.
export const RENAME_LAYER_ROLES = new Set([ROLES.ADMINISTRATOR]);

export function canRenameLayer(role) {
  return RENAME_LAYER_ROLES.has(role);
}

// Delete-a-dataset: a formal, project-scoped upload - not a reference layer
// or an ad-hoc quick-add, which keep their own removal roles/gating
// (canManageReferenceLayers / canUpload respectively). Administrator-only,
// mirrors app.domain.enums.DELETE_DATASET_ROLES - separate named capability
// from RENAME_LAYER_ROLES even though identical today, same convention.
export const DELETE_DATASET_ROLES = new Set([ROLES.ADMINISTRATOR]);

export function canDeleteDataset(role) {
  return DELETE_DATASET_ROLES.has(role);
}

// Wave: project-level RBAC. Mirrors app.domain.enums.PROJECT_ROLES -
// Administrator is global-only, never a valid project-membership role (an
// Administrator already bypasses membership checks entirely).
export const PROJECT_ROLES = [ROLES.GIS_ASSOCIATE, ROLES.ANALYST, ROLES.VERIFIER, ROLES.VIEWER];

/** Mirrors app.domain.authz.require_project_manage: a global Administrator,
 * or a user whose PROJECT-level role on THIS project (not their global
 * role) is GIS Associate. `myProjectRole` is that member's own row from the
 * project's member list, or undefined if they have none. */
export function canManageMembers(globalRole, myProjectRole) {
  return globalRole === ROLES.ADMINISTRATOR || myProjectRole === ROLES.GIS_ASSOCIATE;
}

export const DATASET_TYPES = ["LULC", "NDVI", "Biomass", "Boundary", "Satellite / Raw Imagery"];

export const PROJECT_STATUSES = ["Active", "Under Review", "Archived"];
