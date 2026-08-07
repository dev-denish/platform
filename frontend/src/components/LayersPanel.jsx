import { memo, useMemo, useState } from "react";
import { AlertTriangle, ChevronDown, Info, Settings2, Trash2, X } from "lucide-react";
import { useCollapse } from "../lib/useCollapse.js";
import SymbologyPanel from "./SymbologyPanel.jsx";
import ClassLegendEditor from "./ClassLegendEditor.jsx";
import AddAdhocLayerDialog from "./AddAdhocLayerDialog.jsx";
import ConfirmDialog from "./ConfirmDialog.jsx";
import { formatDate, formatNumber } from "../lib/format.js";
import { apiFetch } from "../config.js";
import { useAuth } from "../context/AuthContext.jsx";
import { canDeleteDataset, canManageReferenceLayers, canRenameLayer, canUpload } from "../lib/roles.js";

/**
 * The GEE Code Editor's "Layers" panel - a floating overlay top-left of the
 * map (Wave: floating map controls), behind its own orange toggle, after a
 * stint as a docked left column (Wave: map UI redesign) and before that a
 * floating top-right overlay - grouped into the four real layer kinds the
 * multi-format wave introduced
 * (classified imagery / raw imagery / vector / WMS-WFS), each independently
 * collapsible. "Reference Layers" and "Added Layers" are explicitly a later
 * wave's concept and are NOT built here - this only reorganizes/restyles a
 * project's own already-ingested layers.
 *
 * One visibility control per layer (confirmed against real QGIS behavior:
 * its checkbox and eye icon are literally the same control, not two) - the
 * plain checkbox already was that single control, kept as-is.
 *
 * `vectorData` is "existing data, just surfaced here" for the feature count -
 * each vector/WFS layer's own already-fetched GeoJSON (built by
 * ProjectMap.jsx), no new endpoint. `source`/`accuracy_score` for the info
 * popover come straight off each layer object now (LayerOut - Wave:
 * Reference Layer Library), not a separate per-project datasets list.
 *
 * Wave: Reference Layer Library - a reference layer (visible on EVERY
 * project, not just the one it's attached to - see LayerOut.is_reference)
 * gets its OWN group regardless of kind, checked first so it never also
 * lands in one of the four kind-based groups below.
 *
 * Wave 3 (Added Layers) - an ad-hoc layer (LayerOut.is_adhoc) gets its own
 * "Added layers" group too, same mutual-exclusion pattern as is_reference:
 * checked alongside it so it never also lands in a kind-based group.
 */
const GROUPS = [
  { key: "reference", label: "Reference layers", match: (l) => l.is_reference },
  { key: "adhoc", label: "Added layers", match: (l) => !l.is_reference && l.is_adhoc },
  {
    key: "classified",
    label: "Classified imagery",
    match: (l) => !l.is_reference && !l.is_adhoc && l.layer_kind === "raster" && !!l.class_legend,
  },
  {
    key: "raw",
    label: "Raw imagery",
    match: (l) => !l.is_reference && !l.is_adhoc && l.layer_kind === "raster" && !l.class_legend,
  },
  {
    key: "vector",
    label: "Vector layers",
    match: (l) => !l.is_reference && !l.is_adhoc && l.layer_kind === "vector",
  },
  {
    key: "wms_wfs",
    label: "WMS/WFS layers",
    match: (l) =>
      !l.is_reference && !l.is_adhoc && (l.layer_kind === "external_wms" || l.layer_kind === "external_wfs"),
  },
];

function byDate(a, b) {
  if (!a.date_processed && !b.date_processed) return 0;
  if (!a.date_processed) return 1;
  if (!b.date_processed) return -1;
  return a.date_processed < b.date_processed ? -1 : a.date_processed > b.date_processed ? 1 : 0;
}

