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

/**
 * Native max zoom for each basemap source, for Leaflet's `maxNativeZoom` -
 * NOT the map's overall `maxZoom` (that stays 22, driven by the classified
 * LULC/raster layers' own COG tiling). Past its native zoom, Leaflet keeps
 * requesting the highest real tile and scales it up instead of asking the
 * source for tiles that don't exist there.
 *
 * Esri World Imagery: 18, confirmed by direct testing - real submeter
 * coverage runs out well before the service's published LOD table (which
 * lists levels up to 23) outside flagship-metro imagery programs.
 *
 * Carto light_all: 20, confirmed by fetching actual tile bytes across zooms
 * and locations - tile content is still substantive through z20, thins
 * sharply at z21, and is a near-blank ~100-byte tile by z22.
 */
export const BASEMAP_MAX_NATIVE_ZOOM = 18;
export const CARTO_BASEMAP_MAX_NATIVE_ZOOM = 20;

/**
 * Basemap gallery (Wave: map toolbar capabilities) - the picker's whole option
 * list, so adding a source is one entry here and nothing else. `satellite` and
 * `map` are the two originals above, unchanged and still first so an existing
 * session's saved mode keeps meaning what it meant.
 *
 * Every entry carries its OWN `maxNativeZoom`, confirmed the same way the two
 * originals were (fetching real tile BYTES across zooms and locations, not
 * just checking for a 200) - a source that 200s with a blank placeholder past
 * its real depth is exactly the blank-tile bug the maxNativeZoom fix
 * addressed:
 *
 * - OSM standard: real content through z19; z20+ is a hard HTTP 400 from the
 *   tile server. maxNativeZoom 19.
 * - Carto Dark (dark_all): same tile pyramid as the light_all "Map" entry -
 *   substantive content through z20, thinning at z21. maxNativeZoom 20.
 * - Esri World Topo Map: content stops at z17 and z18/19/20 all return the
 *   identical 2521-byte "map data not available" placeholder (byte-identical
 *   md5 across Mysuru/Suntikoppa/Madikeri/Bengaluru/Delhi - i.e. a 200 that
 *   renders as nothing). maxNativeZoom 17, so Leaflet upscales the real z17
 *   tile instead of showing that placeholder.
 */
/**
 * Wave: Basemap panel (Map Toolbar Enhancement v2). Google Satellite/Terrain/
 * Hybrid/Streets, added at the user's explicit direction despite the ToS risk
 * flagged during planning: this app has no Google Maps Platform API key or
 * billing account configured anywhere (confirmed - grepped the whole repo),
 * so these hit Google's undocumented mt0-3.google.com/vt tile endpoint
 * instead of the real (keyed, billed) Tiles API. No SLA, unsupported, can
 * break or get IP-blocked without warning - if that happens, the fix is a
 * real Maps Platform key + billing account, swapped in here the same way
 * VITE_BASEMAP_URL already lets satellite's own URL be overridden.
 *
 * Reachability + real tile content spot-checked the same way every other
 * entry in this file was (fetching actual tile bytes, not just a 200) -
 * against Bengaluru (12.9716, 77.5946) at z20/z21, all four layer codes
 * (s/p/y/m) returned real, non-placeholder image bytes. Only spot-checked at
 * one city, unlike the exhaustive multi-city testing the other entries below
 * document - an unofficial endpoint with no published LOD table to check
 * against. maxNativeZoom 20 for all four (satellite alone also confirmed
 * live at z21, but kept at the shared, more conservative number rather than
 * one-off tuning a single layer past what was actually verified for the
 * other three).
 *
 * `subdomains: "0123"` - Google's mt{s} convention is numeric (mt0-mt3), not
 * Leaflet's default 'abc'; harmless no-op on every non-Google entry below
 * (their URLs don't contain `{s}` at all).
 */
const GOOGLE_SUBDOMAINS = "0123";
const GOOGLE_ATTRIBUTION = "Map data &copy; Google";
const GOOGLE_MAX_NATIVE_ZOOM = 20;

