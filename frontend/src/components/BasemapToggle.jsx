/**
 * GEE-style "Map"/"Satellite" basemap picker, now docked in the top toolbar
 * (Wave: map UI redesign) as a plain <select> instead of a pill toggle -
 * same two free tile sources as before (Esri World Imagery / Carto, see
 * ProjectMap.jsx), same props, just restyled to match the toolbar's other
 * controls.
 */
export default function BasemapToggle({ mode, onChange }) {
  return (
    <select
      className="map-toolbar-select"
      value={mode}
      onChange={(e) => onChange(e.target.value)}
      aria-label="Basemap"
    >
      <option value="satellite">Satellite</option>
      <option value="map">Map</option>
    </select>
  );
}
