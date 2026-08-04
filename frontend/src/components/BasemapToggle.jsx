import { BASEMAPS } from "../lib/basemap.js";

/**
 * Basemap picker docked in the top toolbar - still the same plain <select> it
 * became in the map UI redesign, now driven off lib/basemap.js's BASEMAPS list
 * instead of two hardcoded options (Wave: map toolbar capabilities). A <select>
 * IS the gallery at five entries: it's already a native dropdown with keyboard
 * support, so no thumbnail-preview UI was built for it.
 */
export default function BasemapToggle({ mode, onChange }) {
  return (
    <select
      className="map-toolbar-select"
      value={mode}
      onChange={(e) => onChange(e.target.value)}
      aria-label="Basemap"
    >
      {BASEMAPS.map((b) => (
        <option key={b.key} value={b.key}>
          {b.label}
        </option>
      ))}
    </select>
  );
}
