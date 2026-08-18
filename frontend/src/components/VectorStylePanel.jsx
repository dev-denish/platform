/**
 * Per-layer vector style controls (Wave: Map Toolbar Enhancement v2, Tier 4)
 * - the vector-layer counterpart to SymbologyPanel.jsx (raster-only: bands/
 * stretch/classified colors). Stroke color, stroke weight, and fill opacity
 * are the three values renderVectorLayer already hardcoded per dataset type
 * (DATASET_TYPE_COLORS) before this feature existed - this just exposes them
 * as controls instead of leaving them fixed. Layer opacity itself is the
 * existing generic opacity slider every layer kind already gets (see
 * LayersPanel's gear popover) - not duplicated here.
 */
export default function VectorStylePanel({ layer, style, onChange, hideTitle = false }) {
  if (!style) return null;

  function update(patch) {
    onChange(layer.layer_id, { ...style, ...patch });
  }

  return (
    <div className="symbology-panel">
      {hideTitle ? null : (
        <div className="symbology-header">
          <span className="symbology-title">{layer.type} style</span>
        </div>
      )}
      <label className="vector-style-row">
        <span>Color</span>
        <input type="color" value={style.color} onChange={(e) => update({ color: e.target.value })} />
      </label>
      <label className="vector-style-row">
        <span>Line weight: {style.weight}px</span>
        <input
          type="range"
          min="1"
          max="8"
          step="1"
          value={style.weight}
          onChange={(e) => update({ weight: Number(e.target.value) })}
        />
      </label>
      <label className="vector-style-row">
        <span>Fill opacity: {style.fillOpacity.toFixed(2)}</span>
        <input
          type="range"
          min="0"
          max="1"
          step="0.05"
          value={style.fillOpacity}
          onChange={(e) => update({ fillOpacity: Number(e.target.value) })}
        />
      </label>
    </div>
  );
}
