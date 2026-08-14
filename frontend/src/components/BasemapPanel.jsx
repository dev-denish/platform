import { useState } from "react";
import { Check } from "lucide-react";
import { BASEMAPS, BASEMAP_SOURCES, basemapFor } from "../lib/basemap.js";

/**
 * Richer basemap picker (Wave: Map Toolbar Enhancement v2, Tier 2) - a
 * floating panel with source tabs + a thumbnail-style option list, opened
 * from its own toolbar toggle (see ProjectMap.jsx's map-floating-toggle-row).
 * Deliberately NOT a replacement for BasemapToggle's plain <select> inside
 * MapToolbar - that one stays exactly as-is (existing e2e tests drive it via
 * `getByLabel("Basemap")` + `selectOption`), this is an additional, more
 * discoverable entry point onto the exact same `basemapMode` state, so
 * picking a basemap here or there always agrees.
 *
 * Every source tab here actually works - unlike the mockup's "Google only,
 * others disabled" placeholder plan, Esri/Carto/OSM were already fully wired
 * in lib/basemap.js before this wave (see BASEMAPS) - only Google is new.
 */
export default function BasemapPanel({ mode, onChange }) {
  const [activeTab, setActiveTab] = useState(() => basemapFor(mode).source);
  const options = BASEMAPS.filter((b) => b.source === activeTab);

  return (
    <div className="basemap-panel">
      <div className="basemap-panel-header">
        <span>Basemap</span>
      </div>
      <div className="basemap-panel-body">
        {/* "Map source", not "Basemap source" - avoids colliding with the
         * existing e2e suite's getByLabel("Basemap") (a substring match
         * against BasemapToggle's own <select>) - see ProjectMap.jsx's
         * matching comment on this panel's own toggle button. */}
        <div className="basemap-panel-tabs" role="tablist" aria-label="Map source">
          {BASEMAP_SOURCES.map((s) => (
            <button
              key={s.key}
              type="button"
              role="tab"
              aria-selected={activeTab === s.key}
              className={`basemap-panel-tab${activeTab === s.key ? " basemap-panel-tab-active" : ""}`}
              onClick={() => setActiveTab(s.key)}
            >
              {s.label}
            </button>
          ))}
        </div>
        <ul className="basemap-panel-options">
          {options.map((b) => (
            <li key={b.key}>
              <button
                type="button"
                className={`basemap-panel-option${mode === b.key ? " basemap-panel-option-selected" : ""}`}
                onClick={() => onChange(b.key)}
              >
                <span className={`basemap-panel-thumb basemap-panel-thumb-${b.key}`} aria-hidden="true" />
                <span className="basemap-panel-option-text">
                  <span className="basemap-panel-option-name">{b.label}</span>
                  <span className="basemap-panel-option-sub">{b.source}</span>
                </span>
                {mode === b.key ? (
                  <Check size={14} strokeWidth={2} className="icon basemap-panel-check" aria-hidden="true" />
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