/**
 * The one place a layer's name is decided, used by the row label and by the
 * rename field's prefill so the two can't drift. An admin-set
 * `display_name` (rename-a-layer) wins; with no override this is exactly the
 * label shown before that field existed - an ad-hoc layer's meaningful name
 * is whatever display name it was added with (stored in `source` - see
 * adhoc_layers.py), not its generic internal dataset type, which the
 * uploader never saw.
 */
function displayLabel(layer) {
  return layer.display_name ?? (layer.is_adhoc ? layer.source ?? "Untitled layer" : layer.type);
}

/** displayLabel, suffixed with the date if there is one (mirrors the
 * backend's dataset_label formatting) - the row label already builds this
 * inline; the delete confirmation needs the exact same string so two
 * same-type datasets with different dates aren't indistinguishable right
 * before a permanent, unrecoverable delete. */
function displayLabelWithDate(layer) {
  return `${displayLabel(layer)}${layer.date_processed ? ` · ${layer.date_processed}` : ""}`;
}

/**
 * Rename-a-layer: the info popover's editable name field, rendered only for
 * an Administrator (everyone else gets the same value as plain text). The
 * caller keys this on layer_id so switching layers remounts it with the new
 * prefill - no effect needed to sync state to props. Save/error state
 * follows ClassLegendEditor's saving/error + try-finally pattern.
 */
