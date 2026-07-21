/**
 * Shared basemap config for ProjectMap and PortfolioMap, so both stay in sync
 * and swapping providers is a one-place edit.
 *
 * Esri World Imagery is the default: real satellite/aerial basemap, no API key
 * and no billing account required. Set VITE_BASEMAP_URL (+ optionally
 * VITE_BASEMAP_ATTRIBUTION) at build time to swap in Mapbox/Google/etc once a
 * key is available - see frontend/Dockerfile and deploy/docker-compose.yml.
 */
export const BASEMAP_URL =
  import.meta.env.VITE_BASEMAP_URL ||
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";

export const BASEMAP_ATTRIBUTION =
  import.meta.env.VITE_BASEMAP_ATTRIBUTION ||
  "Tiles &copy; Esri &mdash; Esri, Maxar, Earthstar Geographics, and the GIS User Community";

/**
 * Phase 3 Wave E: the "Map" half of ProjectMap's GEE-style Map/Satellite
 * toggle. Same Carto Light tile source PortfolioMap.jsx already uses (copied
 * verbatim, not re-exported from there - PortfolioMap.jsx is out of scope for
 * this wave) - no new tile provider, no API key/billing, matches this
 * codebase's existing "free basemap" decision.
 */
export const CARTO_BASEMAP_URL = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png";
export const CARTO_BASEMAP_ATTRIBUTION =
  '&copy; <a href="https://carto.com/attributions">CARTO</a> &copy; OpenStreetMap contributors';
