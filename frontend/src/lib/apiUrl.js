/**
 * Vector layer URL doubling fix (map-toolbar-v2 wave).
 *
 * `features_url` (ProjectService._features_url, backend) already carries the
 * full `/api/v1/...` path, same as tile_url_template - but unlike that one
 * (used raw as a Leaflet tile URL), features_url goes through apiFetch,
 * which itself prepends API_BASE. Without stripping API_BASE's path first,
 * every vector/WFS layer 404s on a doubled `/api/v1/api/v1/...` path.
 *
 * `.replace(API_BASE, "")` alone only ever matched when API_BASE was the
 * relative default ("/api/v1") - the backend always returns features_url as
 * that same relative path, never with a host. The moment API_BASE is set to
 * an ABSOLUTE url (exactly what VITE_API_BASE is in this repo's own e2e
 * config, playwright.config.js), the literal string API_BASE never occurs
 * inside a relative features_url, the replace is a silent no-op, and
 * apiFetch prepends API_BASE a second time - the doubled path again, just
 * from a different cause than a missing strip. Stripping only API_BASE's
 * PATHNAME (always "/api/v1" regardless of whether a host is configured)
 * matches what's actually embedded in features_url either way.
 */
export function apiBasePathOf(apiBase) {
  return apiBase.startsWith("http") ? new URL(apiBase).pathname : apiBase;
}

/** Strip API_BASE's pathname prefix from a backend-supplied absolute-path
 * URL (e.g. features_url) so it's safe to pass straight into apiFetch,
 * which will re-prepend the full API_BASE (host + path, when absolute). */
export function toApiFetchPath(url, apiBase) {
  return url.replace(apiBasePathOf(apiBase), "");
}
