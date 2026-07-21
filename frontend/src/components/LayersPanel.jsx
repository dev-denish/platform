import { useMemo, useState } from "react";
import SymbologyPanel from "./SymbologyPanel.jsx";
import { datedLayerGroups } from "../lib/timeline.js";

/**
 * The GEE Code Editor's "Layers" panel, consolidated from three previously-
 * separate controls (checkboxes, the always-on inline SymbologyPanel, and
 * per-layer opacity) into one collapsible top-right panel - one row per REAL
 * layer, independently checked/visible.
 *
 * Phase 3 Wave G: Time/Compare (Wave C/E) is gone - every layer, dated or
 * not, is now a plain independent visibility toggle reading/writing
 * `layerState[id].visible` directly, exactly like undated layers always
 * worked. There is no more "pick one active date" or "pick two to compare"
 * concept gating what's checked; all real layers default to visible (see
 * ProjectMap's `initLayerState`) and stay that way until the user
 * unchecks one - genuine multi-select, not a radio button in disguise.
 *
 * Rows are still ordered chronologically (oldest first, undated last) via
 * lib/timeline.js's datedLayerGroups - the date-grouping/sorting logic Wave
 * C built stays meaningfully alive here even though the exclusive
 * single-date/compare UI it originally served is gone.
 *
 * The gear icon opens a popover with the EXISTING Symbology controls
 * (SymbologyPanel, unchanged internally) plus an Opacity slider pulled in
 * from the old LayerControls state - both apply live; Apply/Close both just
 * close the popover (there is no buffered/revertable state to commit or
 * discard, so a fake distinction between them would be worse than an honest
 * single behavior). Each layer's popover is independent, so with several
 * layers visible at once, adjusting one's bands/stretch/colors never
 * touches another's.
 */
export default function LayersPanel({
  layers,
  layerState,
  symbologyState,
  onToggleVisibility,
  onOpacityChange,
  onSymbologyChange,
}) {
  const [expanded, setExpanded] = useState(true);
  const [openGearId, setOpenGearId] = useState(null);

  const orderedLayers = useMemo(() => {
    const dated = datedLayerGroups(layers).map((g) => g.layer);
    const datedIds = new Set(dated.map((l) => l.layer_id));
    const undated = layers.filter((l) => !datedIds.has(l.layer_id));
    return [...dated, ...undated];
  }, [layers]);

  if (!layers || layers.length === 0) return null;

  function isChecked(layer) {
    return (layerState[layer.layer_id] ?? { visible: true }).visible;
  }

  function handleRowToggle(layer) {
    onToggleVisibility(layer.layer_id, !isChecked(layer));
  }

  const openGearLayer = layers.find((l) => l.layer_id === openGearId) ?? null;

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
      {expanded ? (
        <ul className="layers-panel-list">
          {orderedLayers.map((l) => (
            <li className="layer-row" key={l.layer_id}>
              <input
                type="checkbox"
                className="layer-row-checkbox"
                checked={isChecked(l)}
                onChange={() => handleRowToggle(l)}
                aria-label={`Toggle ${l.type} ${l.date_processed ?? "undated"}`}
              />
              <span className="layer-row-label">
                {l.type}
                {l.date_processed ? ` · ${l.date_processed}` : ""}
              </span>
              {l.tile_url_template ? (
                <button
                  type="button"
                  className="layer-row-gear"
                  aria-label="Visualization parameters"
                  onClick={() => setOpenGearId(openGearId === l.layer_id ? null : l.layer_id)}
                >
                  ⚙
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {openGearLayer ? (
        <div className="symbology-popover">
          <div className="symbology-popover-header">
            <span>
              {openGearLayer.type}
              {openGearLayer.date_processed ? ` (${openGearLayer.date_processed})` : ""}
            </span>
            <span className="symbology-popover-subtitle">visualization parameters</span>
          </div>

          <SymbologyPanel
            layer={openGearLayer}
            symbology={symbologyState[openGearLayer.layer_id]}
            onChange={onSymbologyChange}
            hideTitle
          />

          <label className="symbology-popover-opacity">
            <span>Opacity</span>
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={(layerState[openGearLayer.layer_id] ?? { opacity: 1 }).opacity}
              onChange={(e) => onOpacityChange(openGearLayer.layer_id, Number(e.target.value))}
            />
            <span className="symbology-popover-opacity-value">
              {(layerState[openGearLayer.layer_id] ?? { opacity: 1 }).opacity.toFixed(2)}
            </span>
          </label>

          <div className="symbology-popover-footer">
            <button type="button" className="ghost-button" onClick={() => setOpenGearId(null)}>
              Close
            </button>
            <button type="button" className="primary-button" onClick={() => setOpenGearId(null)}>
              Apply
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
