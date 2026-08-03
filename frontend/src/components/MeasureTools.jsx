/**
 * The in-progress measurement readout: live distance/area result + Clear.
 * Mode selection itself now lives in the top toolbar's Measure menu (Wave:
 * map UI redesign) - this floats over the map only while a measurement is
 * actually in progress, showing what that toolbar selection is doing.
 */
export default function MeasureTools({ mode, onClear, result, pointCount }) {
  if (mode === "inspect") return null;
  return (
    <div className="measure-tools">
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
    </div>
  );
}
