/**
 * Interactive replacement for the raw JSON "Class legend" textarea: one row
 * per class (pixel value + dropdown/custom name + color swatch + remove),
 * plus "+ Add class". Colors picked here ARE the class's real, persisted
 * color - app/services/ingestion/raster.py's `Legend` type already accepts a
 * {"label", "color"} dict per entry (used today by the post-upload Symbology
 * panel), so this is a new entry point onto existing storage, not a schema
 * change.
 */
import { useState } from "react";
import { Trash2 } from "lucide-react";
import { apiFetch } from "../config.js";
import { classColor } from "../lib/colors.js";

export const COMMON_CLASSES = [
  "Forest", "Water", "Cropland/Agriculture", "Urban/Built-up", "Grassland",
  "Barren/Bare soil", "Wetland", "Snow/Ice", "Shrubland", "Mangrove", "Unclassified",
];

export const CUSTOM = "__custom__";

// Real, distinguishable QUALITATIVE (unordered-categorical) palettes - no
// sequential/gradient ramps, since these classes have no inherent ordering.
const RAMPS = {
  set2: ["#66c2a5", "#fc8d62", "#8da0cb", "#e78ac3", "#a6d854", "#ffd92f", "#e5c494", "#b3b3b3"],
  dark2: ["#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02", "#a6761d", "#666666"],
  paired: [
    "#a6cee3", "#1f78b4", "#b2df8a", "#33a02c", "#fb9a99", "#e31a1c",
    "#fdbf6f", "#ff7f00", "#cab2d6", "#6a3d9a", "#ffff99", "#b15928",
  ],
  lulc9: [
    "#228b22", "#50aa2a", "#ee82ee", "#ffff00", "#ffd700",
    "#7fff00", "#4682b4", "#b22222", "#d2b48c",
  ],
};

const RAMP_OPTIONS = [
  { key: "", label: "Color ramp…" },
  { key: "random", label: "Random colors" },
  { key: "set2", label: "Qualitative — Set2" },
  { key: "dark2", label: "Qualitative — Dark2" },
  { key: "paired", label: "Qualitative — Paired" },
  { key: "lulc9", label: "LULC (9-class)" },
];

function hslToHex(h, s, l) {
  s /= 100;
  l /= 100;
  const k = (n) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  const toHex = (x) => Math.round(255 * x).toString(16).padStart(2, "0");
  return `#${toHex(f(0))}${toHex(f(8))}${toHex(f(4))}`;
}

// Golden-angle hue steps guarantee N distinct hues in one call, unlike drawing
// Math.random() per row, which can land two classes on near-identical hues.
function randomDistinctColors(n) {
  const start = Math.random() * 360;
  const GOLDEN_ANGLE = 137.508;
  return Array.from({ length: n }, (_, i) => hslToHex((start + i * GOLDEN_ANGLE) % 360, 65, 55));
}

function colorsForRamp(rampKey, n) {
  if (!rampKey || n === 0) return null;
  if (rampKey === "random") return randomDistinctColors(n);
  const palette = RAMPS[rampKey];
  return Array.from({ length: n }, (_, i) => palette[i % palette.length]);
}

/** Re-assigns a color to every row, in order, from the given ramp. Always a
 * full reassignment (not just filling gaps) so behavior stays simple and
 * predictable rather than sticky per class. */
function applyRamp(rampKey, targetRows) {
  const colors = colorsForRamp(rampKey, targetRows.length);
  if (!colors) return targetRows;
  return targetRows.map((row, i) => ({ ...row, color: colors[i], colorTouched: true }));
}

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

/** The inverse of `buildLegend` - turns a persisted `class_legend` (from an
 * already-ingested layer) back into editable rows, for the post-upload
 * "Edit classes" flow (Wave: editable class legend). Every entry becomes a
 * custom row (there's no reliable way to tell a persisted label was
 * originally picked from COMMON_CLASSES vs typed by hand) except when the
 * label happens to match one exactly - then the dropdown shows that
 * selection instead of "+ Add new class…", a cosmetic nicety only.
 * `colorTouched: true` throughout - the persisted color is real and
 * intentional, so renaming a row's text must not silently recolor it. */
