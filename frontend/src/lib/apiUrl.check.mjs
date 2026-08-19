// Run: node src/lib/apiUrl.check.mjs   (no test runner in this frontend)
//
// Regression coverage for the vector-layer URL doubling bug: a features_url
// like "/api/v1/layers/<id>/geojson" (always backend-relative, see
// ProjectService._features_url) must come out of toApiFetchPath() ready to
// be re-prepended with API_BASE by apiFetch without doubling `/api/v1`,
// whether API_BASE is the relative default or an absolute URL (as
// playwright.config.js sets it for the e2e suite).
import assert from "node:assert/strict";
import { apiBasePathOf, toApiFetchPath } from "./apiUrl.js";

const FEATURES_URL = "/api/v1/layers/42/geojson";

// Relative API_BASE (nginx/ingress default, .env.example, docker-compose.yml, Dockerfile default).
assert.equal(apiBasePathOf("/api/v1"), "/api/v1");
assert.equal(toApiFetchPath(FEATURES_URL, "/api/v1"), "/layers/42/geojson");
assert.equal(`/api/v1${toApiFetchPath(FEATURES_URL, "/api/v1")}`, FEATURES_URL);

// Absolute API_BASE - exactly playwright.config.js's webServer.env.VITE_API_BASE.
const ABSOLUTE_BASE = "http://localhost:8091/api/v1";
assert.equal(apiBasePathOf(ABSOLUTE_BASE), "/api/v1");
assert.equal(toApiFetchPath(FEATURES_URL, ABSOLUTE_BASE), "/layers/42/geojson");
assert.equal(`${ABSOLUTE_BASE}${toApiFetchPath(FEATURES_URL, ABSOLUTE_BASE)}`, `${ABSOLUTE_BASE}/layers/42/geojson`);

// external_wfs layers share the identical features_url shape (ProjectService._features_url).
assert.equal(
  toApiFetchPath("/api/v1/external-layers/7/wfs", ABSOLUTE_BASE),
  "/external-layers/7/wfs"
);

console.log("apiUrl.js toApiFetchPath ok - no /api/v1/api/v1 doubling for relative or absolute API_BASE");
