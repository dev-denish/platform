import { useEffect, useRef, useState } from "react";

/**
 * Live Lat/Lon/Zoom/Scale readout (Wave: Map Toolbar Enhancement v2 fix) -
 * a real footer strip attached to the bottom edge of the map card, NOT a
 * floating overlay sitting on top of the imagery. This replaces the old
 * CoordinateBadge, which was a genuine Leaflet "bottomright" control (via
 * L.control + a React portal) sharing the scale bar's own corner - that
 * approach put it INSIDE the Leaflet container by construction, which is
 * exactly the "covering the map" complaint this fix addresses. This
 * component has no Leaflet dependency at all: `lat`/`lon`/`zoom`/
 * `scaleLabel` are plain props already computed from ProjectMap's own
 * `mapView` state, and it renders as a normal sibling AFTER
 * `.map-canvas-wrap`, inside `.map-frame` (see ProjectMap.jsx) - part of the
 * map component's own footer, never over the tiles.
 *
 * Still the click-to-copy button it always was - `.map-coord-badge`/
 * `.map-toolbar-copy`/`.map-toolbar-copied` class names are unchanged (only
 * restyled as a full-width strip, see index.css) specifically so the
 * existing e2e suite's `.map-coord-badge` locator keeps working unmodified.
 */
export default function MapStatusBar({ lat, lon, zoom, scaleLabel }) {
  const [copied, setCopied] = useState(false);
  const copyTimerRef = useRef(null);

  useEffect(() => () => clearTimeout(copyTimerRef.current), []);

  async function copyCoords() {
    if (lat == null || lon == null) return;
    try {
      await navigator.clipboard.writeText(`${lat.toFixed(5)}, ${lon.toFixed(5)}`);
      setCopied(true);
      // Restart rather than stack, so a second click doesn't have the first
      // click's timer cut its own confirmation short.
      clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard permission denied / insecure context - nothing useful to say
      // in a one-line readout, and the coordinates are still visible to select.
    }
  }

  return (
    <button
      type="button"
      className="map-coord-badge map-toolbar-copy"
      onClick={copyCoords}
      disabled={lat == null || lon == null}
      title="Copy these coordinates"
    >
      {lat != null && lon != null ? (
        <>
          Lat: {lat.toFixed(5)}° Lon: {lon.toFixed(5)}°
        </>
      ) : (
        "Lat: — Lon: —"
      )}
      {copied ? <span className="map-toolbar-copied"> Copied!</span> : null}
      {" | "}Zoom: {zoom ?? "—"}
      {" | "}Scale: {scaleLabel ?? "—"}
    </button>
  );
}
