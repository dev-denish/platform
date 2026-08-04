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
export const BASEMAPS = [
  {
    key: "satellite",
    label: "Satellite",
    url: BASEMAP_URL,
    attribution: BASEMAP_ATTRIBUTION,
    maxNativeZoom: BASEMAP_MAX_NATIVE_ZOOM,
  },
  {
    key: "map",
    label: "Map (light)",
    url: CARTO_BASEMAP_URL,
    attribution: CARTO_BASEMAP_ATTRIBUTION,
    maxNativeZoom: CARTO_BASEMAP_MAX_NATIVE_ZOOM,
  },
  {
    key: "dark",
    label: "Map (dark)",
    url: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    attribution: CARTO_BASEMAP_ATTRIBUTION,
    maxNativeZoom: 20,
  },
  {
    key: "osm",
    label: "Streets (OSM)",
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxNativeZoom: 19,
  },
  {
    key: "topo",
    label: "Topographic",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
    attribution: "Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ, and the GIS User Community",
    maxNativeZoom: 17,
  },
];

/** Falls back to the first entry so an unknown/stale stored mode still renders
 * a basemap rather than nothing. */
export function basemapFor(key) {
  return BASEMAPS.find((b) => b.key === key) ?? BASEMAPS[0];
}
