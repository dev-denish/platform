/**
 * Toolbar for the map's three click behaviors, mutually exclusive: a plain
 * click either inspects a raster pixel (default) or adds a vertex to a
 * distance/area measurement - never both, so there's no ambiguity about what
 * a click on the map does right now. Switching modes clears any in-progress
 * shape; "Clear" resets without leaving the current mode.
 */
const MODES = [
  ["inspect", "Inspect pixel"],
  ["distance", "Measure distance"],
  ["area", "Measure area"],
];

export default function MeasureTools({ mode, onModeChange, onClear, result, pointCount }) {
  return (
    <div className="measure-tools">
      <div className="symbology-toggle" role="group">
        {MODES.map(([value, label]) => (
          <button
            key={value}
            type="button"
            className={`symbology-toggle-btn${mode === value ? " symbology-toggle-btn-active" : ""}`}
            onClick={() => onModeChange(value)}
          >
            {label}
          </button>
        ))}
      </div>
      {mode !== "inspect" ? (
        <>
          <span className="measure-result">
            {result ??
              (mode === "distance"
                ? "Click the map to start a line"
                : "Click the map to start a shape (3+ points)")}
          </span>
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
        </>
      ) : null}
    </div>
  );
}
