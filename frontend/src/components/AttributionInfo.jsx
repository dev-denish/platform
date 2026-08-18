import { useEffect, useRef, useState } from "react";
import { basemapSourceInfo } from "../lib/basemap.js";

/**
 * Attribution pill + interactive "i" info toggle (Wave: Map Toolbar
 * Enhancement v2 fix) - replaces Leaflet's own default attribution control
 * (turned off via <MapContainer attributionControl={false}>, see
 * ProjectMap.jsx) with one that shows the exact same text but adds a real,
 * clickable info panel: the current basemap source's terms of use, a
 * report-a-map-error link, and this app's own keyboard shortcuts - matching
 * what real Google Maps shows behind its own (i) button. A plain absolutely-
 * positioned sibling (not a Leaflet control) - it doesn't need to track map
 * position/zoom, just the current basemap, so there's no reason to route it
 * through Leaflet's control/portal machinery the way CoordinateBadge used to.
 *
 * `attributionHtml` is Leaflet-style attribution markup (may contain a real
 * `&copy;`/`<a>` - see lib/basemap.js's own ATTRIBUTION constants), so it's
 * rendered as HTML, not escaped text - same trust boundary Leaflet's own
 * attribution control already had (this app's own hardcoded basemap list,
 * never user input).
 */
export default function AttributionInfo({ attributionHtml, source }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const info = basemapSourceInfo(source);

  useEffect(() => {
    if (!open) return undefined;
    function handlePointerDown(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    }
    function handleKeyDown(e) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  // A basemap switch mid-open would leave the panel showing the OLD source's
  // links under the NEW attribution text - close it instead, same as any
  // other transient popover reacting to the ground shifting under it.
  useEffect(() => {
    setOpen(false);
  }, [source]);

  return (
    <div className="attribution-info" ref={rootRef}>
      <span className="attribution-info-text" dangerouslySetInnerHTML={{ __html: attributionHtml ?? "" }} />
      <button
        type="button"
        className="attribution-info-toggle"
        aria-label={open ? "Hide map data info" : "Show map data info"}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        i
      </button>
      {open ? (
        <div className="attribution-info-panel" role="dialog" aria-label="Map data info">
          <a href={info.termsUrl} target="_blank" rel="noopener noreferrer">
            Terms of use
          </a>
          <a href={info.reportUrl} target="_blank" rel="noopener noreferrer">
            Report a map error
          </a>
          <div className="attribution-info-shortcuts">
            <div className="attribution-info-shortcuts-title">Keyboard shortcuts</div>
            <div>Arrow keys — pan</div>
            <div>+ / − — zoom in/out</div>
            <div>Esc — close menu/dialog</div>
            <div>Click the map once — enable scroll-to-zoom</div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
