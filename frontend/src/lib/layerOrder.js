/**
 * Drag-to-reorder layers (Wave: Map Toolbar Enhancement v2, Tier 4) - a
 * personal display preference, not project data, so this is localStorage
 * per project the same way MapToolbar.jsx's saved map-view bookmarks already
 * are, not a backend field. One FLAT ordered list of layer_ids covering
 * every layer in the project, not one list per LayersPanel group - a single
 * source of truth for "paint order" that both the panel's per-group row
 * order AND the map's own z-index/mount order can derive from consistently.
 */
const storageKey = (projectId) => `map-layer-order:${projectId}`;

export function readLayerOrder(projectId) {
  try {
    const raw = localStorage.getItem(storageKey(projectId));
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    // Corrupt/hand-edited entry - a broken saved order must not break the map.
    return [];
  }
}

export function writeLayerOrder(projectId, order) {
  localStorage.setItem(storageKey(projectId), JSON.stringify(order));
}

/**
 * Sorts `list` by its layers' positions in `order`; any layer NOT yet in
 * `order` (new upload since the order was last saved, or the very first
 * render before any drag has happened) keeps its place in `list`'s own
 * incoming order, appended after every layer that IS in `order` - so an
 * unfamiliar layer shows up sensibly (wherever the caller's default sort,
 * e.g. byDate, already put it) instead of jumping to a surprising position.
 */
export function sortByLayerOrder(list, order) {
  const rank = new Map(order.map((id, i) => [id, i]));
  return [...list].sort((a, b) => {
    const ra = rank.has(a.layer_id) ? rank.get(a.layer_id) : Infinity;
    const rb = rank.has(b.layer_id) ? rank.get(b.layer_id) : Infinity;
    if (ra !== rb) return ra - rb;
    return 0; // both unranked (or a tie) - stable sort keeps list's own order
  });
}

/**
 * Moves `draggedId` to just before `targetId` within `order`. Callers pass
 * the FULL sequence currently on screen (LayersPanel's own flattened,
 * already-sorted display order - see its flatVisualOrder), not necessarily
 * the sparse, only-ever-dragged list ProjectMap.jsx persists - `targetId`
 * may be a layer nobody has dragged before and so isn't in THAT list yet,
 * and this needs to find it at its real visible position, not miss it and
 * silently append to the end. Appends `draggedId` at the end if `targetId`
 * is null (dropped past the last row).
 */
export function moveBefore(order, draggedId, targetId) {
  const next = order.filter((id) => id !== draggedId);
  const targetIndex = targetId ? next.indexOf(targetId) : -1;
  if (targetIndex === -1) {
    next.push(draggedId);
  } else {
    next.splice(targetIndex, 0, draggedId);
  }
  return next;
}
