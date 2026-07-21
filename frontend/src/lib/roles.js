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

export const DATASET_TYPES = ["LULC", "NDVI", "Biomass", "Boundary", "Satellite / Raw Imagery"];

export const PROJECT_STATUSES = ["Active", "Under Review", "Archived"];
