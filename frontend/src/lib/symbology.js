/** Shared with the pixel-inspection popup (Wave D) - same "entry is either a
 * plain string or a {label, color} object" convention a persisted
 * class_legend uses everywhere else in this app. */
export function legendEntryLabel(value, entry) {
  if (typeof entry === "string") return entry || value;
  return entry?.label || value;
}

export function legendEntryColor(entry) {
  if (entry && typeof entry === "object" && entry.color) return entry.color;
  return "#0B6B46";
}

/**
 * Per-layer symbology state + the query-string it turns into on the tile URL.
 * Mirrors the backend's own defaults exactly (app/services/tile_renderer.py):
 * no params -> first 3 bands (or 1 repeated to grayscale) at a 2-98 percentile
 * stretch - so a layer nobody has touched the controls for renders identically
 * to before this feature existed.
 */
export function initSymbologyState(layers) {
  const s = {};
  for (const l of layers) {
    const hasLegend = !!(l.class_legend && Object.keys(l.class_legend).length > 0);
    const bandCount = l.band_count ?? 3;
    s[l.layer_id] = {
      mode: hasLegend ? "classified" : "raw",
      composite: bandCount >= 3 ? "rgb" : "gray",
      bands: bandCount >= 3 ? [1, 2, 3] : [1],
      stretch: [2, 98],
      colorOverrides: {},
    };
  }
  return s;
}

/** Appends this layer's current symbology as query params onto its signed
 * tile_url_template. Classified mode only sends `colors` (and only if the
 * user actually overrode something - otherwise the persisted legend's own
 * colors already apply server-side with no params needed). Raw mode always
 * sends `bands` + `stretch` explicitly, which also correctly forces raw
 * rendering even for a single-band classified layer the user chose to view
 * as raw (see tile_renderer.py: an explicit `bands` param always wins). */
export function buildTileUrl(template, symbology, hasLegend) {
  if (!template || !symbology) return template;
  const params = new URLSearchParams();

  if (symbology.mode === "classified" && hasLegend) {
    const overrides = Object.entries(symbology.colorOverrides).filter(([, hex]) => hex);
    if (overrides.length > 0) {
      params.set("colors", overrides.map(([value, hex]) => `${value}:${hex.replace("#", "")}`).join(","));
    }
  } else {
    params.set("bands", symbology.bands.join(","));
    params.set("stretch", symbology.stretch.join(","));
  }

  const qs = params.toString();
  return qs ? `${template}&${qs}` : template;
}
