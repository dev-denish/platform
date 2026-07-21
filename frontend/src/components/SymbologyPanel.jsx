/**
 * Per-layer symbology controls: band-to-channel assignment + stretch for raw
 * imagery, or per-class color override for a classified (class_legend)
 * layer, with a mode toggle between the two when a layer has both available.
 * Every control just updates local state; ProjectMap.jsx turns that state
 * into `bands`/`stretch`/`colors` query params on the tile URL - no
 * re-ingestion, the backend renders each combination live (see tiles.py).
 */
import { legendEntryLabel, legendEntryColor } from "../lib/symbology.js";

const CHANNEL_LABELS = ["R", "G", "B"];

export default function SymbologyPanel({ layer, symbology, onChange, hideTitle = false }) {
  if (!symbology) return null;
  const bandCount = layer.band_count ?? 3;
  const legendEntries = layer.class_legend ? Object.entries(layer.class_legend) : [];
  const hasLegend = legendEntries.length > 0;
  const bandOptions = Array.from({ length: bandCount }, (_, i) => i + 1);

  function update(patch) {
    onChange(layer.layer_id, { ...symbology, ...patch });
  }

  function updateBand(index, value) {
    const bands = [...symbology.bands];
    bands[index] = Number(value);
    update({ bands });
  }

  function updateColorOverride(value, hex) {
    update({ colorOverrides: { ...symbology.colorOverrides, [value]: hex } });
  }

  const showClassified = hasLegend && symbology.mode === "classified";

  return (
    <div className="symbology-panel">
      <div className="symbology-header">
        {hideTitle ? null : <span className="symbology-title">{layer.type} symbology</span>}
        {hasLegend ? (
          <div className="symbology-toggle" role="group">
            <button
              type="button"
              className={`symbology-toggle-btn${symbology.mode === "classified" ? " symbology-toggle-btn-active" : ""}`}
              onClick={() => update({ mode: "classified" })}
            >
              Classified
            </button>
            <button
              type="button"
              className={`symbology-toggle-btn${symbology.mode === "raw" ? " symbology-toggle-btn-active" : ""}`}
              onClick={() => update({ mode: "raw" })}
            >
              Raw bands
            </button>
          </div>
        ) : null}
      </div>

      {showClassified ? (
        <div className="symbology-classes">
          {legendEntries.map(([value, entry]) => (
            <label className="symbology-class-row" key={value}>
              <input
                type="color"
                value={symbology.colorOverrides[value] ?? legendEntryColor(entry)}
                onChange={(e) => updateColorOverride(value, e.target.value)}
              />
              <span>{legendEntryLabel(value, entry)}</span>
            </label>
          ))}
        </div>
      ) : (
        <div className="symbology-bands">
          {bandCount >= 3 ? (
            <div className="symbology-toggle" role="group">
              <button
                type="button"
                className={`symbology-toggle-btn${symbology.composite === "rgb" ? " symbology-toggle-btn-active" : ""}`}
                onClick={() => update({ composite: "rgb", bands: [1, 2, 3] })}
              >
                Color (R/G/B)
              </button>
              <button
                type="button"
                className={`symbology-toggle-btn${symbology.composite === "gray" ? " symbology-toggle-btn-active" : ""}`}
                onClick={() => update({ composite: "gray", bands: [symbology.bands[0] ?? 1] })}
              >
                Grayscale
              </button>
            </div>
          ) : null}

          {symbology.composite === "rgb" && bandCount >= 3 ? (
            CHANNEL_LABELS.map((channel, i) => (
              <label className="symbology-band-row" key={channel}>
                <span>{channel}</span>
                <select value={symbology.bands[i]} onChange={(e) => updateBand(i, e.target.value)}>
                  {bandOptions.map((b) => (
                    <option key={b} value={b}>
                      Band {b}
                    </option>
                  ))}
                </select>
              </label>
            ))
          ) : (
            <label className="symbology-band-row">
              <span>Band</span>
              <select
                value={symbology.bands[0]}
                onChange={(e) => update({ bands: [Number(e.target.value)] })}
              >
                {bandOptions.map((b) => (
                  <option key={b} value={b}>
                    Band {b}
                  </option>
                ))}
              </select>
            </label>
          )}

          <label className="symbology-stretch-row">
            <span>Stretch low: {symbology.stretch[0]}%</span>
            <input
              type="range"
              min="0"
              max="49"
              value={symbology.stretch[0]}
              onChange={(e) => update({ stretch: [Number(e.target.value), symbology.stretch[1]] })}
            />
          </label>
          <label className="symbology-stretch-row">
            <span>Stretch high: {symbology.stretch[1]}%</span>
            <input
              type="range"
              min="51"
              max="100"
              value={symbology.stretch[1]}
              onChange={(e) => update({ stretch: [symbology.stretch[0], Number(e.target.value)] })}
            />
          </label>
        </div>
      )}
    </div>
  );
}
