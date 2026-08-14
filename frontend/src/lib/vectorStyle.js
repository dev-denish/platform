import { DATASET_TYPE_COLORS } from "./colors.js";

/**
 * Per-layer vector style state (Wave: Map Toolbar Enhancement v2, Tier 4) -
 * the vector-layer counterpart to lib/symbology.js's raster-only
 * initSymbologyState. Ephemeral/session-only, same as that sibling state
 * (not persisted to localStorage or the backend) - a layer nobody has
 * touched the controls for renders with exactly the defaults
 * renderVectorLayer already hardcoded before this feature existed, so
 * nothing changes for a project no one has customized.
 */
export function initVectorStyleState(layers) {
  const s = {};
  for (const l of layers) {
    if (l.layer_kind !== "vector" && l.layer_kind !== "external_wfs") continue;
    s[l.layer_id] = {
      color: DATASET_TYPE_COLORS[l.type] ?? "#0B6B46",
      weight: 2,
      fillOpacity: 0.15,
    };
  }
  return s;
}

export function defaultVectorStyle(layer) {
  return { color: DATASET_TYPE_COLORS[layer.type] ?? "#0B6B46", weight: 2, fillOpacity: 0.15 };
}