export function rowsFromLegend(legend) {
  return Object.entries(legend || {})
    .sort((a, b) => parseInt(a[0], 10) - parseInt(b[0], 10))
    .map(([pixelValue, entry]) => {
      const label = (typeof entry === "string" ? entry : entry?.label) ?? "";
      const color =
        typeof entry === "object" && entry?.color ? entry.color : classColor(label || "custom");
      const isCommon = COMMON_CLASSES.includes(label) && label !== "Unclassified";
      return {
        id: crypto.randomUUID(),
        pixelValue: String(pixelValue),
        classSelect: isCommon ? label : CUSTOM,
        customName: isCommon ? "" : label,
        color,
        colorTouched: true,
      };
    });
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

/** A row for a value the "Scan file" action discovered - a blank, generic
 * starting label (e.g. "Class 0") the user renames to the real land-cover
 * name, same as QGIS's own scan-then-label workflow. Represented as a
 * regular custom row (not a new row "kind") so it behaves identically to a
 * manually-added one: same color picker, same remove button, same
 * `buildLegend` handling. */
function scannedRow(pixelValue) {
  const customName = `Class ${pixelValue}`;
  return {
    id: crypto.randomUUID(),
    pixelValue: String(pixelValue),
    classSelect: CUSTOM,
    customName,
    color: classColor(customName),
    colorTouched: false,
  };
}

/** Merges a "Scan file" result into the current rows: a scanned value that
 * already has a row (however it got there) is left alone - scanning never
 * clobbers a label/color the user already assigned - and every new value
 * becomes its own row. Always a full sort by pixel value afterward (not
 * just appending), matching QGIS's Classify, which always lists classes in
 * ascending value order. */
function mergeScannedValues(rows, scannedValues) {
  const existing = new Set(
    rows.map((r) => parseInt(r.pixelValue, 10)).filter((n) => !Number.isNaN(n))
  );
  const added = scannedValues.filter((v) => !existing.has(v)).map(scannedRow);
  return [...rows, ...added].sort(
    (a, b) => parseInt(a.pixelValue, 10) - parseInt(b.pixelValue, 10)
  );
}

export default function ClassLegendBuilder({ rows, onChange, file }) {
  const [ramp, setRamp] = useState("");
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState(null);

  function updateRow(id, patch) {
    onChange(rows.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }

  function handleRampChange(value) {
    setRamp(value);
    onChange(applyRamp(value, rows));
  }

  async function handleScan() {
    if (!file) return;
    setScanning(true);
    setScanError(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const result = await apiFetch("/datasets/scan-values", { method: "POST", body });
      const merged = mergeScannedValues(rows, result.values);
      onChange(applyRamp(ramp, merged));
    } catch (err) {
      setScanError(err.message ?? "Could not scan this file.");
    } finally {
      setScanning(false);
    }
  }

  function handleAdd() {
    onChange(applyRamp(ramp, [...rows, newRow(rows)]));
  }

  function handleRemove(id) {
    onChange(applyRamp(ramp, rows.filter((r) => r.id !== id)));
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
      <div className="legend-toolbar">
        <button
          type="button"
          className="ghost-button"
          onClick={handleScan}
          disabled={!file || scanning}
          title="Read the actual distinct pixel values in this file's band and add a row for each"
        >
          {scanning ? "Scanning…" : "Scan file"}
        </button>
        <select
          className="field-input legend-ramp-select"
          value={ramp}
          onChange={(e) => handleRampChange(e.target.value)}
          aria-label="Color ramp"
        >
          {RAMP_OPTIONS.map((opt) => (
            <option key={opt.key} value={opt.key}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>
      {scanError ? <span className="field-hint field-hint-error">{scanError}</span> : null}
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
              onClick={() => handleRemove(row.id)}
              aria-label="Remove class"
              title="Remove class"
            >
              <Trash2 size={16} strokeWidth={2} className="icon" />
            </button>
          </div>
        );
      })}
      <button type="button" className="ghost-button" onClick={handleAdd}>
        + Add class
      </button>
    </div>
  );
}
