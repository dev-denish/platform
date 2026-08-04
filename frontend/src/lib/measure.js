import { area as turfArea } from "@turf/area";
import { formatNumber } from "./format.js";

/**
 * Real-world distance/area from a list of Leaflet LatLng vertices - the two
 * measure tools' actual math, kept out of ProjectMap.jsx.
 *
 * Distance: Leaflet's own `LatLng.distanceTo` (already installed, no new
 * dependency) - a real haversine great-circle calculation in meters, not
 * naive degree math.
 *
 * Area: Leaflet has no equivalent area utility, so this reaches for
 * `@turf/area` - a real geodesic polygon-area formula (Chamberlain &
 * Duquette) operating directly on WGS84 lon/lat, not a planar shoelace over
 * raw degrees (that was literally the Phase-1 bug this platform exists to
 * avoid). One small scoped package, not the full turf bundle.
 */
export function lineDistanceMeters(latlngs) {
  let total = 0;
  for (let i = 1; i < latlngs.length; i++) {
    total += latlngs[i - 1].distanceTo(latlngs[i]);
  }
  return total;
}

export function polygonAreaHectares(latlngs) {
  if (latlngs.length < 3) return 0;
  const ring = latlngs.map((p) => [p.lng, p.lat]);
  ring.push(ring[0]); // close the ring
  const squareMeters = turfArea({ type: "Polygon", coordinates: [ring] });
  return squareMeters / 10000;
}

/**
 * Unit conversion + display on top of the two base functions above, which stay
 * the single source of geodesic truth (meters / hectares). Everything here is
 * plain multiplication - `factor` converts FROM the base unit, `minDigits` is
 * the floor on decimal places for that unit, `label` is what field teams see
 * in the dropdown.
 */
export const MEASURE_UNITS = {
  distance: {
    m: { label: "Meters", symbol: "m", factor: 1, minDigits: 1 },
    km: { label: "Kilometers", symbol: "km", factor: 0.001, minDigits: 2 },
    ft: { label: "Feet", symbol: "ft", factor: 3.280839895013123, minDigits: 0 },
    mi: { label: "Miles", symbol: "mi", factor: 0.000621371192237334, minDigits: 2 },
  },
  area: {
    ha: { label: "Hectares", symbol: "ha", factor: 1, minDigits: 2 },
    km2: { label: "Square kilometers", symbol: "km²", factor: 0.01, minDigits: 2 },
    ac: { label: "Acres", symbol: "ac", factor: 2.471053814671653, minDigits: 2 },
    mi2: { label: "Square miles", symbol: "mi²", factor: 0.003861021585424458, minDigits: 2 },
  },
};

export const DEFAULT_UNIT = { distance: "m", area: "ha" };

/**
 * Decimal places for `value`: ~4 significant figures, never fewer than the
 * unit's own floor, capped at 6. Without this, a 0.3 ha plot shown in square
 * miles at a fixed 2 decimals reads "0.00 mi²"; with it, "0.001158 mi²".
 */
function decimalsFor(value, minDigits) {
  if (!value) return minDigits;
  const magnitude = Math.floor(Math.log10(Math.abs(value)));
  return Math.min(6, Math.max(minDigits, 3 - magnitude));
}

/** meters (distance) or hectares (area) -> "1,250.4 m" / "0.31 ac" */
export function formatMeasurement(mode, baseValue, unitKey) {
  const units = MEASURE_UNITS[mode];
  if (!units || baseValue == null) return null;
  const unit = units[unitKey] ?? units[DEFAULT_UNIT[mode]];
  const value = baseValue * unit.factor;
  return `${formatNumber(value, decimalsFor(value, unit.minDigits))} ${unit.symbol}`;
}

// Last-used unit per mode. A UI preference, so localStorage (survives reloads)
// rather than the sessionStorage useCollapse uses for transient panel state.
const unitStorageKey = (mode) => `dmrv.measureUnit.${mode}`;

export function readStoredUnit(mode) {
  const stored = localStorage.getItem(unitStorageKey(mode));
  return stored && MEASURE_UNITS[mode][stored] ? stored : DEFAULT_UNIT[mode];
}

export function storeUnit(mode, unitKey) {
  localStorage.setItem(unitStorageKey(mode), unitKey);
}

/**
 * "1:8,407"-style ratio scale for the toolbar readout - the standard
 * web-Mercator formula (same one QGIS/ArcGIS web viewers use): ground
 * resolution at this zoom/latitude, divided by the OGC-standard assumed
 * screen pixel size (0.28mm), rounded to a whole-number denominator.
 */
export function scaleRatioLabel(zoom, lat) {
  const metersPerPixel = (156543.03392804097 * Math.cos((lat * Math.PI) / 180)) / Math.pow(2, zoom);
  const denominator = Math.round(metersPerPixel / 0.00028);
  return `1:${denominator.toLocaleString("en-US")}`;
}
