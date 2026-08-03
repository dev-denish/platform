/**
 * Interactive replacement for the raw JSON "Class legend" textarea: one row
 * per class (pixel value + dropdown/custom name + color swatch + remove),
 * plus "+ Add class". Colors picked here ARE the class's real, persisted
 * color - app/services/ingestion/raster.py's `Legend` type already accepts a
 * {"label", "color"} dict per entry (used today by the post-upload Symbology
 * panel), so this is a new entry point onto existing storage, not a schema
 * change.
 */
import { classColor } from "../lib/colors.js";

export const COMMON_CLASSES = [
  "Forest", "Water", "Cropland/Agriculture", "Urban/Built-up", "Grassland",
  "Barren/Bare soil", "Wetland", "Snow/Ice", "Shrubland", "Mangrove", "Unclassified",
];

const CUSTOM = "__custom__";

function effectiveName(row) {
  return row.classSelect === CUSTOM ? row.customName : row.classSelect;
}

/** Same rule as the backend's _has_real_label (app/api/v1/datasets.py) -
 * blank/whitespace/"none" is never a real class, whether typed here or sent
 * directly to the API. */
function isRealName(name) {
  const n = (name || "").trim();
  return !!n && n.toLowerCase() !== "none";
}

/** {pixel_value: {label, color}} for every row with a real name and an
 * integer pixel value. A row failing either check is silently excluded -
 * same as if the person had removed it - not sent as a bogus class. */
export function buildLegend(rows) {
  const legend = {};
  for (const row of rows) {
    const name = effectiveName(row).trim();
    if (!isRealName(name)) continue;
    const pixelValue = String(row.pixelValue).trim();
    if (!/^-?\d+$/.test(pixelValue)) continue;
    legend[pixelValue] = { label: name, color: row.color };
  }
  return legend;
}

function nextPixelValue(rows) {
  const used = rows.map((r) => parseInt(r.pixelValue, 10)).filter((n) => !Number.isNaN(n));
  return used.length ? Math.max(...used) + 1 : 1;
}

function defaultClassSelect(rows) {
  const used = new Set(rows.map((r) => r.classSelect));
  return COMMON_CLASSES.find((c) => !used.has(c)) ?? COMMON_CLASSES[0];
}

function newRow(rows) {
  const classSelect = defaultClassSelect(rows);
  return {
    id: crypto.randomUUID(),
    pixelValue: String(nextPixelValue(rows)),
    classSelect,
    customName: "",
    color: classColor(classSelect),
    colorTouched: false,
  };
}

export default function ClassLegendBuilder({ rows, onChange }) {
  function updateRow(id, patch) {
    onChange(rows.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }

  function handleClassSelect(row, value) {
    const patch = { classSelect: value };
    if (value !== CUSTOM && !row.colorTouched) patch.color = classColor(value);
    updateRow(row.id, patch);
  }

  function handleCustomName(row, value) {
    const patch = { customName: value };
    if (!row.colorTouched) patch.color = classColor(value || "custom");
    updateRow(row.id, patch);
  }

  return (
    <div className="legend-builder">
      {rows.map((row) => {
        const isCustom = row.classSelect === CUSTOM;
        const showWarning = isCustom && !isRealName(row.customName);
        return (
          <div className="legend-row" key={row.id}>
            <input
              type="number"
              className="field-input legend-row-pixel"
              value={row.pixelValue}
              onChange={(e) => updateRow(row.id, { pixelValue: e.target.value })}
              aria-label="Pixel value"
            />
            <select
              className="field-input legend-row-select"
              value={row.classSelect}
              onChange={(e) => handleClassSelect(row, e.target.value)}
            >
              {COMMON_CLASSES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
              <option value={CUSTOM}>+ Add new class…</option>
            </select>
            {isCustom ? (
              <span className="legend-row-custom">
                <input
                  type="text"
                  className="field-input"
                  value={row.customName}
                  onChange={(e) => handleCustomName(row, e.target.value)}
                  placeholder="Custom class name"
                />
                {showWarning ? <span className="field-hint">Won't be saved until named.</span> : null}
              </span>
            ) : null}
            <input
              type="color"
              className="legend-row-color"
              value={row.color}
              onChange={(e) => updateRow(row.id, { color: e.target.value, colorTouched: true })}
              aria-label="Class color"
              title="Class color"
            />
            <button
              type="button"
              className="ghost-button legend-row-remove"
              onClick={() => onChange(rows.filter((r) => r.id !== row.id))}
              aria-label="Remove class"
              title="Remove class"
            >
              🗑
            </button>
          </div>
        );
      })}
      <button type="button" className="ghost-button" onClick={() => onChange([...rows, newRow(rows)])}>
        + Add class
      </button>
    </div>
  );
}
