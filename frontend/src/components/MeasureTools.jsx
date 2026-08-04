import { useState } from "react";
import {
  MEASURE_UNITS,
  formatMeasurement,
  readStoredUnit,
  storeUnit,
} from "../lib/measure.js";

/**
 * The in-progress measurement readout: live distance/area result, the unit it's
 * shown in, + Clear. Mode selection itself lives in the top toolbar's Measure
 * menu (Wave: map UI redesign) - this floats over the map only while a
 * measurement is actually in progress, showing what that toolbar selection is
 * doing.
 *
 * `result` is the raw geodesic value from lib/measure.js in its base unit
 * (meters for distance, hectares for area), or null before there are enough
 * points. Formatting happens here because the chosen unit lives here.
 */
export default function MeasureTools({ mode, onClear, result: baseValue, pointCount }) {
  const [distanceUnit, setDistanceUnit] = useState(() => readStoredUnit("distance"));
  const [areaUnit, setAreaUnit] = useState(() => readStoredUnit("area"));

  if (mode === "inspect") return null;

  const isArea = mode === "area";
  const unit = isArea ? areaUnit : distanceUnit;

  function changeUnit(next) {
    storeUnit(mode, next);
    (isArea ? setAreaUnit : setDistanceUnit)(next);
  }

  return (
    <div className="measure-tools">
      <span className="measure-result">
        {formatMeasurement(mode, baseValue, unit) ??
          (isArea ? "Click the map to start a shape (3+ points)" : "Click the map to start a line")}
      </span>
      <select
        className="map-toolbar-select"
        aria-label={isArea ? "Area units" : "Distance units"}
        value={unit}
        onChange={(e) => changeUnit(e.target.value)}
      >
        {Object.entries(MEASURE_UNITS[mode]).map(([key, { label }]) => (
          <option key={key} value={key}>
            {label}
          </option>
        ))}
      </select>
      {/* Always mounted (just disabled at 0 points), not conditionally
       * rendered: this row sits directly above the map, and the very
       * first vertex click would otherwise make this button appear,
       * changing the toolbar's wrapped height and shifting the map a
       * few pixels out from under the user's next click mid-shape - a
       * real, measured bug (confirmed via automated verification: the
       * map's on-page position moved 12px after the first click before
       * this fix), not just a cosmetic nit. */}
      <button type="button" className="ghost-button" disabled={pointCount === 0} onClick={onClear}>
        Clear
      </button>
    </div>
  );
}
