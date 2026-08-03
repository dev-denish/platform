import { useMemo, useState } from "react";
import SymbologyPanel from "./SymbologyPanel.jsx";
import ClassLegendEditor from "./ClassLegendEditor.jsx";
import AddAdhocLayerDialog from "./AddAdhocLayerDialog.jsx";
import { formatDate, formatNumber } from "../lib/format.js";
import { apiFetch } from "../config.js";
import { useAuth } from "../context/AuthContext.jsx";
import { canManageReferenceLayers, canUpload } from "../lib/roles.js";

/**
 * The GEE Code Editor's "Layers" panel, now a docked left column beside the
 * map (Wave: map UI redesign) rather than a floating top-right overlay -
 * grouped into the four real layer kinds the multi-format wave introduced
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

function featureCountFor(layer, vectorData) {
  if (layer.layer_kind !== "vector" && layer.layer_kind !== "external_wfs") return null;
  const data = vectorData?.[layer.layer_id];
  return data?.features ? data.features.length : null;
}

export default function LayersPanel({
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
  const [expanded, setExpanded] = useState(true);
  const [expandedGroups, setExpandedGroups] = useState(() =>
    Object.fromEntries(GROUPS.map((g) => [g.key, true]))
  );
  // A single shared popover slot (like before) - only ever one of
  // gear/info open at a time, whichever layer/kind was last clicked.
  const [openPopover, setOpenPopover] = useState(null); // { layerId, kind: "gear" | "info" }
  const [removingId, setRemovingId] = useState(null);
  const [addLayerOpen, setAddLayerOpen] = useState(false);

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

  const groupedLayers = useMemo(() => {
    return GROUPS.map((g) => ({ ...g, layers: layers.filter(g.match).sort(byDate) }));
  }, [layers]);

  if (!layers || layers.length === 0) return null;

  function isChecked(layer) {
    return (layerState[layer.layer_id] ?? { visible: true }).visible;
  }

  function toggleGroup(key) {
    setExpandedGroups((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  function openPopoverFor(layerId, kind) {
    setOpenPopover((prev) => (prev?.layerId === layerId && prev?.kind === kind ? null : { layerId, kind }));
  }

  const openLayer = layers.find((l) => l.layer_id === openPopover?.layerId) ?? null;

  return (
    <div className="layers-panel">
      <button
        type="button"
        className={`layers-panel-header${expanded ? "" : " layers-panel-header-collapsed"}`}
        onClick={() => setExpanded((e) => !e)}
      >
        <span className="layers-panel-title">Layers</span>
        <span className={`layers-panel-chevron${expanded ? " layers-panel-chevron-open" : ""}`} aria-hidden="true">
          ▾
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
      {expanded ? (
        <div className="layers-panel-groups">
          {groupedLayers
            .filter((g) => g.layers.length > 0)
            .map((g) => (
              <div className="layer-group" key={g.key}>
                <button type="button" className="layer-group-header" onClick={() => toggleGroup(g.key)}>
                  <span
                    className={`layer-group-chevron${expandedGroups[g.key] ? " layer-group-chevron-open" : ""}`}
                    aria-hidden="true"
                  >
                    ▾
                  </span>
                  {g.label}
                </button>
                {expandedGroups[g.key] ? (
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
                            <span className="layer-row-label">
                              {/* Wave 3: an ad-hoc layer's meaningful name is
                                  whatever display name it was added with
                                  (stored in `source` - see
                                  adhoc_layers.py) - not its generic internal
                                  dataset type, which the uploader never saw. */}
                              {l.is_adhoc ? l.source ?? "Untitled layer" : l.type}
                              {l.date_processed ? ` · ${l.date_processed}` : ""}
                            </span>
                            {featureCount != null ? (
                              <span className="layer-row-feature-count">{formatNumber(featureCount, 0)} features</span>
                            ) : null}
                          </span>
                          {l.needs_reingestion ? (
                            <span
                              className="layer-row-warning"
                              title="This layer predates a rendering fix and has no real padding mask - re-upload the source file to fix warp-fill artifacts."
                            >
                              ⚠
                            </span>
                          ) : null}
                          <button
                            type="button"
                            className="layer-row-info"
                            aria-label="Layer info"
                            onClick={() => openPopoverFor(l.layer_id, "info")}
                          >
                            ⓘ
                          </button>
                          {l.layer_kind === "raster" && l.tile_url_template ? (
                            <button
                              type="button"
                              className="layer-row-gear"
                              aria-label="Visualization parameters"
                              onClick={() => openPopoverFor(l.layer_id, "gear")}
                            >
                              ⚙
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
                              ✕
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
                              ✕
                            </button>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                ) : null}
              </div>
            ))}
        </div>
      ) : null}

      {openLayer && openPopover.kind === "gear" ? (
        <div className="symbology-popover">
          <div className="symbology-popover-header">
            <span>
              {openLayer.type}
              {openLayer.date_processed ? ` (${openLayer.date_processed})` : ""}
            </span>
            <span className="symbology-popover-subtitle">visualization parameters</span>
          </div>

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
            <dt>Source</dt>
            <dd>{openLayer.source ?? "—"}</dd>
            <dt>Date processed</dt>
            <dd>{formatDate(openLayer.date_processed)}</dd>
            <dt>Accuracy</dt>
            <dd>{openLayer.accuracy_score != null ? `${formatNumber(openLayer.accuracy_score)}%` : "—"}</dd>
            {openLayer.needs_reingestion ? (
              <>
                <dt>Padding mask</dt>
                <dd>⚠ Needs re-upload (predates the padding-mask fix)</dd>
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
    </div>
  );
}
