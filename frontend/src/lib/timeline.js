/**
 * Groups a project's layers by their real date_processed value for the
 * time-slider/compare feature - no assumed spacing (irregular real dates are
 * fine), sorted chronologically. Layers with no date are excluded entirely;
 * they keep rendering through the existing always-on layer checkboxes
 * instead of the time controls.
 */
export function datedLayerGroups(layers) {
  const byDate = new Map();
  for (const l of layers) {
    if (!l.date_processed) continue;
    const group = byDate.get(l.date_processed) ?? [];
    group.push(l);
    byDate.set(l.date_processed, group);
  }
  return [...byDate.entries()]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([date, group]) => ({
      date,
      // ponytail: real data can have >1 layer on the same date (seen in dev
      // DB); prefer one with real tiles over a preview-only stub instead of
      // building a whole multi-layer-per-date UI for a rare case.
      layer: group.find((l) => l.tile_url_template) ?? group[0],
    }));
}