export const BASEMAPS = [
  {
    key: "satellite",
    label: "Satellite",
    source: "esri",
    url: BASEMAP_URL,
    attribution: BASEMAP_ATTRIBUTION,
    maxNativeZoom: BASEMAP_MAX_NATIVE_ZOOM,
  },
  {
    key: "map",
    label: "Map (light)",
    source: "carto",
    url: CARTO_BASEMAP_URL,
    attribution: CARTO_BASEMAP_ATTRIBUTION,
    maxNativeZoom: CARTO_BASEMAP_MAX_NATIVE_ZOOM,
  },
  {
    key: "dark",
    label: "Map (dark)",
    source: "carto",
    url: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    attribution: CARTO_BASEMAP_ATTRIBUTION,
    maxNativeZoom: 20,
  },
  {
    key: "osm",
    label: "Streets (OSM)",
    source: "osm",
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxNativeZoom: 19,
  },
  {
    key: "topo",
    label: "Topographic",
    source: "esri",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
    attribution: "Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ, and the GIS User Community",
    maxNativeZoom: 17,
  },
  {
    key: "google-satellite",
    label: "Google Satellite",
    source: "google",
    url: "https://mt{s}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
    subdomains: GOOGLE_SUBDOMAINS,
    attribution: GOOGLE_ATTRIBUTION,
    maxNativeZoom: GOOGLE_MAX_NATIVE_ZOOM,
  },
  {
    key: "google-terrain",
    label: "Google Terrain",
    source: "google",
    url: "https://mt{s}.google.com/vt/lyrs=p&x={x}&y={y}&z={z}",
    subdomains: GOOGLE_SUBDOMAINS,
    attribution: GOOGLE_ATTRIBUTION,
    maxNativeZoom: GOOGLE_MAX_NATIVE_ZOOM,
  },
  {
    key: "google-hybrid",
    label: "Google Hybrid",
    source: "google",
    url: "https://mt{s}.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
    subdomains: GOOGLE_SUBDOMAINS,
    attribution: GOOGLE_ATTRIBUTION,
    maxNativeZoom: GOOGLE_MAX_NATIVE_ZOOM,
  },
  {
    key: "google-streets",
    label: "Google Streets",
    source: "google",
    url: "https://mt{s}.google.com/vt/lyrs=m&x={x}&y={y}&z={z}",
    subdomains: GOOGLE_SUBDOMAINS,
    attribution: GOOGLE_ATTRIBUTION,
    maxNativeZoom: GOOGLE_MAX_NATIVE_ZOOM,
  },
];

/** Display order + labels for the Basemap panel's source tabs (Wave: Basemap
 * panel). Google first - it's the one source this pass actually built,
 * mirroring the mockup's Google-first tab order. */
// `termsUrl`/`reportUrl` (Wave: Map Toolbar Enhancement v2 - attribution info
// toggle) - each source's own REAL terms-of-use and map-error-reporting
// pages, not placeholders. OSM's own /fixthemap is its actual official
// "report a map problem" page; Esri/Google/Carto don't publish an
// imagery-specific equivalent, so those two point at each provider's general
// support/feedback channel instead.
export const BASEMAP_SOURCES = [
  {
    key: "google",
    label: "Google",
    termsUrl: "https://www.google.com/help/terms_maps/",
    reportUrl: "https://support.google.com/maps/answer/3094088",
  },
  {
    key: "esri",
    label: "Esri",
    termsUrl: "https://www.esri.com/en-us/legal/terms/full-master-agreement",
    reportUrl: "https://support.esri.com/",
  },
  {
    key: "carto",
    label: "Carto",
    termsUrl: "https://carto.com/attributions",
    reportUrl: "https://carto.com/contact/",
  },
  {
    key: "osm",
    label: "OpenStreetMap",
    termsUrl: "https://www.openstreetmap.org/copyright",
    reportUrl: "https://www.openstreetmap.org/fixthemap",
  },
];

/** Falls back to the `google` entry (arbitrary but always present) so an
 * unknown/stale basemap source never leaves the attribution info panel with
 * dead links. */
export function basemapSourceInfo(sourceKey) {
  return BASEMAP_SOURCES.find((s) => s.key === sourceKey) ?? BASEMAP_SOURCES[0];
}

/** Falls back to the first entry so an unknown/stale stored mode still renders
 * a basemap rather than nothing. */
export function basemapFor(key) {
  return BASEMAPS.find((b) => b.key === key) ?? BASEMAPS[0];
}