function LayerNameField({ layer, onRenamed }) {
  const [value, setValue] = useState(() => displayLabel(layer));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  async function save() {
    const trimmed = value.trim();
    // Matches the backend's min_length=1 - a whitespace-only name would be a
    // 422 and would also render as a nameless row.
    if (!trimmed) {
      setError("Enter a name.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await apiFetch(`/layers/${layer.layer_id}/display-name`, {
        method: "PATCH",
        body: JSON.stringify({ display_name: trimmed }),
      });
      await onRenamed?.();
    } catch (err) {
      setError(err.message ?? "Could not rename this layer.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="layer-rename-field">
      <input
        type="text"
        className="field-input"
        aria-label="Layer name"
        maxLength={256}
        value={value}
        disabled={saving}
        onChange={(e) => setValue(e.target.value)}
      />
      <button type="button" className="ghost-button" disabled={saving} onClick={save}>
        {saving ? "Saving…" : "Save"}
      </button>
      {error ? <span className="field-hint field-hint-error">{error}</span> : null}
    </div>
  );
}

function featureCountFor(layer, vectorData) {
  if (layer.layer_kind !== "vector" && layer.layer_kind !== "external_wfs") return null;
  const data = vectorData?.[layer.layer_id];
  return data?.features ? data.features.length : null;
}

function LayersPanel({
  layers,
  layerState,
  symbologyState,
  vectorData,
  onToggleVisibility,
  onOpacityChange,
  onSymbologyChange,
  onRefreshLayers,
  onLegendChanged,
  projectId,
}) {
  const { user } = useAuth();
  // Shared collapsible primitive (sessionStorage-backed, so a collapsed
  // group stays collapsed when you navigate away and come back). No projectId
  // in the keys: this is per-browser-tab UI preference, not project data.
  const [expanded, toggleExpanded] = useCollapse("collapse:layers-panel:root", true);
  // One hook per entry of the module-level GROUPS const - a fixed-length list,
  // so the hook COUNT and ORDER are identical on every render. Deliberately
  // driven off GROUPS and not off `groupedLayers`, which is filtered by
  // `layers.length > 0` further down: that filter changes length whenever a
  // layer is added/removed, and calling a hook per filtered entry would shift
  // hook order mid-session. Groups with no layers just don't render any UI.
  const groupCollapse = Object.fromEntries(
    GROUPS.map((g) => [g.key, useCollapse(`collapse:layers-panel:group:${g.key}`, true)])
  );
  // A single shared popover slot (like before) - only ever one of
  // gear/info open at a time, whichever layer/kind was last clicked.
  const [openPopover, setOpenPopover] = useState(null); // { layerId, kind: "gear" | "info" }
  const [removingId, setRemovingId] = useState(null);
  const [addLayerOpen, setAddLayerOpen] = useState(false);
  // Delete-a-dataset needs a real confirmation (unlike the reference/ad-hoc
  // removals above, which delete on click) - the underlying file isn't
  // easily recoverable once gone. Holds the whole layer object (not just an
  // id) so the confirmation dialog can show its name without a second
  // lookup.
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  async function removeReferenceLayer(layerId) {
    setRemovingId(layerId);
    try {
      await apiFetch(`/reference-layers/${layerId}`, { method: "DELETE" });
      await onRefreshLayers?.();
    } finally {
      setRemovingId(null);
    }
  }

  async function removeAdhocLayer(layerId) {
    setRemovingId(layerId);
    try {
      await apiFetch(`/adhoc-layers/${layerId}`, { method: "DELETE" });
      await onRefreshLayers?.();
    } finally {
      setRemovingId(null);
    }
  }

  async function deleteDataset() {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await apiFetch(`/datasets/${deleteTarget.layer_id}`, { method: "DELETE" });
      await onRefreshLayers?.();
      setDeleteTarget(null);
    } catch (err) {
      // Dialog stays open with the error visible - a failed delete
      // shouldn't silently close as if it had succeeded.
      setDeleteError(err.message ?? "Could not delete this dataset.");
    } finally {
      setDeleting(false);
    }
  }

  const groupedLayers = useMemo(() => {
    return GROUPS.map((g) => ({ ...g, layers: layers.filter(g.match).sort(byDate) }));
  }, [layers]);

  if (!layers || layers.length === 0) return null;

  function isChecked(layer) {
    return (layerState[layer.layer_id] ?? { visible: true }).visible;
  }

  function openPopoverFor(layerId, kind) {
    setOpenPopover((prev) => (prev?.layerId === layerId && prev?.kind === kind ? null : { layerId, kind }));
  }

  const openLayer = layers.find((l) => l.layer_id === openPopover?.layerId) ?? null;

  return (
    <div className="layers-panel">
      <button type="button" className="layers-panel-header" aria-expanded={expanded} onClick={toggleExpanded}>
        <span className="layers-panel-title">Layers</span>
        <span className={`layers-panel-chevron${expanded ? " layers-panel-chevron-open" : ""}`} aria-hidden="true">
          <ChevronDown size={16} strokeWidth={2} className="icon" />
        </span>
      </button>
      {expanded && user && canUpload(user.role) && projectId ? (
        <div className="layers-panel-toolbar">
          <button type="button" className="ghost-button" onClick={() => setAddLayerOpen(true)}>
            + Add layer
          </button>
        </div>
      ) : null}
      <AddAdhocLayerDialog
        open={addLayerOpen}
        projectId={projectId}
        onCreated={async () => {
          setAddLayerOpen(false);
          await onRefreshLayers?.();
        }}
        onCancel={() => setAddLayerOpen(false)}
      />
      {/* The shared `.collapsible-body` grid-rows animation, on the existing
       * panel classes. `flex` is inline rather than in the stylesheet because
       * this wrapper is now the flex child that has to fill the floating
       * panel's own max-height when open (and take zero height when closed),
       * and `.layers-panel-groups` inside it needs a bounded height to keep
       * scrolling. */}
      <div
        className="collapsible-body"
        data-open={expanded}
        inert={expanded ? undefined : ""}
        style={{ flex: expanded ? 1 : "none", minHeight: 0 }}
      >
        <div className="collapsible-body-inner">
          <div className="layers-panel-groups" style={{ maxHeight: "100%" }}>
            {groupedLayers
              .filter((g) => g.layers.length > 0)
              .map((g) => {
                const [groupOpen, toggleGroup] = groupCollapse[g.key];
                return (
                  <div className="layer-group" key={g.key}>
                    <button
                      type="button"
                      className="layer-group-header"
                      aria-expanded={groupOpen}
                      onClick={toggleGroup}
                    >
                      <span
                        className={`layer-group-chevron${groupOpen ? " layer-group-chevron-open" : ""}`}
                        aria-hidden="true"
                      >
                        <ChevronDown size={16} strokeWidth={2} className="icon" />
                      </span>
                      {g.label}
                    </button>
                    <div className="collapsible-body" data-open={groupOpen} inert={groupOpen ? undefined : ""}>
                      <div className="collapsible-body-inner">
                        <ul className="layers-panel-list">
                          {g.layers.map((l) => {
                            const featureCount = featureCountFor(l, vectorData);
                            return (
                              <li className="layer-row" key={l.layer_id}>
                                <input
                                  type="checkbox"
                                  className="layer-row-checkbox"
                                  checked={isChecked(l)}
                                  onChange={() => onToggleVisibility(l.layer_id, !isChecked(l))}
                                  aria-label={`Toggle ${l.type} ${l.date_processed ?? "undated"}`}
                                />
                                <span className="layer-row-body">
                                  <span className="layer-row-label">{displayLabelWithDate(l)}</span>
                                  {featureCount != null ? (
                                    <span className="layer-row-feature-count">{formatNumber(featureCount, 0)} features</span>
                                  ) : null}
                                </span>
                                {l.needs_reingestion ? (
                                  <span
                                    className="layer-row-warning"
                                    title="This layer predates a rendering fix and has no real padding mask - re-upload the source file to fix warp-fill artifacts."
                                  >
                                    <AlertTriangle size={14} strokeWidth={2} className="icon" aria-hidden="true" />
                                  </span>
                                ) : null}
                                <button
                                  type="button"
                                  className="layer-row-info"
                                  aria-label="Layer info"
                                  onClick={() => openPopoverFor(l.layer_id, "info")}
                                >
                                  <Info size={16} strokeWidth={2} className="icon" />
                                </button>
                                {l.layer_kind === "raster" && l.tile_url_template ? (
                                  <button
                                    type="button"
                                    className="layer-row-gear"
                                    aria-label="Visualization parameters"
                                    onClick={() => openPopoverFor(l.layer_id, "gear")}
                                  >
                                    <Settings2 size={16} strokeWidth={2} className="icon" />
                                  </button>
                                ) : null}
                                {l.is_reference && user && canManageReferenceLayers(user.role) ? (
                                  <button
                                    type="button"
                                    className="layer-row-remove"
                                    aria-label="Remove this reference layer"
                                    disabled={removingId === l.layer_id}
                                    onClick={() => removeReferenceLayer(l.layer_id)}
                                  >
                                    <X size={16} strokeWidth={2} className="icon" />
                                  </button>
                                ) : null}
                                {l.is_adhoc && user && canUpload(user.role) ? (
                                  <button
                                    type="button"
                                    className="layer-row-remove"
                                    aria-label="Remove this added layer"
                                    disabled={removingId === l.layer_id}
                                    onClick={() => removeAdhocLayer(l.layer_id)}
                                  >
                                    <X size={16} strokeWidth={2} className="icon" />
                                  </button>
                                ) : null}
                                {!l.is_reference && !l.is_adhoc && user && canDeleteDataset(user.role) ? (
                                  <button
                                    type="button"
                                    className="layer-row-remove"
                                    aria-label="Delete this dataset"
                                    onClick={() => {
                                      setDeleteError(null);
                                      setDeleteTarget(l);
                                    }}
                                  >
                                    <Trash2 size={16} strokeWidth={2} className="icon" />
                                  </button>
                                ) : null}
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    </div>
                  </div>
                );
              })}
          </div>
        </div>
      </div>

      {openLayer && openPopover.kind === "gear" ? (
        <div className="symbology-popover">
          <div className="symbology-popover-header">
            <span>
              {openLayer.type}
              {openLayer.date_processed ? ` (${openLayer.date_processed})` : ""}
            </span>
            <span className="symbology-popover-subtitle">visualization parameters</span>
          </div>

          {/* Only the middle content scrolls (see .symbology-popover-scroll) -
           * a 9-class LULC legend opened in ClassLegendEditor grows this
           * popover past the map frame, which clips it. Header and footer stay
           * outside the wrapper so the layer name and Close/Apply are always
           * visible. */}
          <div className="symbology-popover-scroll">
            <SymbologyPanel
              layer={openLayer}
              symbology={symbologyState[openLayer.layer_id]}
              onChange={onSymbologyChange}
              hideTitle
            />

            {openLayer.layer_kind === "raster" && openLayer.class_legend && user && canUpload(user.role) ? (
              <ClassLegendEditor layer={openLayer} onSaved={onLegendChanged} />
            ) : null}

            <label className="symbology-popover-opacity">
              <span>Opacity</span>
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={(layerState[openLayer.layer_id] ?? { opacity: 1 }).opacity}
                onChange={(e) => onOpacityChange(openLayer.layer_id, Number(e.target.value))}
              />
              <span className="symbology-popover-opacity-value">
                {(layerState[openLayer.layer_id] ?? { opacity: 1 }).opacity.toFixed(2)}
              </span>
            </label>
          </div>

          <div className="symbology-popover-footer">
            <button type="button" className="ghost-button" onClick={() => setOpenPopover(null)}>
              Close
            </button>
            <button type="button" className="primary-button" onClick={() => setOpenPopover(null)}>
              Apply
            </button>
          </div>
        </div>
      ) : null}

      {openLayer && openPopover.kind === "info" ? (
        <div className="symbology-popover">
          <div className="symbology-popover-header">
            <span>{openLayer.type}</span>
            <span className="symbology-popover-subtitle">layer info</span>
          </div>
          <dl className="layer-info-fields">
            <dt>Name</dt>
            <dd>
              {user && canRenameLayer(user.role) ? (
                <LayerNameField key={openLayer.layer_id} layer={openLayer} onRenamed={onRefreshLayers} />
              ) : (
                displayLabel(openLayer)
              )}
            </dd>
            <dt>Source</dt>
            <dd>{openLayer.source ?? "—"}</dd>
            <dt>Date processed</dt>
            <dd>{formatDate(openLayer.date_processed)}</dd>
            <dt>Accuracy</dt>
            <dd>
              {openLayer.accuracy_score != null ? (
                <span className="verify-mark">{formatNumber(openLayer.accuracy_score)}%</span>
              ) : (
                "—"
              )}
            </dd>
            {openLayer.needs_reingestion ? (
              <>
                <dt>Padding mask</dt>
                <dd>
                  <AlertTriangle size={14} strokeWidth={2} className="icon" aria-hidden="true" /> Needs re-upload
                  (predates the padding-mask fix)
                </dd>
              </>
            ) : null}
          </dl>
          <div className="symbology-popover-footer">
            <button type="button" className="ghost-button" onClick={() => setOpenPopover(null)}>
              Close
            </button>
          </div>
        </div>
      ) : null}

      <ConfirmDialog
        open={!!deleteTarget}
        title="Delete this dataset?"
        detail={
          deleteTarget
            ? `"${displayLabelWithDate(deleteTarget)}" and its underlying file will be permanently removed. This cannot be undone.`
            : ""
        }
        confirmLabel={deleting ? "Deleting…" : "Delete"}
        danger
        onConfirm={deleteDataset}
        onCancel={() => setDeleteTarget(null)}
      >
        {deleteError ? <p className="field-hint field-hint-error">{deleteError}</p> : null}
      </ConfirmDialog>
    </div>
  );
}

// Memoized: its callback props (onToggleVisibility / onOpacityChange /
// onSymbologyChange) are useCallback-stabilized in ProjectMap, so a
// mousemove-driven map re-render no longer re-renders this whole panel.
export default memo(LayersPanel);
