/**
 * Single source of truth for color-coding, shared between the project map,
 * the portfolio map, and the land-cover pie chart / legend / table - so the
 * same dataset type or class name always renders the same color everywhere.
 */

/** Colors for the four fixed dataset types (see app/domain/enums.py::DatasetType).
 * Tuned for contrast against white cards/tables and map tiles (light theme) —
 * darker/more saturated than the old dark-theme values, which read as
 * washed-out on a light background. */
export const DATASET_TYPE_COLORS = {
  LULC: "#0B6B46",
  NDVI: "#B4690E",
  Biomass: "#0C6FB0",
  Boundary: "#C4574A",
};

/**
 * Land-cover CLASS names (Cropland, Dense Forest, ...) are free-form: they come
 * from whatever `class_legend` a user supplies at upload time, so the set isn't
 * known ahead of time. Colors are assigned deterministically by hashing the name
 * against a fixed palette, so a given class keeps the same color across the pie
 * chart, its legend, and the breakdown table, regardless of fetch order or which
 * classes happen to be present.
 */
const CLASS_PALETTE = [
  "#0B6B46", "#B4690E", "#0C6FB0", "#C4574A",
  "#7C5CFC", "#0E8C7A", "#6B8C40", "#4F8EF7",
  "#A8447A", "#3D7A5C", "#8A5A2B", "#2E7D9A",
];

function hashString(value) {
  let h = 0;
  for (let i = 0; i < value.length; i++) {
    h = (h * 31 + value.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

export function classColor(name) {
  return CLASS_PALETTE[hashString(name) % CLASS_PALETTE.length];
}
